# -*- coding: utf-8 -*-

"""
Real-time proxy health scoring and rotation strategy.

Monitors each proxy's success/failure rates, response times, and block detection
patterns. Maintains a health score (0-100) for each proxy based on recent performance
metrics. Implements intelligent rotation strategies that prefer healthier proxies
and gradually phase out failing ones.
"""

import logging
import datetime
import random
from collections import defaultdict
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger(__name__)


class ProxyHealthScorer:
    """
    Tracks real-time proxy health metrics and provides intelligent proxy selection.
    
    Maintains a health score (0-100) for each proxy based on:
    - Success/failure rates
    - Block detection patterns
    - Response times
    - Recent performance (with time-window decay)
    """
    
    def __init__(self, time_window_hours: int = 24, ema_alpha: float = 0.3):
        """
        Initialize ProxyHealthScorer.
        
        Args:
            time_window_hours: Number of hours to consider for metrics (default 24)
            ema_alpha: Exponential moving average alpha (weight for new values, default 0.3)
        """
        self.time_window_hours = time_window_hours
        self.ema_alpha = ema_alpha
        
        # Per-proxy metrics: {proxy_id: {metric_name: value}}
        self.proxy_metrics = defaultdict(lambda: {
            'success_count': 0,
            'failure_count': 0,
            'block_count': 0,
            'total_requests': 0,
            'last_used_time': None,
            'avg_response_time': None,  # Using EMA
            'request_history': []  # List of (timestamp, success, blocked, response_time)
        })
    
    def update_proxy_health(self, proxy_id: int, request_success: bool, 
                           was_blocked: bool, response_time_ms: float) -> None:
        """
        Update health metrics for a proxy based on a single request.
        
        Args:
            proxy_id: Unique identifier for the proxy
            request_success: Whether the request succeeded (True/False)
            was_blocked: Whether the proxy was blocked (True/False)
            response_time_ms: Response time in milliseconds
        """
        if proxy_id not in self.proxy_metrics:
            self.proxy_metrics[proxy_id] = {
                'success_count': 0,
                'failure_count': 0,
                'block_count': 0,
                'total_requests': 0,
                'last_used_time': None,
                'avg_response_time': None,
                'request_history': []
            }
        
        metrics = self.proxy_metrics[proxy_id]
        now = datetime.datetime.utcnow()
        
        # Update counts
        metrics['total_requests'] += 1
        if request_success:
            metrics['success_count'] += 1
        else:
            metrics['failure_count'] += 1
        
        if was_blocked:
            metrics['block_count'] += 1
        
        # Update last used time
        metrics['last_used_time'] = now
        
        # Update response time using exponential moving average
        if metrics['avg_response_time'] is None:
            metrics['avg_response_time'] = response_time_ms
        else:
            metrics['avg_response_time'] = (
                self.ema_alpha * response_time_ms + 
                (1 - self.ema_alpha) * metrics['avg_response_time']
            )
        
        # Add to request history for time-window decay
        metrics['request_history'].append((now, request_success, was_blocked, response_time_ms))
        
        # Clean up old history entries (older than time window)
        cutoff_time = now - datetime.timedelta(hours=self.time_window_hours)
        metrics['request_history'] = [
            entry for entry in metrics['request_history']
            if entry[0] > cutoff_time
        ]
    
    def _calculate_health_score(self, proxy_id: int) -> float:
        """
        Calculate health score for a proxy (0-100).
        
        Formula: base_score = 100 - (failures / total * 50) - (blocks / total * 50)
        
        New proxies with no history return neutral score of 50.
        
        Args:
            proxy_id: Unique identifier for the proxy
            
        Returns:
            Health score between 0 and 100
        """
        if proxy_id not in self.proxy_metrics:
            return 50.0  # Neutral score for unknown proxy
        
        metrics = self.proxy_metrics[proxy_id]
        total_requests = metrics['total_requests']
        
        # New proxies with no history get neutral score
        if total_requests == 0:
            return 50.0
        
        # Consider only recent requests (within time window)
        now = datetime.datetime.utcnow()
        cutoff_time = now - datetime.timedelta(hours=self.time_window_hours)
        
        recent_history = [
            entry for entry in metrics['request_history']
            if entry[0] > cutoff_time
        ]
        
        if not recent_history:
            # No recent history, return neutral
            return 50.0
        
        # Calculate failure and block rates from recent history
        recent_failures = sum(1 for _, success, _, _ in recent_history if not success)
        recent_blocks = sum(1 for _, _, blocked, _ in recent_history if blocked)
        recent_total = len(recent_history)
        
        # Apply decay to older entries (entries further back have less weight)
        # Simplification: use only recent window for scoring
        failure_rate = recent_failures / recent_total if recent_total > 0 else 0
        block_rate = recent_blocks / recent_total if recent_total > 0 else 0
        
        # Calculate base score
        base_score = 100 - (failure_rate * 50) - (block_rate * 50)
        
        # Cap at 0-100
        return max(0.0, min(100.0, base_score))
    
    def get_healthy_proxies(self, min_score: float = 70.0) -> List[Tuple[int, float]]:
        """
        Get all proxies meeting minimum health score, sorted by score (descending).
        
        Args:
            min_score: Minimum health score threshold (default 70)
            
        Returns:
            List of tuples: (proxy_id, health_score) sorted by score descending
        """
        healthy = []
        
        for proxy_id in self.proxy_metrics.keys():
            score = self._calculate_health_score(proxy_id)
            if score >= min_score:
                healthy.append((proxy_id, score))
        
        # Sort by score descending
        healthy.sort(key=lambda x: x[1], reverse=True)
        
        return healthy
    
    def select_proxy_for_job(self, job_config: Dict = None, min_score: float = 0.0) -> Optional[int]:
        """
        Select a proxy for a job using weighted random selection biased toward healthier proxies.
        
        Uses a softmax-like weighting so healthier proxies are more likely to be selected,
        but even lower-scoring proxies have a chance (allowing recovery).
        
        Args:
            job_config: Job configuration (for future use, e.g., region requirements)
            min_score: Minimum acceptable health score (default 0, allow all)
            
        Returns:
            Selected proxy_id, or None if no proxies meet criteria
        """
        if not self.proxy_metrics:
            return None
        
        # Get all proxies with their scores
        all_proxies = []
        for proxy_id in self.proxy_metrics.keys():
            score = self._calculate_health_score(proxy_id)
            if score >= min_score:
                all_proxies.append((proxy_id, score))
        
        if not all_proxies:
            return None
        
        # If only one proxy, return it
        if len(all_proxies) == 1:
            return all_proxies[0][0]
        
        # Apply softmax-like transformation to scores for weighted selection
        # Normalize scores to 0-100 range, then apply exponential weighting
        scores = [score for _, score in all_proxies]
        min_s = min(scores)
        max_s = max(scores)
        
        # Avoid division by zero
        if min_s == max_s:
            # All equal scores: uniform distribution
            weights = [1.0] * len(all_proxies)
        else:
            # Normalize to 0-1, then apply exponential
            normalized = [(s - min_s) / (max_s - min_s) for s in scores]
            # Apply exponential to emphasize differences
            weights = [2 ** norm for norm in normalized]
        
        # Random weighted selection
        proxy_id = random.choices(
            [p_id for p_id, _ in all_proxies],
            weights=weights,
            k=1
        )[0]
        
        return proxy_id
    
    def get_proxy_health_stats(self, proxy_id: int) -> Dict:
        """
        Get detailed health statistics for a specific proxy.
        
        Args:
            proxy_id: Unique identifier for the proxy
            
        Returns:
            Dictionary with health metrics and score
        """
        if proxy_id not in self.proxy_metrics:
            return {
                'proxy_id': proxy_id,
                'health_score': 50.0,
                'success_count': 0,
                'failure_count': 0,
                'block_count': 0,
                'total_requests': 0,
                'last_used_time': None,
                'avg_response_time': None
            }
        
        metrics = self.proxy_metrics[proxy_id]
        
        return {
            'proxy_id': proxy_id,
            'health_score': self._calculate_health_score(proxy_id),
            'success_count': metrics['success_count'],
            'failure_count': metrics['failure_count'],
            'block_count': metrics['block_count'],
            'total_requests': metrics['total_requests'],
            'last_used_time': metrics['last_used_time'],
            'avg_response_time': metrics['avg_response_time']
        }
    
    def get_all_proxy_stats(self) -> Dict[int, Dict]:
        """
        Get health statistics for all proxies.
        
        Returns:
            Dictionary mapping proxy_id to stats
        """
        stats = {}
        for proxy_id in self.proxy_metrics.keys():
            stats[proxy_id] = self.get_proxy_health_stats(proxy_id)
        
        return stats
