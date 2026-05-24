# -*- coding: utf-8 -*-

"""
Worker registry and heartbeat health monitoring system.

This module provides a WorkerRegistry class that allows remote workers to:
1. Register themselves with the system
2. Send periodic heartbeats to indicate they are alive
3. Report their current resource utilization (CPU, memory, job slots)

The system automatically marks workers as inactive after a configurable timeout
period without a heartbeat, ensuring the orchestrator has an accurate view of
available worker capacity and can detect failures early.
"""

import datetime
import threading
import logging
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.exc import SQLAlchemyError
from GoogleScraper.database import Worker, get_session

logger = logging.getLogger(__name__)


class WorkerRegistry:
    """Manages worker registration and health monitoring."""

    def __init__(self, config, engine=None):
        """Initialize the WorkerRegistry.

        Args:
            config: Configuration dictionary with heartbeat settings
            engine: Optional SQLAlchemy engine. If not provided, one will be created from config.
        """
        self.config = config
        self.engine = engine
        self.session_factory = get_session(config, scoped=False, engine=engine)

        # Get heartbeat configuration
        self.heartbeat_interval = config.get('heartbeat_interval', 30)
        self.heartbeat_timeout = config.get('heartbeat_timeout', 120)
        self.enable_health_check = config.get('enable_worker_health_check', True)

        # Health check thread
        self._health_check_thread = None
        self._stop_health_check = False

        # Start background health check process if enabled
        if self.enable_health_check:
            self._start_health_check()

    def _get_session(self):
        """Get a new database session."""
        return self.session_factory()

    def register_worker(self, hostname, worker_type, concurrent_capacity):
        """Register a new worker in the system.

        Args:
            hostname: Worker hostname/identifier
            worker_type: Type of worker (e.g., 'scraper', 'proxy_checker')
            concurrent_capacity: Number of concurrent jobs this worker can handle

        Returns:
            worker_id: The ID of the registered worker

        Raises:
            Exception: If database operation fails
        """
        session = self._get_session()
        try:
            # Check if worker already exists
            existing_worker = session.query(Worker).filter(Worker.hostname == hostname).first()
            if existing_worker:
                # Worker already registered, reactivate if inactive
                if not existing_worker.is_active:
                    existing_worker.is_active = True
                    existing_worker.last_heartbeat = datetime.datetime.utcnow()
                    session.commit()
                    logger.info('Worker %s came back online (reactivated)', hostname)
                return existing_worker.id

            # Create new worker
            worker = Worker(
                hostname=hostname,
                worker_type=worker_type,
                concurrent_capacity=concurrent_capacity,
                is_active=True,
                last_heartbeat=datetime.datetime.utcnow()
            )
            session.add(worker)
            session.commit()
            worker_id = worker.id
            logger.info('Worker registered: %s (id=%d, type=%s, capacity=%d)',
                       hostname, worker_id, worker_type, concurrent_capacity)
            return worker_id

        except SQLAlchemyError as e:
            session.rollback()
            logger.error('Database error while registering worker %s: %s', hostname, str(e))
            raise
        finally:
            session.close()

    def heartbeat(self, worker_id, current_job_count=0, cpu_usage='0', memory_usage='0'):
        """Update worker heartbeat and utilization metrics.

        This method should be called periodically by workers to indicate they are alive.

        Args:
            worker_id: The ID of the worker
            current_job_count: Number of jobs currently being processed
            cpu_usage: CPU usage as a string percentage (e.g., "45.2")
            memory_usage: Memory usage as a string percentage (e.g., "62.1")

        Returns:
            True if heartbeat was successfully recorded, False otherwise

        Raises:
            Exception: If database operation fails
        """
        session = self._get_session()
        try:
            worker = session.query(Worker).filter(Worker.id == worker_id).first()
            if not worker:
                logger.warning('Heartbeat from unknown worker_id: %d', worker_id)
                return False

            # Update heartbeat and metrics
            worker.last_heartbeat = datetime.datetime.utcnow()
            worker.current_job_count = current_job_count
            worker.cpu_usage = str(cpu_usage)
            worker.memory_usage = str(memory_usage)

            # Reactivate worker if it was marked inactive
            if not worker.is_active:
                worker.is_active = True
                logger.info('Worker %s came back online', worker.hostname)

            session.commit()
            return True

        except SQLAlchemyError as e:
            session.rollback()
            logger.error('Database error while recording heartbeat for worker_id %d: %s', worker_id, str(e))
            raise
        finally:
            session.close()

    def get_worker_status(self, worker_id):
        """Get the current status of a worker.

        Args:
            worker_id: The ID of the worker

        Returns:
            Worker object if found, None otherwise
        """
        session = self._get_session()
        try:
            worker = session.query(Worker).filter(Worker.id == worker_id).first()
            return worker
        finally:
            session.close()

    def get_all_active_workers(self):
        """Get all currently active workers.

        Returns:
            List of active Worker objects
        """
        session = self._get_session()
        try:
            workers = session.query(Worker).filter(Worker.is_active == True).all()
            return workers
        finally:
            session.close()

    def _start_health_check(self):
        """Start the background health check thread."""
        self._stop_health_check = False
        self._health_check_thread = threading.Thread(
            target=self._health_check_loop,
            daemon=True,
            name='WorkerHealthCheck'
        )
        self._health_check_thread.start()
        logger.info('Worker health check thread started (interval=%ds, timeout=%ds)',
                   self.heartbeat_interval, self.heartbeat_timeout)

    def _health_check_loop(self):
        """Background health check loop that marks stale workers as inactive."""
        while not self._stop_health_check:
            try:
                self._perform_health_check()
            except Exception as e:
                logger.error('Error in health check loop: %s', str(e))

            # Sleep for the heartbeat interval duration
            for _ in range(int(self.heartbeat_interval)):
                if self._stop_health_check:
                    break
                threading.Event().wait(1)

    def _perform_health_check(self):
        """Check all workers and mark stale ones as inactive."""
        session = self._get_session()
        try:
            now = datetime.datetime.utcnow()
            timeout_delta = datetime.timedelta(seconds=self.heartbeat_timeout)

            # Find all active workers that haven't sent a heartbeat in timeout period
            stale_workers = session.query(Worker).filter(
                Worker.is_active == True,
                Worker.last_heartbeat < (now - timeout_delta)
            ).all()

            for worker in stale_workers:
                worker.is_active = False
                logger.warning('Worker %s marked as inactive (no heartbeat for %d seconds)',
                             worker.hostname, self.heartbeat_timeout)

            if stale_workers:
                session.commit()

        except SQLAlchemyError as e:
            logger.error('Database error in health check: %s', str(e))
            session.rollback()
        finally:
            session.close()

    def stop_health_check(self):
        """Stop the background health check thread."""
        self._stop_health_check = True
        if self._health_check_thread and self._health_check_thread.is_alive():
            self._health_check_thread.join(timeout=5)
            logger.info('Worker health check thread stopped')

    def __del__(self):
        """Cleanup: stop health check thread on deletion."""
        self.stop_health_check()
