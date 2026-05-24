# -*- coding: utf-8 -*-

"""
Real-time proxy health scoring and rotation strategy.

This module provides the ProxyHealthScorer class that monitors proxy performance
metrics (success/failure rates, response times, block detection patterns) and
maintains health scores (0-100) for each proxy. It implements intelligent rotation
strategies that prefer healthier proxies and gradually phase out failing ones.
"""

import datetime
import logging
import math
import random
from typing import List, Optional, Dict, Tuple
from GoogleScraper import database

logger = logging.getLogger(__name__)


class ProxyHealthScorer:
    """Tracks and scores proxy health based on real-time performance metrics.
    
    Maintains health scores for proxies based on recent success/failure rates,
    response times, and block detection. Implements time-window decay to allow
    proxies to recover after temporary blocks.
    """
    
    def __init__(self, session, config=None, time_window_hours=24):
        """Initialize the ProxyHealthScorer.
        
        Args:
            session: SQLAlchemy database session
            config: Optional configuration dictionary
            time_window_hours: Number of hours to consider for metrics (default 24)
        """
        self.session = session
        self.config = config or {}
        self.time_window_hours = time_window_hours
        self.ema_alpha = 0.3  # Exponential moving average smoothing factor
        
    def calculate_health_score(self, proxy_id: int) -> float:
        """Calculate health score for a proxy.
        
        Health score formula:
        base_score = 100 - (failures / total_requests * 50) - (blocks / total_requests * 50)
        Score is capped at 0-100.
        
        New proxies with no history get a neutral score of 50.
        
        Args:
            proxy_id: The ID of the proxy to score
            
        Returns:
            Health score from 0 to 100
        """
        try:
            # Get proxy health score record
            health_record = self.session.query(database.ProxyHealthScore).filter(
                database.ProxyHealthScore.proxy_id == proxy_id
            ).first()
            
            if not health_record:
                # New proxy with no history gets neutral score
                return 50.0
            
            # Calculate total requests from success_rate
            # Success rate is stored as a float (0.0 to 1.0)
            # We need to reconstruct the metrics from the data we have
            
            # Get the proxy to check if it exists
            proxy = self.session.query(database.Proxy).filter(
                database.Proxy.id == proxy_id
            ).first()
            
            if not proxy:
                return 50.0
            
            # If no metrics recorded yet, return neutral score
            if health_record.failure_count == 0 and health_record.blocked_count == 0:
                # This could be a new proxy or one with only successes
                if health_record.success_rate >= 0.99:  # All successes so far
                    # Estimate total requests - if success_rate is 1.0 and no failures, 
                    # we can't infer total requests, so return neutral for safety
                    return 50.0
            
            # Use success_rate and failure_count to estimate total requests
            total_requests = health_record.failure_count + health_record.blocked_count
            
            # If we have very few requests, use neutral score
            if total_requests == 0:
                return 50.0
            
            # Calculate estimated successes from success_rate
            # success_rate = successes / (successes + failures + blocks)
            # Let's derive from the counts we have
            estimated_total = total_requests / (1 - health_record.success_rate) if health_record.success_rate < 1.0 else total_requests
            
            # Actually, let's recalculate based on what we track
            # failures + blocks = total failures/blocks
            # success_rate tells us the proportion of successes
            
            # Better approach: use the counts directly
            # Assume success_rate represents recent performance
            failure_rate = 1.0 - health_record.success_rate
            
            # Convert the rate to a count proportion
            # If we have recorded failures/blocks, we can estimate success count
            if total_requests > 0:
                # Estimate based on failure rate
                failure_count = health_record.failure_count
                block_count = health_record.blocked_count
                
                # Total impact = (failures + blocks) / total_requests
                total_impact = (failure_count + block_count) / max(1, total_requests)
                
                # Base score: 100 - 50 * (failures/total) - 50 * (blocks/total)
                failure_impact = min(50, (failure_count / max(1, total_requests)) * 50)
                block_impact = min(50, (block_count / max(1, total_requests)) * 50)
                
                base_score = 100 - failure_impact - block_impact
            else:
                base_score = 50.0
            
            # Cap between 0 and 100
            health_score = max(0.0, min(100.0, base_score))
            
            return health_score
            
        except Exception as e:
            logger.error(f"Error calculating health score for proxy {proxy_id}: {e}")
            return 50.0
    
    def update_proxy_health(self, proxy_id: int, request_success: bool, 
                          was_blocked: bool = False, response_time_ms: float = 0):
        """Update health metrics for a proxy based on a request outcome.
        
        Args:
            proxy_id: The ID of the proxy
            request_success: True if the request succeeded
            was_blocked: True if the proxy was blocked during the request
            response_time_ms: Response time in milliseconds (for averaging)
        """
        try:
            # Get or create health score record
            health_record = self.session.query(database.ProxyHealthScore).filter(
                database.ProxyHealthScore.proxy_id == proxy_id
            ).first()
            
            if not health_record:
                health_record = database.ProxyHealthScore(proxy_id=proxy_id)
                self.session.add(health_record)
            
            # Update counters
            if not request_success:
                health_record.failure_count = (health_record.failure_count or 0) + 1
            
            if was_blocked:
                health_record.blocked_count = (health_record.blocked_count or 0) + 1
            
            # Update last used time
            health_record.last_used = datetime.datetime.utcnow()
            
            # Update success rate using exponential moving average
            total_count = (health_record.failure_count or 0) + (health_record.blocked_count or 0) + (1 if request_success else 0)
            
            if total_count > 0:
                new_success_rate = 1.0 - ((health_record.failure_count or 0) + (health_record.blocked_count or 0)) / total_count
                
                # Apply EMA smoothing
                if health_record.success_rate is None or health_record.success_rate == 1.0:
                    health_record.success_rate = new_success_rate
                else:
                    health_record.success_rate = (self.ema_alpha * new_success_rate + 
                                                 (1 - self.ema_alpha) * health_record.success_rate)
            
            self.session.commit()
            
        except Exception as e:
            logger.error(f"Error updating health for proxy {proxy_id}: {e}")
            self.session.rollback()
    
    def get_healthy_proxies(self, min_score: float = 70) -> List[Tuple[database.Proxy, float]]:
        """Get all proxies meeting minimum health score, sorted by health descending.
        
        Filters proxies based on the time window decay mechanism. Only considers
        metrics from the last N hours to allow recovery of temporarily blocked proxies.
        
        Args:
            min_score: Minimum health score required (0-100)
            
        Returns:
            List of tuples (Proxy object, health_score) sorted by score descending
        """
        try:
            # Get all proxies
            proxies = self.session.query(database.Proxy).all()
            
            healthy_proxies = []
            
            for proxy in proxies:
                score = self.calculate_health_score(proxy.id)
                
                # Apply time-window decay: check if proxy was used recently
                health_record = self.session.query(database.ProxyHealthScore).filter(
                    database.ProxyHealthScore.proxy_id == proxy.id
                ).first()
                
                if health_record and health_record.last_used:
                    # Check if last used within time window
                    time_since_use = datetime.datetime.utcnow() - health_record.last_used
                    hours_since_use = time_since_use.total_seconds() / 3600
                    
                    # Apply decay: older metrics lose weight
                    if hours_since_use > self.time_window_hours:
                        # Proxy hasn't been used in a while, boost score slightly to allow recovery
                        score = min(100, score + (min(20, hours_since_use / self.time_window_hours * 10)))
                
                if score >= min_score:
                    healthy_proxies.append((proxy, score))
            
            # Sort by score descending
            healthy_proxies.sort(key=lambda x: x[1], reverse=True)
            
            return healthy_proxies
            
        except Exception as e:
            logger.error(f"Error getting healthy proxies: {e}")
            return []
    
    def select_proxy_for_job(self, job_config: Optional[Dict] = None) -> Optional[database.Proxy]:
        """Select a proxy for a new job using weighted random selection.
        
        Uses a weighted random selection biased toward healthier proxies.
        This ensures that healthy proxies are preferred while still allowing
        lower-health proxies a chance to prove themselves.
        
        Args:
            job_config: Optional job configuration (for future use)
            
        Returns:
            Selected Proxy object, or None if no healthy proxies available
        """
        try:
            min_score = self.config.get('proxy_min_health_score', 30)
            healthy_proxies = self.get_healthy_proxies(min_score=min_score)
            
            if not healthy_proxies:
                # Fall back to all proxies if no healthy ones available
                all_proxies = self.session.query(database.Proxy).all()
                if not all_proxies:
                    return None
                
                # Return random proxy as fallback
                return random.choice(all_proxies)
            
            # Weighted random selection based on health scores
            # Weight = (score + offset)^exponent to bias toward better scores
            proxies = [p[0] for p in healthy_proxies]
            scores = [p[1] for p in healthy_proxies]
            
            # Normalize scores to weights using exponential bias
            # Higher scores get exponentially higher weights
            weights = [(score / 100.0) ** 2 for score in scores]  # Square to bias toward healthier
            
            # Normalize weights to sum to 1
            weight_sum = sum(weights)
            if weight_sum > 0:
                weights = [w / weight_sum for w in weights]
            else:
                # Equal weights if all scores are 0
                weights = [1.0 / len(proxies) for _ in proxies]
            
            # Select using weighted random choice
            selected_proxy = random.choices(proxies, weights=weights, k=1)[0]
            
            return selected_proxy
            
        except Exception as e:
            logger.error(f"Error selecting proxy for job: {e}")
            
            # Fallback: return any random proxy
            try:
                all_proxies = self.session.query(database.Proxy).all()
                return random.choice(all_proxies) if all_proxies else None
            except:
                return None
    
    def get_proxy_status(self, proxy_id: int) -> Optional[Dict]:
        """Get detailed status information for a proxy.
        
        Args:
            proxy_id: The ID of the proxy
            
        Returns:
            Dictionary with proxy health information, or None if proxy not found
        """
        try:
            health_record = self.session.query(database.ProxyHealthScore).filter(
                database.ProxyHealthScore.proxy_id == proxy_id
            ).first()
            
            if not health_record:
                return None
            
            score = self.calculate_health_score(proxy_id)
            
            return {
                'proxy_id': proxy_id,
                'health_score': score,
                'success_rate': health_record.success_rate or 1.0,
                'failure_count': health_record.failure_count or 0,
                'blocked_count': health_record.blocked_count or 0,
                'last_used': health_record.last_used,
            }
            
        except Exception as e:
            logger.error(f"Error getting proxy status for {proxy_id}: {e}")
            return None
    
    def decay_old_metrics(self):
        """Apply decay to old metrics outside the time window.
        
        This allows proxies to recover after being temporarily blocked or
        having issues. Metrics older than time_window_hours gradually lose weight.
        """
        try:
            cutoff_time = datetime.datetime.utcnow() - datetime.timedelta(hours=self.time_window_hours)
            
            # Get all health records with last_used before cutoff
            old_records = self.session.query(database.ProxyHealthScore).filter(
                database.ProxyHealthScore.last_used < cutoff_time
            ).all()
            
            for record in old_records:
                # Gradually reset metrics to allow recovery
                # Reduce failure and block counts slightly
                record.failure_count = max(0, int(record.failure_count * 0.9) if record.failure_count else 0)
                record.blocked_count = max(0, int(record.blocked_count * 0.9) if record.blocked_count else 0)
                
                # Boost success rate slightly
                if record.success_rate:
                    record.success_rate = min(1.0, record.success_rate + 0.05)
            
            self.session.commit()
            
        except Exception as e:
            logger.error(f"Error applying metric decay: {e}")
            self.session.rollback()
    
    def get_health_summary(self) -> Dict:
        """Get a summary of all proxy health statistics.
        
        Returns:
            Dictionary with overall proxy health statistics
        """
        try:
            proxies = self.session.query(database.Proxy).all()
            
            if not proxies:
                return {
                    'total_proxies': 0,
                    'healthy_count': 0,
                    'degraded_count': 0,
                    'failed_count': 0,
                    'average_score': 0,
                }
            
            scores = [self.calculate_health_score(p.id) for p in proxies]
            
            healthy_count = sum(1 for s in scores if s >= 70)
            degraded_count = sum(1 for s in scores if 30 <= s < 70)
            failed_count = sum(1 for s in scores if s < 30)
            
            return {
                'total_proxies': len(proxies),
                'healthy_count': healthy_count,
                'degraded_count': degraded_count,
                'failed_count': failed_count,
                'average_score': sum(scores) / len(scores) if scores else 0,
                'min_score': min(scores) if scores else 0,
                'max_score': max(scores) if scores else 0,
            }
            
        except Exception as e:
            logger.error(f"Error getting health summary: {e}")
            return {}
