# -*- coding: utf-8 -*-

"""
Tests for proxy health scoring and rotation strategy.

Tests the ProxyHealthScorer class including:
- Health score calculation
- Time-window decay
- Exponential moving average for response times
- Healthy proxy filtering
- Weighted proxy selection
- Edge cases (new proxies, no history, etc.)
"""

import unittest
import datetime
from GoogleScraper.proxy_health import ProxyHealthScorer


class TestProxyHealthScoring(unittest.TestCase):
    """Test health score calculation."""
    
    def setUp(self):
        """Initialize a fresh ProxyHealthScorer for each test."""
        self.scorer = ProxyHealthScorer(time_window_hours=24, ema_alpha=0.3)
    
    def test_new_proxy_has_neutral_score(self):
        """New proxies with no history should have neutral score of 50."""
        score = self.scorer._calculate_health_score(999)
        self.assertEqual(score, 50.0)
    
    def test_perfect_proxy_score(self):
        """Proxy with all successful requests should have score of 100."""
        proxy_id = 1
        for i in range(10):
            self.scorer.update_proxy_health(proxy_id, request_success=True, 
                                           was_blocked=False, response_time_ms=100)
        score = self.scorer._calculate_health_score(proxy_id)
        self.assertEqual(score, 100.0)
    
    def test_all_failures_score(self):
        """Proxy with all failed requests (not blocked) should have score of 50.
        
        Formula: 100 - (1.0 * 50) - (0.0 * 50) = 50
        """
        proxy_id = 2
        for i in range(10):
            self.scorer.update_proxy_health(proxy_id, request_success=False, 
                                           was_blocked=False, response_time_ms=100)
        score = self.scorer._calculate_health_score(proxy_id)
        self.assertEqual(score, 50.0)
    
    def test_all_blocked_score(self):
        """Proxy with all blocked requests should have score of 0.
        
        Blocked requests count as both failures and blocks.
        Formula: 100 - (1.0 * 50) - (1.0 * 50) = 0
        """
        proxy_id = 3
        for i in range(10):
            self.scorer.update_proxy_health(proxy_id, request_success=False, 
                                           was_blocked=True, response_time_ms=100)
        score = self.scorer._calculate_health_score(proxy_id)
        self.assertEqual(score, 0.0)
    
    def test_mixed_results_score(self):
        """Test score calculation with mixed success/failure/blocks.
        
        5 successes, 3 failures, 2 blocks = 10 total
        failure_rate = 5/10 = 0.5, block_rate = 2/10 = 0.2
        score = 100 - (0.5 * 50) - (0.2 * 50) = 100 - 25 - 10 = 65
        """
        proxy_id = 4
        # 5 successes
        for i in range(5):
            self.scorer.update_proxy_health(proxy_id, request_success=True, 
                                           was_blocked=False, response_time_ms=100)
        # 3 failures (not blocked)
        for i in range(3):
            self.scorer.update_proxy_health(proxy_id, request_success=False, 
                                           was_blocked=False, response_time_ms=100)
        # 2 failures (blocked)
        for i in range(2):
            self.scorer.update_proxy_health(proxy_id, request_success=False, 
                                           was_blocked=True, response_time_ms=100)
        
        score = self.scorer._calculate_health_score(proxy_id)
        self.assertEqual(score, 65.0)
    
    def test_50_percent_failure_score(self):
        """Test 50% failure rate with no blocks.
        
        5 successes, 5 failures = 10 total
        failure_rate = 5/10 = 0.5, block_rate = 0/10 = 0.0
        score = 100 - (0.5 * 50) - (0.0 * 50) = 75
        """
        proxy_id = 5
        for i in range(5):
            self.scorer.update_proxy_health(proxy_id, request_success=True, 
                                           was_blocked=False, response_time_ms=100)
        for i in range(5):
            self.scorer.update_proxy_health(proxy_id, request_success=False, 
                                           was_blocked=False, response_time_ms=100)
        score = self.scorer._calculate_health_score(proxy_id)
        self.assertEqual(score, 75.0)
    
    def test_score_capped_at_100(self):
        """Score should be capped at 100."""
        proxy_id = 6
        self.scorer.update_proxy_health(proxy_id, request_success=True, 
                                       was_blocked=False, response_time_ms=100)
        score = self.scorer._calculate_health_score(proxy_id)
        self.assertLessEqual(score, 100.0)
    
    def test_score_capped_at_0(self):
        """Score should be capped at 0."""
        proxy_id = 7
        for i in range(10):
            self.scorer.update_proxy_health(proxy_id, request_success=False, 
                                           was_blocked=True, response_time_ms=100)
        score = self.scorer._calculate_health_score(proxy_id)
        self.assertGreaterEqual(score, 0.0)


class TestTimeWindowDecay(unittest.TestCase):
    """Test time-window decay mechanism."""
    
    def test_old_metrics_are_excluded(self):
        """Metrics older than time window should not affect score."""
        scorer = ProxyHealthScorer(time_window_hours=1, ema_alpha=0.3)
        proxy_id = 10
        
        # Add old request (simulate being added 2 hours ago)
        now = datetime.datetime.utcnow()
        old_time = now - datetime.timedelta(hours=2)
        
        # Manually add old request to history to test decay
        metrics = scorer.proxy_metrics[proxy_id]
        metrics['request_history'].append((old_time, False, False, 100))
        metrics['total_requests'] = 1
        metrics['failure_count'] = 1
        
        # Add recent success (clears old entries automatically)
        scorer.update_proxy_health(proxy_id, request_success=True, 
                                  was_blocked=False, response_time_ms=100)
        
        # Score should be based only on recent success (within 1 hour window)
        score = scorer._calculate_health_score(proxy_id)
        # Only 1 recent request which is a success, so score should be 100
        self.assertEqual(score, 100.0)


class TestExponentialMovingAverage(unittest.TestCase):
    """Test exponential moving average for response times."""
    
    def setUp(self):
        """Initialize a fresh ProxyHealthScorer for each test."""
        self.scorer = ProxyHealthScorer(time_window_hours=24, ema_alpha=0.3)
    
    def test_ema_initialization(self):
        """First response time should initialize EMA."""
        proxy_id = 20
        self.scorer.update_proxy_health(proxy_id, request_success=True, 
                                       was_blocked=False, response_time_ms=100)
        metrics = self.scorer.proxy_metrics[proxy_id]
        self.assertEqual(metrics['avg_response_time'], 100.0)
    
    def test_ema_calculation(self):
        """EMA should weight new values by alpha.
        
        First value: 100
        Second value: 200, with alpha=0.3
        EMA = 0.3 * 200 + 0.7 * 100 = 60 + 70 = 130
        """
        proxy_id = 21
        self.scorer.update_proxy_health(proxy_id, request_success=True, 
                                       was_blocked=False, response_time_ms=100)
        self.scorer.update_proxy_health(proxy_id, request_success=True, 
                                       was_blocked=False, response_time_ms=200)
        metrics = self.scorer.proxy_metrics[proxy_id]
        self.assertAlmostEqual(metrics['avg_response_time'], 130.0, places=1)
    
    def test_ema_avoids_outliers(self):
        """EMA should smooth out outlier response times."""
        proxy_id = 22
        
        # Add multiple normal values around 100
        for i in range(5):
            self.scorer.update_proxy_health(proxy_id, request_success=True, 
                                           was_blocked=False, response_time_ms=100)
        
        # Add an outlier
        self.scorer.update_proxy_health(proxy_id, request_success=True, 
                                       was_blocked=False, response_time_ms=500)
        
        # EMA should be closer to 100 than to 500
        metrics = self.scorer.proxy_metrics[proxy_id]
        avg = metrics['avg_response_time']
        # EMA after outlier: should be much closer to 100 than 500
        distance_to_100 = abs(avg - 100)
        distance_to_500 = abs(avg - 500)
        self.assertLess(distance_to_100, distance_to_500)


class TestGetHealthyProxies(unittest.TestCase):
    """Test filtering and sorting of healthy proxies."""
    
    def setUp(self):
        """Initialize scorer and add test proxies with various scores."""
        self.scorer = ProxyHealthScorer()
        
        # Proxy 1: score 100 (10 successes)
        for i in range(10):
            self.scorer.update_proxy_health(1, request_success=True, 
                                           was_blocked=False, response_time_ms=100)
        
        # Proxy 2: score 75 (5 successes, 5 failures)
        for i in range(5):
            self.scorer.update_proxy_health(2, request_success=True, 
                                           was_blocked=False, response_time_ms=100)
        for i in range(5):
            self.scorer.update_proxy_health(2, request_success=False, 
                                           was_blocked=False, response_time_ms=100)
        
        # Proxy 3: score 50 (all failures, no blocks)
        for i in range(10):
            self.scorer.update_proxy_health(3, request_success=False, 
                                           was_blocked=False, response_time_ms=100)
    
    def test_get_healthy_proxies_filters_by_score(self):
        """Should only return proxies above minimum score."""
        healthy = self.scorer.get_healthy_proxies(min_score=70)
        proxy_ids = [p_id for p_id, _ in healthy]
        
        # Only proxies 1 (100) and 2 (75) should be returned
        self.assertIn(1, proxy_ids)
        self.assertIn(2, proxy_ids)
        self.assertNotIn(3, proxy_ids)  # 50 < 70
    
    def test_get_healthy_proxies_sorted_descending(self):
        """Should return proxies sorted by score descending."""
        healthy = self.scorer.get_healthy_proxies(min_score=0)
        
        if len(healthy) > 1:
            for i in range(len(healthy) - 1):
                self.assertGreaterEqual(healthy[i][1], healthy[i + 1][1])
    
    def test_get_healthy_proxies_with_zero_threshold(self):
        """Should return all proxies when threshold is 0."""
        healthy = self.scorer.get_healthy_proxies(min_score=0)
        # Should include proxies 1, 2, 3
        self.assertGreaterEqual(len(healthy), 3)


class TestProxySelection(unittest.TestCase):
    """Test weighted proxy selection strategy."""
    
    def setUp(self):
        """Initialize scorer with test proxies."""
        self.scorer = ProxyHealthScorer()
        
        # Proxy 1: score 100
        for i in range(10):
            self.scorer.update_proxy_health(1, request_success=True, 
                                           was_blocked=False, response_time_ms=100)
        
        # Proxy 2: score 75 (5 success, 5 failures)
        for i in range(5):
            self.scorer.update_proxy_health(2, request_success=True, 
                                           was_blocked=False, response_time_ms=100)
        for i in range(5):
            self.scorer.update_proxy_health(2, request_success=False, 
                                           was_blocked=False, response_time_ms=100)
    
    def test_select_proxy_returns_valid_proxy(self):
        """Should return a valid proxy_id."""
        selected = self.scorer.select_proxy_for_job()
        self.assertIn(selected, [1, 2])
    
    def test_select_proxy_respects_min_score(self):
        """Should respect minimum score threshold."""
        # With min_score=80, only proxy 1 (score 100) should be selected
        for _ in range(20):
            selected = self.scorer.select_proxy_for_job(min_score=80)
            self.assertEqual(selected, 1)
    
    def test_select_proxy_biased_toward_healthy(self):
        """Healthier proxies should be selected more often."""
        selections = {}
        
        # Make many selections
        for _ in range(100):
            selected = self.scorer.select_proxy_for_job(min_score=0)
            selections[selected] = selections.get(selected, 0) + 1
        
        # Proxy 1 (score 100) should be selected more than proxy 2 (score 75)
        self.assertGreater(selections.get(1, 0), selections.get(2, 0))
    
    def test_select_proxy_single_proxy(self):
        """Should return the only proxy if only one available."""
        scorer = ProxyHealthScorer()
        scorer.update_proxy_health(99, request_success=True, 
                                  was_blocked=False, response_time_ms=100)
        
        selected = scorer.select_proxy_for_job()
        self.assertEqual(selected, 99)
    
    def test_select_proxy_no_proxies(self):
        """Should return None if no proxies available."""
        scorer = ProxyHealthScorer()
        selected = scorer.select_proxy_for_job()
        self.assertIsNone(selected)
    
    def test_select_proxy_no_proxies_meet_min_score(self):
        """Should return None if no proxies meet minimum score."""
        selected = self.scorer.select_proxy_for_job(min_score=110)
        self.assertIsNone(selected)


class TestProxyStats(unittest.TestCase):
    """Test proxy statistics retrieval."""
    
    def setUp(self):
        """Initialize scorer and add test data."""
        self.scorer = ProxyHealthScorer()
        self.scorer.update_proxy_health(1, request_success=True, 
                                       was_blocked=False, response_time_ms=100)
        self.scorer.update_proxy_health(1, request_success=False, 
                                       was_blocked=True, response_time_ms=200)
    
    def test_get_proxy_health_stats(self):
        """Should return detailed stats for a proxy."""
        stats = self.scorer.get_proxy_health_stats(1)
        
        self.assertEqual(stats['proxy_id'], 1)
        self.assertEqual(stats['total_requests'], 2)
        self.assertEqual(stats['success_count'], 1)
        self.assertEqual(stats['failure_count'], 1)
        self.assertEqual(stats['block_count'], 1)
        self.assertIsNotNone(stats['avg_response_time'])
        self.assertIsNotNone(stats['last_used_time'])
    
    def test_get_proxy_health_stats_unknown_proxy(self):
        """Should return default stats for unknown proxy."""
        stats = self.scorer.get_proxy_health_stats(999)
        
        self.assertEqual(stats['proxy_id'], 999)
        self.assertEqual(stats['health_score'], 50.0)
        self.assertEqual(stats['total_requests'], 0)
    
    def test_get_all_proxy_stats(self):
        """Should return stats for all proxies."""
        self.scorer.update_proxy_health(2, request_success=True, 
                                       was_blocked=False, response_time_ms=100)
        
        all_stats = self.scorer.get_all_proxy_stats()
        
        self.assertIn(1, all_stats)
        self.assertIn(2, all_stats)
        self.assertEqual(len(all_stats), 2)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and corner scenarios."""
    
    def setUp(self):
        """Initialize a fresh ProxyHealthScorer for each test."""
        self.scorer = ProxyHealthScorer()
    
    def test_zero_response_time(self):
        """Should handle zero response time."""
        self.scorer.update_proxy_health(1, request_success=True, 
                                       was_blocked=False, response_time_ms=0)
        metrics = self.scorer.proxy_metrics[1]
        self.assertEqual(metrics['avg_response_time'], 0.0)
    
    def test_very_large_response_time(self):
        """Should handle very large response times."""
        self.scorer.update_proxy_health(2, request_success=True, 
                                       was_blocked=False, response_time_ms=999999)
        metrics = self.scorer.proxy_metrics[2]
        self.assertEqual(metrics['avg_response_time'], 999999.0)
    
    def test_many_proxies(self):
        """Should handle many proxies efficiently."""
        # Add 1000 proxies
        for proxy_id in range(1000):
            self.scorer.update_proxy_health(proxy_id, request_success=True, 
                                           was_blocked=False, response_time_ms=100)
        
        healthy = self.scorer.get_healthy_proxies(min_score=90)
        self.assertEqual(len(healthy), 1000)
    
    def test_update_same_proxy_many_times(self):
        """Should handle many updates to the same proxy."""
        for i in range(1000):
            self.scorer.update_proxy_health(1, request_success=(i % 2 == 0), 
                                           was_blocked=False, response_time_ms=100 + i)
        
        metrics = self.scorer.proxy_metrics[1]
        self.assertEqual(metrics['total_requests'], 1000)


if __name__ == '__main__':
    unittest.main()
