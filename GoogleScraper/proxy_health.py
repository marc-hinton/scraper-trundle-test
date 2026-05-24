# -*- coding: utf-8 -*-

"""
Proxy health tracking system that monitors proxy performance metrics and manages
intelligent rotation strategies.

The ProxyHealthScorer maintains health scores (0-100) for each proxy based on:
- Success rate (recent successes vs failures)
- Block detection patterns
- Response time performance (using exponential moving average)
- Time-window decay (allowing temporary recovery from blocks)

Scoring formula:
    base_score = 100 - (failures / total_requests * 50) - (blocks / total_requests * 50)
    capped at 0-100

New proxies with no history receive neutral score of 50.
"""

import datetime
import logging
import random
from typing import List, Optional, Dict, Tuple
from GoogleScraper import database

logger = logging.getLogger(__name__)


class ProxyHealthScorer:
    """Manages health scoring and rotation strategy for proxies."""
    
    def __init__(self, session, time_window_hours=24, decay_factor=0.95):
        """
        Initialize the ProxyHealthScorer.
        
        Args:
            session: SQLAlchemy database session
            time_window_hours: Only consider metrics from the last N hours (default 24)
            decay_factor: Exponential decay factor for old metrics (0-1, default 0.95)
        """
        self.session = session
        self.time_window_hours = time_window_hours
        self.decay_factor = decay_factor
        self._in_memory_cache = {}  # In-memory cache for fast access
        
    def update_proxy_health(self, proxy_id: int, request_success: bool, 
                           was_blocked: bool = False, response_time_ms: float = 0) -> None:
        """
        Update proxy health metrics after a request.
        
        This method is called in the hot path of scraping, so it should be fast and non-blocking.
        
        Args:
            proxy_id: The ID of the proxy
            request_success: Whether the request was successful
            was_blocked: Whether the proxy was detected as blocked
            response_time_ms: Response time in milliseconds (0 if request failed)
        """
        try:
            # Get or create health score record
            health_record = self.session.query(database.ProxyHealthScore).filter(
                database.ProxyHealthScore.proxy_id == proxy_id
            ).first()
            
            if not health_record:
                health_record = database.ProxyHealthScore(
                    proxy_id=proxy_id,
                    success_rate=1.0,
                    failure_count=0,
                    blocked_count=0
                )
                self.session.add(health_record)
            
            # Update metrics
            if request_success and not was_blocked:
                # Successful request
                pass  # success_rate will be recalculated
            else:
                if was_blocked:
                    health_record.blocked_count += 1
                if not request_success:
                    health_record.failure_count += 1
            
            health_record.last_used = datetime.datetime.utcnow()
            
            # Update response time using exponential moving average
            if request_success and response_time_ms > 0:
                current_avg = self._get_in_memory_avg_response_time(proxy_id)
                if current_avg is None:
                    new_avg = response_time_ms
                else:
                    # EMA formula: new_avg = alpha * new_value + (1 - alpha) * old_avg
                    alpha = 0.3  # EMA smoothing factor
                    new_avg = alpha * response_time_ms + (1 - alpha) * current_avg
                self._set_in_memory_avg_response_time(proxy_id, new_avg)
            
            self.session.commit()
            # Note: We keep the response time in cache but invalidate any cached health scores
            # (health scores should be recalculated as the database has changed)
                
        except Exception as e:
            logger.error(f"Error updating proxy health for proxy {proxy_id}: {e}")
            self.session.rollback()
    
    def _get_in_memory_avg_response_time(self, proxy_id: int) -> Optional[float]:
        """Get the in-memory average response time for a proxy."""
        if proxy_id not in self._in_memory_cache:
            return None
        return self._in_memory_cache[proxy_id].get('avg_response_time')
    
    def _set_in_memory_avg_response_time(self, proxy_id: int, avg_time: float) -> None:
        """Set the in-memory average response time for a proxy."""
        if proxy_id not in self._in_memory_cache:
            self._in_memory_cache[proxy_id] = {}
        self._in_memory_cache[proxy_id]['avg_response_time'] = avg_time
    
    def calculate_health_score(self, proxy_id: int) -> float:
        """
        Calculate the current health score (0-100) for a proxy.
        
        Formula:
            base_score = 100 - (failures / total_requests * 50) - (blocks / total_requests * 50)
            
        New proxies with no history get neutral score of 50.
        Old metrics are weighted less due to time-window decay.
        
        Args:
            proxy_id: The ID of the proxy
            
        Returns:
            Health score between 0 and 100
        """
        health_record = self.session.query(database.ProxyHealthScore).filter(
            database.ProxyHealthScore.proxy_id == proxy_id
        ).first()
        
        if not health_record:
            # New proxy with no history: neutral score
            return 50.0
        
        # Get recent metrics within time window
        time_cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=self.time_window_hours)
        
        # If last used is before cutoff and has failures/blocks, reduce score
        if health_record.last_used and health_record.last_used < time_cutoff:
            # Proxy hasn't been used recently - give it a recovery score
            if health_record.failure_count + health_record.blocked_count > 0:
                return 60.0  # Slightly above neutral to allow recovery
            else:
                return 75.0  # Good score if it had success before
        
        # Calculate total requests and metrics
        total_requests = (health_record.blocked_count + health_record.failure_count)
        if total_requests == 0:
            # Only successes, no failures or blocks - use success_rate directly
            return min(100.0, health_record.success_rate * 100)
        
        # Calculate base score using formula
        failure_rate = health_record.failure_count / (total_requests + 1)  # +1 to avoid division by zero
        block_rate = health_record.blocked_count / (total_requests + 1)
        
        base_score = 100.0 - (failure_rate * 50.0) - (block_rate * 50.0)
        
        # Cap score between 0 and 100
        return max(0.0, min(100.0, base_score))
    
    def get_healthy_proxies(self, min_score: float = 70.0) -> List[Tuple[int, str, float]]:
        """
        Get all proxies with health score >= min_score, sorted by health score (descending).
        
        Args:
            min_score: Minimum health score to filter proxies (default 70)
            
        Returns:
            List of tuples: (proxy_id, proxy_ip, health_score), sorted by score descending
        """
        proxies = self.session.query(database.Proxy).all()
        
        healthy_proxies = []
        for proxy in proxies:
            score = self.calculate_health_score(proxy.id)
            if score >= min_score:
                healthy_proxies.append((proxy.id, proxy.ip, score))
        
        # Sort by score descending
        healthy_proxies.sort(key=lambda x: x[2], reverse=True)
        
        return healthy_proxies
    
    def select_proxy_for_job(self, min_score: float = 70.0) -> Optional[int]:
        """
        Select a proxy for a job using weighted random selection biased toward healthier proxies.
        
        This implements an intelligent rotation strategy that:
        1. Prefers healthier proxies (higher probability)
        2. Still occasionally uses lower-scored proxies (to test recovery)
        3. Excludes very poor proxies (score < min_score)
        
        Args:
            min_score: Minimum health score threshold (default 70)
            
        Returns:
            Selected proxy ID, or None if no eligible proxies exist
        """
        proxies = self.session.query(database.Proxy).all()
        
        eligible_proxies = []
        weights = []
        
        for proxy in proxies:
            score = self.calculate_health_score(proxy.id)
            if score >= min_score:
                eligible_proxies.append(proxy.id)
                # Weight proportional to score: higher score = higher probability
                # Use quadratic weighting to increase the bias toward better proxies
                weight = (score / 100.0) ** 2
                weights.append(weight)
        
        if not eligible_proxies:
            # No proxies meet minimum score - return None or select from all
            logger.warning("No proxies with score >= {}, selecting from all proxies".format(min_score))
            return None
        
        # Weighted random selection
        total_weight = sum(weights)
        if total_weight == 0:
            return eligible_proxies[0]
        
        # Normalize weights to probabilities
        probabilities = [w / total_weight for w in weights]
        
        selected_proxy_id = random.choices(eligible_proxies, weights=probabilities, k=1)[0]
        return selected_proxy_id
    
    def get_proxy_stats(self, proxy_id: int) -> Dict:
        """
        Get detailed statistics for a proxy.
        
        Args:
            proxy_id: The ID of the proxy
            
        Returns:
            Dictionary with detailed proxy statistics
        """
        health_record = self.session.query(database.ProxyHealthScore).filter(
            database.ProxyHealthScore.proxy_id == proxy_id
        ).first()
        
        proxy = self.session.query(database.Proxy).filter(
            database.Proxy.id == proxy_id
        ).first()
        
        if not health_record or not proxy:
            return {}
        
        total_requests = health_record.blocked_count + health_record.failure_count
        success_count = max(0, total_requests - health_record.failure_count - health_record.blocked_count)
        
        return {
            'proxy_id': proxy_id,
            'proxy_ip': proxy.ip,
            'health_score': self.calculate_health_score(proxy_id),
            'success_count': success_count,
            'failure_count': health_record.failure_count,
            'block_count': health_record.blocked_count,
            'success_rate': health_record.success_rate,
            'last_used': health_record.last_used,
            'avg_response_time_ms': self._get_in_memory_avg_response_time(proxy_id),
        }
    
    def reset_proxy_health(self, proxy_id: int) -> None:
        """
        Reset health metrics for a proxy (useful for manual recovery).
        
        Args:
            proxy_id: The ID of the proxy
        """
        try:
            health_record = self.session.query(database.ProxyHealthScore).filter(
                database.ProxyHealthScore.proxy_id == proxy_id
            ).first()
            
            if health_record:
                health_record.failure_count = 0
                health_record.blocked_count = 0
                health_record.success_rate = 1.0
                health_record.last_used = None
                self.session.commit()
                
            # Clear cache
            if proxy_id in self._in_memory_cache:
                del self._in_memory_cache[proxy_id]
                
            logger.info(f"Reset health metrics for proxy {proxy_id}")
        except Exception as e:
            logger.error(f"Error resetting health for proxy {proxy_id}: {e}")
            self.session.rollback()
