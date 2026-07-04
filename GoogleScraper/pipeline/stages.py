# -*- coding: utf-8 -*-
"""Async stages used by the pipeline coordinator.

Each stage is intentionally independent: it consumes work from an
input :class:`asyncio.Queue` and pushes results onto an output
queue.  Because the queues are bounded, slow stages naturally apply
back-pressure to their producers.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from GoogleScraper.pipeline.plugin import (
    PluginRegistry,
    SearchEnginePlugin,
    plugin_registry as default_registry,
)

logger = logging.getLogger(__name__)

# Sentinel pushed onto queues to signal graceful shutdown.
_STOP = object()


# ---------------------------------------------------------------------- types
@dataclass
class FetchResult:
    """A single response emitted by :class:`FetcherStage`."""

    job: Dict[str, Any]
    plugin: SearchEnginePlugin
    status: int
    body: Optional[str]
    url: str
    requested_at: datetime.datetime = field(
        default_factory=datetime.datetime.utcnow)
    error: Optional[BaseException] = None


@dataclass
class ParsedResult:
    """A parsed page emitted by :class:`ParserStage`."""

    fetch: FetchResult
    parser: Any


# ---------------------------------------------------------------------- base
class _Stage:
    """Base class that runs ``worker_count`` async workers.

    Each worker reads from ``in_queue`` until it observes the sentinel,
    processes the item and forwards the result to ``out_queue`` (when
    provided).  The stage guarantees that exactly ``worker_count``
    sentinels are propagated to the downstream queue on shutdown.
    """

    def __init__(self, in_queue: asyncio.Queue,
                 out_queue: Optional[asyncio.Queue] = None,
                 worker_count: int = 1,
                 name: str = 'stage') -> None:
        self.in_queue = in_queue
        self.out_queue = out_queue
        self.worker_count = max(1, int(worker_count))
        self.name = name
        self._tasks: List[asyncio.Task] = []

    async def start(self) -> None:
        loop = asyncio.get_event_loop()
        self._tasks = [
            loop.create_task(self._worker(i)) for i in range(self.worker_count)
        ]

    async def join(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _worker(self, idx: int) -> None:
        while True:
            item = await self.in_queue.get()
            try:
                if item is _STOP:
                    # Only the last worker propagates a single sentinel
                    # down to the next stage - we simply let the
                    # coordinator emit ``worker_count`` sentinels for
                    # the downstream stage explicitly.
                    return
                await self._process(item)
            except Exception:  # pragma: no cover - defensive
                logger.exception('%s worker %d crashed', self.name, idx)
            finally:
                self.in_queue.task_done()

    async def _process(self, item: Any) -> None:  # pragma: no cover
        raise NotImplementedError


# ------------------------------------------------------------------- fetcher
class FetcherStage(_Stage):
    """Downloads pages, honouring per-host concurrency limits."""

    def __init__(self, in_queue: asyncio.Queue,
                 out_queue: asyncio.Queue,
                 worker_count: int = 4,
                 per_host_limit: int = 2,
                 fetch_func: Optional[Callable[[str, Dict[str, str]],
                                               Awaitable[Any]]] = None,
                 host_limits: Optional[Dict[str, int]] = None) -> None:
        super().__init__(in_queue, out_queue, worker_count=worker_count,
                         name='fetcher')
        self.per_host_limit = max(1, int(per_host_limit))
        self._host_limits = host_limits or {}
        self._host_semaphores: Dict[str, asyncio.Semaphore] = {}
        self._fetch_func = fetch_func

    def _semaphore_for(self, host: str) -> asyncio.Semaphore:
        sem = self._host_semaphores.get(host)
        if sem is None:
            limit = self._host_limits.get(host, self.per_host_limit)
            sem = asyncio.Semaphore(limit)
            self._host_semaphores[host] = sem
        return sem

    async def _process(self, job: Dict[str, Any]) -> None:
        plugin: SearchEnginePlugin = job['plugin']
        url = plugin.build_url(
            query=job.get('query', ''),
            page_number=job.get('page_number', 1),
            num_results_per_page=job.get('num_results_per_page', 10),
            search_type=job.get('search_type', 'normal'),
        )
        headers = plugin.build_headers()
        host = plugin.host()

        sem = self._semaphore_for(host)
        async with sem:
            status, body, error = await self._do_fetch(url, headers)

        result = FetchResult(
            job=job, plugin=plugin, status=status,
            body=body, url=url, error=error,
        )
        if self.out_queue is not None:
            await self.out_queue.put(result)

    async def _do_fetch(self, url: str, headers: Dict[str, str]):
        if self._fetch_func is not None:
            try:
                status, body = await self._fetch_func(url, headers)
                return status, body, None
            except Exception as exc:
                return 0, None, exc
        # Fall back to aiohttp when available.
        try:
            import aiohttp  # type: ignore
        except ImportError:  # pragma: no cover - aiohttp is a dep
            return 0, None, RuntimeError('aiohttp is required to fetch pages')

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    body = await response.text()
                    return response.status, body, None
        except Exception as exc:
            return 0, None, exc


# -------------------------------------------------------------------- parser
class ParserStage(_Stage):
    """Turns fetched HTML into parser objects."""

    def __init__(self, in_queue: asyncio.Queue,
                 out_queue: asyncio.Queue,
                 worker_count: int = 2) -> None:
        super().__init__(in_queue, out_queue, worker_count=worker_count,
                         name='parser')

    async def _process(self, item: FetchResult) -> None:
        if item.error is not None or item.status != 200 or item.body is None:
            logger.info(
                'Skipping parser for %s (status=%s, error=%r)',
                item.url, item.status, item.error)
            return
        try:
            parser = item.plugin.parse(item.body)
        except Exception:
            logger.exception('Parser failed for %s', item.url)
            return
        if self.out_queue is not None:
            await self.out_queue.put(ParsedResult(fetch=item, parser=parser))


# ------------------------------------------------------------------- storage
class StorageStage(_Stage):
    """Base storage stage.  Sub-classes override :meth:`store`."""

    def __init__(self, in_queue: asyncio.Queue,
                 worker_count: int = 1,
                 store_func: Optional[Callable[[ParsedResult],
                                               Awaitable[None]]] = None
                 ) -> None:
        super().__init__(in_queue, out_queue=None,
                         worker_count=worker_count, name='storage')
        self._store_func = store_func

    async def _process(self, item: ParsedResult) -> None:
        if self._store_func is not None:
            maybe = self._store_func(item)
            if asyncio.iscoroutine(maybe):
                await maybe
            return
        await self.store(item)

    async def store(self, item: ParsedResult) -> None:  # pragma: no cover
        raise NotImplementedError


class InMemoryStorageStage(StorageStage):
    """Simple storage backend that just collects results in a list."""

    def __init__(self, in_queue: asyncio.Queue,
                 worker_count: int = 1) -> None:
        super().__init__(in_queue, worker_count=worker_count)
        self.results: List[ParsedResult] = []
        self._lock = asyncio.Lock()

    async def store(self, item: ParsedResult) -> None:
        async with self._lock:
            self.results.append(item)


def resolve_plugin(name: str, config: Dict[str, Any],
                   registry: Optional[PluginRegistry] = None
                   ) -> SearchEnginePlugin:
    """Instantiate a plugin by name using the given registry."""
    reg = registry or default_registry
    return reg.create(name, config=config)


# Sentinel exported for coordinator use.
STOP = _STOP
