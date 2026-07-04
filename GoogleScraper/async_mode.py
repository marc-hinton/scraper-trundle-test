# -*- coding: utf-8 -*-
"""Async scraping entry point.

This module used to embed a synchronous scrape/parse/cache loop
directly.  It has been refactored to delegate all of the heavy
lifting to :mod:`GoogleScraper.pipeline`, keeping only the
integration glue that ties the pipeline to the rest of
GoogleScraper (cache manager, database session, output converter).
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any, Callable, Dict, Iterable, List, Optional

from GoogleScraper.parsing import parse_serp
from GoogleScraper.output_converter import store_serp_result
from GoogleScraper.pipeline import (
    PipelineCoordinator,
    ParsedResult,
    StorageStage,
    plugin_registry,
)

logger = logging.getLogger(__name__)


class _IntegratedStorage(StorageStage):
    """Storage stage that talks to cache/db/output converter."""

    def __init__(self, in_queue, config, cache_manager=None, session=None,
                 scraper_search=None, db_lock=None, worker_count: int = 1):
        super().__init__(in_queue, worker_count=worker_count)
        self.config = config
        self.cache_manager = cache_manager
        self.session = session
        self.scraper_search = scraper_search
        self.db_lock = db_lock

    async def store(self, item: ParsedResult) -> None:
        fetch = item.fetch
        parser = item.parser
        job = fetch.job
        query = job.get('query', '')
        engine = job.get('search_engine') or fetch.plugin.name
        scrape_method = job.get('scrape_method', 'http-async')
        page_number = job.get('page_number', 1)

        # Build a lightweight scraper stand-in with the attributes
        # ``parse_serp`` and downstream helpers expect.
        scraper = type('AsyncScrape', (), {})()
        scraper.query = query
        scraper.search_engine_name = engine
        scraper.scrape_method = scrape_method
        scraper.page_number = page_number
        scraper.requested_at = fetch.requested_at
        scraper.requested_by = 'localhost'
        scraper.status = ('successful' if fetch.status == 200
                          else 'not successful: {}'.format(fetch.status))
        scraper.parser = parser

        if self.cache_manager:
            try:
                self.cache_manager.cache_results(
                    parser, query, engine, scrape_method, page_number)
            except Exception:  # pragma: no cover
                logger.exception('Cache write failed')

        try:
            serp = parse_serp(self.config, parser=parser,
                              scraper=scraper, query=query)
        except Exception:  # pragma: no cover
            logger.exception('parse_serp failed')
            return

        if self.scraper_search is not None:
            self.scraper_search.serps.append(serp)

        if self.session is not None:
            def _persist():
                self.session.add(serp)
                self.session.commit()
            if self.db_lock is not None:
                with self.db_lock:
                    _persist()
            else:
                _persist()

        try:
            store_serp_result(serp, self.config)
        except Exception:  # pragma: no cover
            logger.exception('store_serp_result failed')


class AsyncScrapeScheduler(object):
    """Backwards-compatible async scheduler backed by the plugin pipeline.

    The public API (``run``) matches the historical scheduler so that
    :mod:`GoogleScraper.core` keeps working without changes.
    """

    def __init__(self, config: Dict[str, Any],
                 scrape_jobs: Iterable[Dict[str, Any]],
                 cache_manager=None, session=None,
                 scraper_search=None, db_lock=None) -> None:
        self.config = config
        # Materialise the iterable so we can feed it multiple times.
        self.scrape_jobs: List[Dict[str, Any]] = list(scrape_jobs)
        self.cache_manager = cache_manager
        self.session = session
        self.scraper_search = scraper_search
        self.db_lock = db_lock

        self.coordinator = PipelineCoordinator(
            config=config, registry=plugin_registry,
        )
        # Swap in the integrated storage stage.
        self.coordinator.storage = _IntegratedStorage(
            self.coordinator.store_queue,
            config=config,
            cache_manager=cache_manager,
            session=session,
            scraper_search=scraper_search,
            db_lock=db_lock,
            worker_count=self.coordinator.storage_workers,
        )

    def run(self) -> None:
        self.coordinator.run(self.scrape_jobs)


__all__ = ['AsyncScrapeScheduler']
