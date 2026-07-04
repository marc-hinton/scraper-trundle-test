#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""Unit tests for the async plugin pipeline."""

import asyncio
import unittest

from GoogleScraper.pipeline import (
    FetchResult,
    FetcherStage,
    InMemoryStorageStage,
    ParsedResult,
    ParserStage,
    PipelineCoordinator,
    PluginRegistry,
    SearchEnginePlugin,
    plugin_registry,
    register_plugin,
)
from GoogleScraper.pipeline.stages import STOP


def _run(coro):
    """Helper: run a coroutine on a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------- fake plugin
class _FakeParser(object):
    def __init__(self, config=None, html=''):
        self.config = config
        self.html = html
        self.search_results = {'results': [{'title': 'ok'}]}


class _FakeParserFailing(object):
    def __init__(self, config=None, html=''):
        raise RuntimeError('boom')


class _FakePlugin(SearchEnginePlugin):
    name = 'fake'
    base_url = 'https://fake.example.com/search'
    parser_cls = _FakeParser

    def build_params(self, query, page_number=1,
                     num_results_per_page=10, search_type='normal'):
        return {'q': query, 'p': page_number}


class _OtherHostPlugin(SearchEnginePlugin):
    name = 'other'
    base_url = 'https://other.example.com/search'
    parser_cls = _FakeParser

    def build_params(self, query, page_number=1,
                     num_results_per_page=10, search_type='normal'):
        return {'q': query}


# ==================================================================== tests
class PluginRegistryTests(unittest.TestCase):
    def test_register_and_lookup(self):
        reg = PluginRegistry()
        reg.register(_FakePlugin)
        self.assertIn('fake', reg)
        self.assertIs(reg.get('fake'), _FakePlugin)
        self.assertIsInstance(reg.create('fake'), _FakePlugin)

    def test_register_plugin_decorator(self):
        reg = PluginRegistry()

        @register_plugin(registry=reg)
        class Dummy(SearchEnginePlugin):
            name = 'dummy'
            base_url = 'https://dummy.example.com/'
            parser_cls = _FakeParser

        self.assertIn('dummy', reg)

    def test_register_rejects_non_plugin(self):
        reg = PluginRegistry()
        with self.assertRaises(TypeError):
            reg.register(object)  # type: ignore[arg-type]

    def test_get_unknown_raises(self):
        reg = PluginRegistry()
        with self.assertRaises(KeyError):
            reg.get('nope')

    def test_builtin_engines_registered(self):
        for name in ('google', 'bing', 'yahoo', 'yandex',
                     'duckduckgo', 'baidu', 'ask'):
            self.assertIn(name, plugin_registry, name)


class PluginBehaviourTests(unittest.TestCase):
    def test_build_url_includes_params(self):
        plugin = _FakePlugin()
        url = plugin.build_url(query='hello', page_number=2)
        self.assertTrue(url.startswith('https://fake.example.com/search?'))
        self.assertIn('q=hello', url)
        self.assertIn('p=2', url)

    def test_host_uses_url_netloc(self):
        self.assertEqual(_FakePlugin().host(), 'fake.example.com')

    def test_parse_uses_parser_cls(self):
        parser = _FakePlugin().parse('<html/>')
        self.assertIsInstance(parser, _FakeParser)
        self.assertEqual(parser.html, '<html/>')


# --------------------------------------------------------------- stage tests
class FetcherStagePerHostLimitTests(unittest.TestCase):
    def test_per_host_semaphore_limits_concurrency(self):
        active = {'fake.example.com': 0, 'other.example.com': 0}
        peak = {'fake.example.com': 0, 'other.example.com': 0}

        async def scenario():
            async def fake_fetch(url, headers):
                # Which host was requested?
                host = ('fake.example.com' if 'fake.example.com' in url
                        else 'other.example.com')
                active[host] += 1
                peak[host] = max(peak[host], active[host])
                await asyncio.sleep(0.05)
                active[host] -= 1
                return 200, '<html/>'

            in_q: asyncio.Queue = asyncio.Queue()
            out_q: asyncio.Queue = asyncio.Queue()
            fetcher = FetcherStage(in_q, out_q, worker_count=6,
                                   per_host_limit=2,
                                   fetch_func=fake_fetch)
            await fetcher.start()

            fake = _FakePlugin()
            other = _OtherHostPlugin()
            for i in range(4):
                await in_q.put({'plugin': fake, 'query': 'q{}'.format(i)})
            for i in range(4):
                await in_q.put({'plugin': other, 'query': 'q{}'.format(i)})

            await in_q.join()
            for _ in range(fetcher.worker_count):
                await in_q.put(STOP)
            await fetcher.join()

            results = []
            while not out_q.empty():
                results.append(out_q.get_nowait())
            return results

        results = _run(scenario())
        self.assertEqual(len(results), 8)
        self.assertLessEqual(peak['fake.example.com'], 2)
        self.assertLessEqual(peak['other.example.com'], 2)

    def test_fetcher_records_error(self):
        async def scenario():
            async def bad_fetch(url, headers):
                raise ConnectionError('nope')

            in_q: asyncio.Queue = asyncio.Queue()
            out_q: asyncio.Queue = asyncio.Queue()
            fetcher = FetcherStage(in_q, out_q, worker_count=1,
                                   per_host_limit=1,
                                   fetch_func=bad_fetch)
            await fetcher.start()
            await in_q.put({'plugin': _FakePlugin(), 'query': 'x'})
            await in_q.join()
            await in_q.put(STOP)
            await fetcher.join()
            return out_q.get_nowait()

        item = _run(scenario())
        self.assertIsInstance(item, FetchResult)
        self.assertIsInstance(item.error, ConnectionError)
        self.assertEqual(item.status, 0)


class ParserStageTests(unittest.TestCase):
    def test_parser_emits_parsed_result(self):
        async def scenario():
            in_q: asyncio.Queue = asyncio.Queue()
            out_q: asyncio.Queue = asyncio.Queue()
            stage = ParserStage(in_q, out_q, worker_count=1)
            await stage.start()

            fetch = FetchResult(
                job={'query': 'x'}, plugin=_FakePlugin(),
                status=200, body='<html/>',
                url='https://fake.example.com/search?q=x')
            await in_q.put(fetch)
            await in_q.join()
            await in_q.put(STOP)
            await stage.join()

            return out_q.get_nowait()

        parsed = _run(scenario())
        self.assertIsInstance(parsed, ParsedResult)
        self.assertIsInstance(parsed.parser, _FakeParser)

    def test_parser_skips_bad_status(self):
        async def scenario():
            in_q: asyncio.Queue = asyncio.Queue()
            out_q: asyncio.Queue = asyncio.Queue()
            stage = ParserStage(in_q, out_q, worker_count=1)
            await stage.start()

            fetch = FetchResult(
                job={'query': 'x'}, plugin=_FakePlugin(),
                status=500, body='oops',
                url='https://fake.example.com/search?q=x')
            await in_q.put(fetch)
            await in_q.join()
            await in_q.put(STOP)
            await stage.join()
            return out_q.qsize()

        self.assertEqual(_run(scenario()), 0)


# ------------------------------------------------------------- coordinator
class PipelineCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.reg = PluginRegistry()
        self.reg.register(_FakePlugin)
        self.reg.register(_OtherHostPlugin)

    def test_end_to_end_with_in_memory_storage(self):
        seen_urls = []

        async def fake_fetch(url, headers):
            seen_urls.append(url)
            return 200, '<html/>'

        cfg = {
            'max_concurrent_requests': 2,
            'parser_workers': 2,
            'storage_workers': 1,
            'per_host_limit': 2,
            'fetch_queue_size': 2,   # small on purpose: backpressure!
            'parse_queue_size': 2,
            'store_queue_size': 2,
        }
        coord = PipelineCoordinator(config=cfg, registry=self.reg,
                                    fetch_func=fake_fetch)
        jobs = [{'query': 'q{}'.format(i),
                 'search_engine': 'fake', 'page_number': 1}
                for i in range(6)]
        jobs += [{'query': 'q{}'.format(i),
                  'search_engine': 'other', 'page_number': 1}
                 for i in range(6)]

        _run(coord.run_async(jobs))

        # All requests reached the fetcher.
        self.assertEqual(len(seen_urls), 12)
        # All results made it through to storage.
        self.assertEqual(len(coord.results), 12)
        for item in coord.results:
            self.assertIsInstance(item, ParsedResult)
            self.assertIsInstance(item.parser, _FakeParser)

    def test_bounded_queues_provide_backpressure(self):
        """The fetch queue must never grow beyond its declared maxsize."""

        max_seen = {'v': 0}

        async def fake_fetch(url, headers):
            # Give the producer a chance to over-fill the queue.
            await asyncio.sleep(0.01)
            return 200, '<html/>'

        cfg = {
            'max_concurrent_requests': 1,
            'parser_workers': 1,
            'storage_workers': 1,
            'per_host_limit': 1,
            'fetch_queue_size': 3,
            'parse_queue_size': 3,
            'store_queue_size': 3,
        }
        coord = PipelineCoordinator(config=cfg, registry=self.reg,
                                    fetch_func=fake_fetch)
        # Monkey-patch the queue's put to observe qsize.
        original_put = coord.fetch_queue.put

        async def instrumented_put(item):
            await original_put(item)
            max_seen['v'] = max(max_seen['v'], coord.fetch_queue.qsize())

        coord.fetch_queue.put = instrumented_put  # type: ignore[assignment]

        jobs = [{'query': 'q{}'.format(i),
                 'search_engine': 'fake', 'page_number': 1}
                for i in range(20)]

        _run(coord.run_async(jobs))
        self.assertLessEqual(max_seen['v'], 3)
        self.assertEqual(len(coord.results), 20)

    def test_missing_search_engine_raises(self):
        coord = PipelineCoordinator(config={}, registry=self.reg)
        with self.assertRaises(ValueError):
            coord.prepare_jobs([{'query': 'x'}])

    def test_unknown_plugin_raises(self):
        coord = PipelineCoordinator(config={}, registry=self.reg)
        with self.assertRaises(KeyError):
            coord.prepare_jobs([{'query': 'x', 'search_engine': 'nope'}])

    def test_custom_storage_stage_is_used(self):
        seen = []

        async def store(item):
            seen.append(item)

        async def fake_fetch(url, headers):
            return 200, '<html/>'

        cfg = {'max_concurrent_requests': 1,
               'parser_workers': 1,
               'storage_workers': 1}
        coord = PipelineCoordinator(config=cfg, registry=self.reg,
                                    fetch_func=fake_fetch,
                                    store_func=store)
        jobs = [{'query': 'q', 'search_engine': 'fake', 'page_number': 1}]
        _run(coord.run_async(jobs))
        self.assertEqual(len(seen), 1)


if __name__ == '__main__':
    unittest.main()
