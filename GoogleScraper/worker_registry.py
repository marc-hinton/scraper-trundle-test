# -*- coding: utf-8 -*-

"""
Worker registration and heartbeat health monitoring system.

This module provides functionality for remote workers to register themselves,
periodically send heartbeats to indicate they are alive, and report their current
resource utilization (CPU, memory, concurrent job slots available).

The system automatically marks workers as inactive after a configurable timeout
period without a heartbeat, ensuring the orchestrator has an accurate view of
available worker capacity and can detect failures early.
"""

import logging
import threading
import datetime
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy import and_
from GoogleScraper.database import Worker, get_session

logger = logging.getLogger(__name__)


class WorkerRegistry:
    """Manages worker registration, heartbeat monitoring, and health checks.
    
    This class provides centralized management of remote workers, including
    registration, heartbeat updates, and automatic detection of inactive workers
    through periodic health checks.
    """

    def __init__(self, config):
        """Initialize the WorkerRegistry.
        
        Args:
            config: Configuration dictionary containing heartbeat settings.
                Expected keys:
                - heartbeat_interval: Time in seconds between worker heartbeats (default 30)
                - heartbeat_timeout: Time in seconds to mark worker inactive (default 120)
        """
        self.config = config
        self.heartbeat_interval = config.get('heartbeat_interval', 30)
        self.heartbeat_timeout = config.get('heartbeat_timeout', 120)
        self.health_check_thread = None
        self.running = False
        
        logger.info(
            f'WorkerRegistry initialized with heartbeat_interval={self.heartbeat_interval}s, '
            f'heartbeat_timeout={self.heartbeat_timeout}s'
        )

    def start_health_check(self):
        """Start the background health check thread.
        
        The health check runs periodically every heartbeat_interval seconds
        and marks workers as inactive if they haven't sent a heartbeat
        within the heartbeat_timeout period.
        """
        if self.running:
            logger.warning('Health check thread already running')
            return

        self.running = True
        self.health_check_thread = threading.Thread(
            target=self._health_check_loop,
            daemon=True,
            name='WorkerHealthCheck'
        )
        self.health_check_thread.start()
        logger.info('Worker health check thread started')

    def stop_health_check(self):
        """Stop the background health check thread."""
        self.running = False
        if self.health_check_thread:
            self.health_check_thread.join(timeout=5)
        logger.info('Worker health check thread stopped')

    def _health_check_loop(self):
        """Background loop that periodically checks worker health.
        
        Runs every heartbeat_interval seconds and marks workers as inactive
        if they haven't sent a heartbeat for longer than heartbeat_timeout.
        """
        while self.running:
            try:
                self.perform_health_check()
            except Exception as e:
                logger.error(f'Error during health check: {e}', exc_info=True)

            # Sleep for heartbeat_interval seconds
            threading.Event().wait(self.heartbeat_interval)

    def perform_health_check(self):
        """Perform a single health check cycle.
        
        Marks workers as inactive if their last heartbeat is older than
        the configured heartbeat_timeout.
        
        Returns:
            int: Number of workers marked as inactive.
        """
        try:
            session_factory = get_session(self.config)
            session = session_factory()
            
            try:
                # Calculate the timeout threshold
                timeout_threshold = datetime.datetime.utcnow() - datetime.timedelta(
                    seconds=self.heartbeat_timeout
                )

                # Find all active workers that haven't sent a heartbeat recently
                inactive_workers = session.query(Worker).filter(
                    and_(
                        Worker.is_active == True,
                        Worker.last_heartbeat < timeout_threshold
                    )
                ).all()

                # Mark them as inactive and log the transition
                count = 0
                for worker in inactive_workers:
                    worker.is_active = False
                    logger.warning(
                        f'Marked worker {worker.hostname} (id={worker.id}) as inactive. '
                        f'Last heartbeat: {worker.last_heartbeat}'
                    )
                    count += 1

                if count > 0:
                    session.commit()
                    logger.info(f'Health check marked {count} worker(s) as inactive')

                return count
            finally:
                session.close()

        except Exception as e:
            logger.error(f'Error during worker health check: {e}', exc_info=True)
            return 0

    def register_worker(self, hostname, worker_type, concurrent_capacity):
        """Register a new worker or return existing one.
        
        Creates a new Worker entry in the database with the provided information.
        If a worker with the same hostname already exists, it is returned with
        is_active=True.
        
        Args:
            hostname (str): Unique identifier for the worker (e.g., hostname or IP)
            worker_type (str): Type of worker ('selenium', 'http', or 'puppeteer')
            concurrent_capacity (int): Number of concurrent jobs the worker can handle
            
        Returns:
            Worker: The registered worker object
            
        Raises:
            ValueError: If required parameters are invalid
            Exception: On database errors
        """
        if not hostname or not isinstance(hostname, str):
            raise ValueError('hostname must be a non-empty string')
        if worker_type not in ('selenium', 'http', 'puppeteer'):
            raise ValueError(f'worker_type must be one of: selenium, http, puppeteer')
        if not isinstance(concurrent_capacity, int) or concurrent_capacity < 1:
            raise ValueError('concurrent_capacity must be a positive integer')

        try:
            session_factory = get_session(self.config)
            session = session_factory()

            try:
                # Check if worker already exists
                existing_worker = session.query(Worker).filter(
                    Worker.hostname == hostname
                ).first()

                if existing_worker:
                    existing_worker.is_active = True
                    existing_worker.last_heartbeat = datetime.datetime.utcnow()
                    session.commit()
                    logger.info(
                        f'Worker {hostname} re-registered (id={existing_worker.id}). '
                        f'Marked as active.'
                    )
                    return existing_worker

                # Create new worker
                now = datetime.datetime.utcnow()
                new_worker = Worker(
                    hostname=hostname,
                    worker_type=worker_type,
                    concurrent_job_capacity=concurrent_capacity,
                    registration_time=now,
                    last_heartbeat=now,
                    is_active=True
                )
                session.add(new_worker)
                session.commit()

                logger.info(
                    f'Registered new worker {hostname} (id={new_worker.id}) '
                    f'with type={worker_type}, capacity={concurrent_capacity}'
                )

                return new_worker

            finally:
                session.close()

        except ValueError:
            raise
        except Exception as e:
            logger.error(f'Error registering worker {hostname}: {e}', exc_info=True)
            raise

    def heartbeat(self, worker_id, current_job_count=0, cpu_usage=0.0, memory_usage=0.0):
        """Update a worker's heartbeat and resource utilization metrics.

        This method should be called frequently by workers to indicate they are
        alive and provide current resource usage information. Uses a single UPDATE
        query to minimize database load.

        Args:
            worker_id (int): ID of the worker to update
            current_job_count (int): Number of jobs currently running on the worker
            cpu_usage (float): Current CPU usage percentage (0-100)
            memory_usage (float): Current memory usage percentage (0-100)

        Returns:
            bool: True if heartbeat was successful, False if worker not found

        Raises:
            ValueError: If parameters are invalid
            Exception: On database errors
        """
        if not isinstance(worker_id, int) or worker_id < 1:
            raise ValueError('worker_id must be a positive integer')
        if not isinstance(current_job_count, int) or current_job_count < 0:
            raise ValueError('current_job_count must be a non-negative integer')
        if not isinstance(cpu_usage, (int, float)) or not (0 <= cpu_usage <= 100):
            raise ValueError('cpu_usage must be between 0 and 100')
        if not isinstance(memory_usage, (int, float)) or not (0 <= memory_usage <= 100):
            raise ValueError('memory_usage must be between 0 and 100')

        try:
            session_factory = get_session(self.config)
            session = session_factory()

            try:
                # Use a single UPDATE query for efficiency
                worker = session.query(Worker).filter(
                    Worker.id == worker_id
                ).first()

                if not worker:
                    logger.warning(f'Heartbeat received for unknown worker (id={worker_id})')
                    return False

                # Update all metrics in a single operation
                worker.last_heartbeat = datetime.datetime.utcnow()
                worker.current_job_count = current_job_count
                worker.cpu_usage = cpu_usage
                worker.memory_usage = memory_usage
                session.commit()

                logger.debug(
                    f'Heartbeat received from worker {worker.hostname} (id={worker_id}): '
                    f'jobs={current_job_count}, cpu={cpu_usage}%, mem={memory_usage}%'
                )

                return True

            finally:
                session.close()

        except ValueError:
            raise
        except Exception as e:
            logger.error(f'Error processing heartbeat for worker {worker_id}: {e}', exc_info=True)
            raise

    def get_active_workers(self):
        """Get all currently active workers.
        
        Returns:
            list: List of active Worker objects
        """
        try:
            session_factory = get_session(self.config)
            session = session_factory()

            try:
                workers = session.query(Worker).filter(
                    Worker.is_active == True
                ).all()
                return workers
            finally:
                session.close()

        except Exception as e:
            logger.error(f'Error retrieving active workers: {e}', exc_info=True)
            return []

    def get_worker_by_id(self, worker_id):
        """Get a worker by ID.
        
        Args:
            worker_id (int): ID of the worker to retrieve
            
        Returns:
            Worker or None: The worker object if found, None otherwise
        """
        try:
            session_factory = get_session(self.config)
            session = session_factory()

            try:
                worker = session.query(Worker).filter(
                    Worker.id == worker_id
                ).first()
                return worker
            finally:
                session.close()

        except Exception as e:
            logger.error(f'Error retrieving worker {worker_id}: {e}', exc_info=True)
            return None

    def get_worker_by_hostname(self, hostname):
        """Get a worker by hostname.
        
        Args:
            hostname (str): Hostname of the worker to retrieve
            
        Returns:
            Worker or None: The worker object if found, None otherwise
        """
        try:
            session_factory = get_session(self.config)
            session = session_factory()

            try:
                worker = session.query(Worker).filter(
                    Worker.hostname == hostname
                ).first()
                return worker
            finally:
                session.close()

        except Exception as e:
            logger.error(f'Error retrieving worker {hostname}: {e}', exc_info=True)
            return None
