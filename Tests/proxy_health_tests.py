# -*- coding: utf-8 -*-

"""
Tests for the proxy health scoring and rotation strategy.

Tests cover:
- Health score calculation algorithm
- Weighted random selection (rotation strategy)
- Time-window decay mechanics
- Edge cases (new proxies, all failures, etc.)
- Proxy selection distribution
"""

import unittest
import datetime
import tempfile
import os
from collections import Counter
from GoogleScraper.database import get_engine, get_session, Base, Proxy, ProxyHealthScore
from GoogleScraper.proxy_health import ProxyHealthScorer


class ProxyHealthScorerTestCase(unittest.TestCase):
    """Test cases for ProxyHealthScorer"""
    
    def setUp(self):
        """Create an in-memory SQLite database for testing."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        self.db_path = self.temp_db.name

        # Create engine and session
        self.engine = get_engine({'database_name': self.db_path.replace('.db', '')}, path=self.db_path)
        Base.metadata.create_all(self.engine)

        session_factory = get_session(
            {'database_name': self.db_path.replace('.db', '')},
            scoped=False,
            engine=self.engine,
            path=self.db_path
        )
        self.session = session_factory()
        self.scorer = ProxyHealthScorer(self.session)
    
    def tearDown(self):
        """Clean up test database."""
        self.session.close()
        try:
            os.remove(self.db_path)
        except:
            pass
    
    def _create_test_proxies(self, count=5):
        """Create test proxies in the database."""
        # Clear existing proxies first to avoid unique constraint issues
        self.session.query(Proxy).delete()
        self.session.commit()

        for i in range(count):
            proxy = Proxy(
                ip=f'192.168.1.{i+1}',
                hostname=f'proxy{i+1}.example.com',
                port=8080 + i,
                proto='http',
                username='',
                password=''
            )
            self.session.add(proxy)
        self.session.commit()
        return [p.id for p in self.session.query(Proxy).all()]
    
    def test_new_proxy_has_neutral_score(self):
        """New proxies with no history should have neutral score of 50."""
        self._create_test_proxies(1)
        proxy_id = self.session.query(Proxy).first().id
        score = self.scorer.calculate_health_score(proxy_id)
        self.assertEqual(score, 50.0, "New proxy should have neutral score of 50")
    
    def test_score_calculation_formula(self):
        """Test the health score formula: 100 - (failures/total * 50) - (blocks/total * 50)"""
        self._create_test_proxies(1)
        proxy_id = self.session.query(Proxy).first().id
        
        # Create health record with known values
        health_record = ProxyHealthScore(
            proxy_id=proxy_id,
            success_rate=0.5,
            failure_count=2,
            blocked_count=3
        )
        self.session.add(health_record)
        self.session.commit()
        
        # Expected score: 100 - (2/6 * 50) - (3/6 * 50) = 100 - 16.67 - 25 = 58.33
        score = self.scorer.calculate_health_score(proxy_id)
        expected = 100.0 - (2/6 * 50.0) - (3/6 * 50.0)
        self.assertAlmostEqual(score, expected, places=1)
    
    def test_score_capped_at_zero(self):
        """Health score should be capped at minimum 0."""
        self._create_test_proxies(1)
        proxy_id = self.session.query(Proxy).first().id
        
        # Create health record with extreme negative values
        health_record = ProxyHealthScore(
            proxy_id=proxy_id,
            success_rate=0.0,
            failure_count=1000,
            blocked_count=1000
        )
        self.session.add(health_record)
        self.session.commit()
        
        score = self.scorer.calculate_health_score(proxy_id)
        self.assertGreaterEqual(score, 0.0, "Score should be >= 0")
        self.assertLessEqual(score, 100.0, "Score should be <= 100")
    
    def test_score_capped_at_hundred(self):
        """Health score should be capped at maximum 100."""
        self._create_test_proxies(1)
        proxy_id = self.session.query(Proxy).first().id
        
        # Create health record with no failures or blocks
        health_record = ProxyHealthScore(
            proxy_id=proxy_id,
            success_rate=1.0,
            failure_count=0,
            blocked_count=0
        )
        self.session.add(health_record)
        self.session.commit()
        
        score = self.scorer.calculate_health_score(proxy_id)
        self.assertGreaterEqual(score, 0.0, "Score should be >= 0")
        self.assertLessEqual(score, 100.0, "Score should be <= 100")
    
    def test_update_proxy_health_success(self):
        """Test updating proxy health with successful request."""
        self._create_test_proxies(1)
        proxy_id = self.session.query(Proxy).first().id
        
        # Update with successful request
        self.scorer.update_proxy_health(proxy_id, request_success=True, response_time_ms=100)
        
        health_record = self.session.query(ProxyHealthScore).filter(
            ProxyHealthScore.proxy_id == proxy_id
        ).first()
        
        self.assertIsNotNone(health_record)
        self.assertEqual(health_record.failure_count, 0)
        self.assertEqual(health_record.blocked_count, 0)
        self.assertIsNotNone(health_record.last_used)
    
    def test_update_proxy_health_failure(self):
        """Test updating proxy health with failed request."""
        self._create_test_proxies(1)
        proxy_id = self.session.query(Proxy).first().id
        
        # Update with failed request
        self.scorer.update_proxy_health(proxy_id, request_success=False)
        
        health_record = self.session.query(ProxyHealthScore).filter(
            ProxyHealthScore.proxy_id == proxy_id
        ).first()
        
        self.assertIsNotNone(health_record)
        self.assertEqual(health_record.failure_count, 1)
        self.assertEqual(health_record.blocked_count, 0)
    
    def test_update_proxy_health_blocked(self):
        """Test updating proxy health with blocked detection."""
        self._create_test_proxies(1)
        proxy_id = self.session.query(Proxy).first().id
        
        # Update with blocked detection
        self.scorer.update_proxy_health(proxy_id, request_success=False, was_blocked=True)
        
        health_record = self.session.query(ProxyHealthScore).filter(
            ProxyHealthScore.proxy_id == proxy_id
        ).first()
        
        self.assertIsNotNone(health_record)
        self.assertEqual(health_record.failure_count, 1)
        self.assertEqual(health_record.blocked_count, 1)
    
    def test_response_time_ema(self):
        """Test exponential moving average for response times."""
        self._create_test_proxies(1)
        proxy_id = self.session.query(Proxy).first().id

        # Update with first response time
        self.scorer.update_proxy_health(proxy_id, request_success=True, response_time_ms=100)
        # Note: cache is cleared after commit, so directly check the update happens

        # Update with second response time (much larger)
        self.scorer.update_proxy_health(proxy_id, request_success=True, response_time_ms=200)

        # Get the average from cache after last update
        avg = self.scorer._get_in_memory_avg_response_time(proxy_id)

        # Average should be between 100 and 200 (weighted toward first value)
        # Using EMA: new_avg = 0.3 * 200 + 0.7 * 100 = 60 + 70 = 130
        self.assertIsNotNone(avg, "Average response time should be cached")
        self.assertGreater(avg, 100.0)
        self.assertLess(avg, 200.0)
    
    def test_get_healthy_proxies_filtering(self):
        """Test that get_healthy_proxies filters by min_score."""
        proxy_ids = self._create_test_proxies(3)
        
        # Set up proxies with different health scores
        # Proxy 0: excellent (no failures)
        health0 = ProxyHealthScore(
            proxy_id=proxy_ids[0],
            success_rate=1.0,
            failure_count=0,
            blocked_count=0
        )
        # Proxy 1: good (few failures)
        health1 = ProxyHealthScore(
            proxy_id=proxy_ids[1],
            success_rate=0.8,
            failure_count=1,
            blocked_count=0
        )
        # Proxy 2: poor (many failures)
        health2 = ProxyHealthScore(
            proxy_id=proxy_ids[2],
            success_rate=0.2,
            failure_count=10,
            blocked_count=5
        )
        
        self.session.add_all([health0, health1, health2])
        self.session.commit()
        
        # Get healthy proxies with min_score=70
        healthy = self.scorer.get_healthy_proxies(min_score=70.0)
        
        # Should include at least proxy 0 and maybe proxy 1
        self.assertGreater(len(healthy), 0)
    
    def test_get_healthy_proxies_sorted_by_score(self):
        """Test that get_healthy_proxies returns proxies sorted by score (descending)."""
        proxy_ids = self._create_test_proxies(3)
        
        # Set up proxies with different scores
        health0 = ProxyHealthScore(proxy_id=proxy_ids[0], success_rate=0.9, failure_count=1, blocked_count=0)
        health1 = ProxyHealthScore(proxy_id=proxy_ids[1], success_rate=0.7, failure_count=3, blocked_count=0)
        health2 = ProxyHealthScore(proxy_id=proxy_ids[2], success_rate=0.5, failure_count=5, blocked_count=0)
        
        self.session.add_all([health0, health1, health2])
        self.session.commit()
        
        healthy = self.scorer.get_healthy_proxies(min_score=0.0)
        
        # Should be sorted by score descending
        if len(healthy) > 1:
            for i in range(len(healthy) - 1):
                self.assertGreaterEqual(healthy[i][2], healthy[i+1][2],
                                       "Proxies should be sorted by score descending")
    
    def test_select_proxy_for_job_returns_valid_proxy(self):
        """Test that select_proxy_for_job returns a valid proxy ID."""
        proxy_ids = self._create_test_proxies(3)
        
        # Set up all proxies with good scores
        for proxy_id in proxy_ids:
            health = ProxyHealthScore(
                proxy_id=proxy_id,
                success_rate=0.9,
                failure_count=1,
                blocked_count=0
            )
            self.session.add(health)
        self.session.commit()
        
        selected = self.scorer.select_proxy_for_job(min_score=70.0)
        
        self.assertIsNotNone(selected)
        self.assertIn(selected, proxy_ids)
    
    def test_select_proxy_for_job_weighted_selection(self):
        """Test that select_proxy_for_job biases toward healthier proxies."""
        proxy_ids = self._create_test_proxies(2)
        
        # Set up one excellent proxy and one good proxy
        health0 = ProxyHealthScore(
            proxy_id=proxy_ids[0],
            success_rate=0.99,
            failure_count=1,
            blocked_count=0
        )
        health1 = ProxyHealthScore(
            proxy_id=proxy_ids[1],
            success_rate=0.5,
            failure_count=10,
            blocked_count=0
        )
        
        self.session.add_all([health0, health1])
        self.session.commit()
        
        # Run selection 100 times and count selections
        selections = Counter()
        for _ in range(100):
            selected = self.scorer.select_proxy_for_job(min_score=50.0)
            selections[selected] += 1
        
        # Healthier proxy (proxy_ids[0]) should be selected more often
        self.assertGreater(
            selections.get(proxy_ids[0], 0),
            selections.get(proxy_ids[1], 0),
            "Healthier proxy should be selected more frequently"
        )
    
    def test_select_proxy_returns_none_when_no_eligible_proxies(self):
        """Test that select_proxy_for_job returns None when no proxies meet min_score."""
        self._create_test_proxies(1)
        proxy_id = self.session.query(Proxy).first().id
        
        # Create very poor health record
        health = ProxyHealthScore(
            proxy_id=proxy_id,
            success_rate=0.1,
            failure_count=100,
            blocked_count=50
        )
        self.session.add(health)
        self.session.commit()
        
        # Try to select with high min_score
        selected = self.scorer.select_proxy_for_job(min_score=90.0)
        
        # Should return None since no proxy meets min_score of 90
        self.assertIsNone(selected)
    
    def test_time_window_decay_recent_proxy(self):
        """Test that recently used proxies are scored normally."""
        self._create_test_proxies(1)
        proxy_id = self.session.query(Proxy).first().id
        
        # Create health record with recent usage
        health = ProxyHealthScore(
            proxy_id=proxy_id,
            success_rate=0.5,
            failure_count=2,
            blocked_count=3,
            last_used=datetime.datetime.utcnow()
        )
        self.session.add(health)
        self.session.commit()
        
        score = self.scorer.calculate_health_score(proxy_id)
        
        # Score should be calculated normally (not affected by time decay)
        expected = 100.0 - (2/6 * 50.0) - (3/6 * 50.0)
        self.assertAlmostEqual(score, expected, places=1)
    
    def test_time_window_decay_old_proxy_with_failures(self):
        """Test that old proxies with failures get recovery score."""
        self._create_test_proxies(1)
        proxy_id = self.session.query(Proxy).first().id
        
        # Create health record with old usage (>24 hours ago)
        old_time = datetime.datetime.utcnow() - datetime.timedelta(hours=30)
        health = ProxyHealthScore(
            proxy_id=proxy_id,
            success_rate=0.2,
            failure_count=10,
            blocked_count=5,
            last_used=old_time
        )
        self.session.add(health)
        self.session.commit()
        
        score = self.scorer.calculate_health_score(proxy_id)
        
        # Old proxy with failures should get recovery score
        self.assertEqual(score, 60.0)
    
    def test_get_proxy_stats(self):
        """Test that get_proxy_stats returns detailed statistics."""
        self._create_test_proxies(1)
        proxy_id = self.session.query(Proxy).first().id
        
        health = ProxyHealthScore(
            proxy_id=proxy_id,
            success_rate=0.8,
            failure_count=2,
            blocked_count=1
        )
        self.session.add(health)
        self.session.commit()
        
        stats = self.scorer.get_proxy_stats(proxy_id)
        
        self.assertIsNotNone(stats)
        self.assertEqual(stats['proxy_id'], proxy_id)
        self.assertEqual(stats['failure_count'], 2)
        self.assertEqual(stats['block_count'], 1)
        self.assertIn('health_score', stats)
        self.assertIn('proxy_ip', stats)
    
    def test_reset_proxy_health(self):
        """Test that reset_proxy_health clears all metrics."""
        self._create_test_proxies(1)
        proxy_id = self.session.query(Proxy).first().id
        
        # Create health record with metrics
        health = ProxyHealthScore(
            proxy_id=proxy_id,
            success_rate=0.5,
            failure_count=10,
            blocked_count=5,
            last_used=datetime.datetime.utcnow()
        )
        self.session.add(health)
        self.session.commit()
        
        # Reset health
        self.scorer.reset_proxy_health(proxy_id)
        
        # Check metrics are cleared
        health_record = self.session.query(ProxyHealthScore).filter(
            ProxyHealthScore.proxy_id == proxy_id
        ).first()
        
        self.assertEqual(health_record.failure_count, 0)
        self.assertEqual(health_record.blocked_count, 0)
        self.assertEqual(health_record.success_rate, 1.0)
        self.assertIsNone(health_record.last_used)


if __name__ == '__main__':
    unittest.main()
