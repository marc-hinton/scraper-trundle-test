# -*- coding: utf-8 -*-
"""Coordinator that wires the async pipeline together."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from GoogleScraper.pipeline.plugin import (
    PluginRegistry,
    plugin_registry as default_registry,
)
from GoogleScraper.pipeline.stages import (
    FetcherStage,
    ParsedResult,
    ParserStage,
    STOP,
    StorageStage,
    InMemoryStorageStage,
    resolve_plugin,
)

logger = logging.getLogger(__name__)


class PipelineCoordinator:
    """Schedule scrape jobs through a fetch → parse → store pipeline.

    Parameters
    ----------
    config:
        A configuration dictionary.  The following keys are honoured
        (all optional):

        * ``max_concurrent_requests`` – fetcher worker count.
        * ``parser_workers``          – parser worker count.
        * ``storage_workers``         – storage worker count.
        * ``per_host_limit``          – default per-host concurrency
          limit.
        * ``per_host_limits``         – dict mapping ``host -> int``.
        * ``fetch_queue_size``        – bound of the fetch input queue.
        * ``parse_queue_size``        – bound of the parse queue.
        * ``store_queue_size``        – bound of the storage queue.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None,
                 registry: Optional[PluginRegistry] = None,
                 storage_stage: Optional[StorageStage] = None,
                 fetch_func: Optional[Callable[[str, Dict[str, str]],
                                               Awaitable[Any]]] = None,
                 store_func: Optional[Callable[[ParsedResult],
                                               Awaitable[None]]] = None
                 ) -> None:
        self.config = config or {}
        self.registry = registry or default_registry

        self.fetcher_workers = int(
            self.config.get('max_concurrent_requests', 4))
        self.parser_workers = int(self.config.get('parser_workers', 2))
        self.storage_workers = int(self.config.get('storage_workers', 1))
        self.per_host_limit = int(self.config.get('per_host_limit', 2))
        self.per_host_limits = dict(self.config.get('per_host_limits', {}))

        self.fetch_queue: asyncio.Queue = asyncio.Queue(
            maxsize=int(self.config.get('fetch_queue_size', 32)))
        self.parse_queue: asyncio.Queue = asyncio.Queue(
            maxsize=int(self.config.get('parse_queue_size', 32)))
        self.store_queue: asyncio.Queue = asyncio.Queue(
            maxsize=int(self.config.get('store_queue_size', 32)))

        self.fetcher = FetcherStage(
            self.fetch_queue, self.parse_queue,
            worker_count=self.fetcher_workers,
            per_host_limit=self.per_host_limit,
            host_limits=self.per_host_limits,
            fetch_func=fetch_func,
        )
        self.parser = ParserStage(
            self.parse_queue, self.store_queue,
            worker_count=self.parser_workers,
        )
        if storage_stage is None:
            self.storage: StorageStage = InMemoryStorageStage(
                self.store_queue, worker_count=self.storage_workers)
            if store_func is not None:
                self.storage._store_func = store_func  # type: ignore[attr-defined]
        else:
            self.storage = storage_stage

    # ---------------------------------------------------------------- public
    def prepare_jobs(self, scrape_jobs: Iterable[Dict[str, Any]]
                     ) -> List[Dict[str, Any]]:
        """Bind each scrape job to a resolved plugin instance."""
        prepared: List[Dict[str, Any]] = []
        for job in scrape_jobs:
            name = job.get('search_engine') or job.get('search_engine_name')
            if not name:
                raise ValueError(
                    'scrape job is missing a search_engine key: {!r}'.format(
                        job))
            plugin = resolve_plugin(name, self.config, registry=self.registry)
            new_job = dict(job)
            new_job['plugin'] = plugin
            prepared.append(new_job)
        return prepared

    async def run_async(self, scrape_jobs: Iterable[Dict[str, Any]]) -> None:
        prepared = self.prepare_jobs(scrape_jobs)

        await self.fetcher.start()
        await self.parser.start()
        await self.storage.start()

        # Feed jobs -- back-pressure comes from the bounded queue.
        for job in prepared:
            await self.fetch_queue.put(job)

        # Wait for fetch queue to drain before signalling shutdown.
        await self.fetch_queue.join()
        for _ in range(self.fetcher.worker_count):
            await self.fetch_queue.put(STOP)
        await self.fetcher.join()

        await self.parse_queue.join()
        for _ in range(self.parser.worker_count):
            await self.parse_queue.put(STOP)
        await self.parser.join()

        await self.store_queue.join()
        for _ in range(self.storage.worker_count):
            await self.store_queue.put(STOP)
        await self.storage.join()

    def run(self, scrape_jobs: Iterable[Dict[str, Any]]) -> None:
        """Synchronous wrapper used by non-async callers."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError('event loop is closed')
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_until_complete(self.run_async(scrape_jobs))

    # ------------------------------------------------------------------- api
    @property
    def results(self) -> List[ParsedResult]:
        """Convenience shortcut for the in-memory storage backend."""
        if isinstance(self.storage, InMemoryStorageStage):
            return self.storage.results
        return []
