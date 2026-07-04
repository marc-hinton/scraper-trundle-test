#!/usr/bin/python3
# -*- coding: utf-8 -*-

"""
Unit tests for the pluggable cache backends in GoogleScraper.caching.

python -m pytest Tests/caching_tests.py
"""

import os
import shutil
import tempfile
import unittest

from GoogleScraper.caching import (
    CacheBackend,
    CacheManager,
    FileCacheBackend,
    MemoryCacheBackend,
)


class FakeParser(object):
    """Minimal stand-in for a real HTML parser used by CacheManager.cache_results()."""

    def __init__(self, html):
        self.html = html
        self.cleaned_html = html


class CacheBackendInterfaceTestCase(unittest.TestCase):
    """CacheBackend is the abstract base every concrete backend must implement."""

    def test_base_class_methods_are_not_implemented(self):
        backend = CacheBackend()
        self.assertRaises(NotImplementedError, backend.get, 'some_key')
        self.assertRaises(NotImplementedError, backend.set, 'some_key', 'value')
        self.assertRaises(NotImplementedError, backend.clear)

    def test_file_and_memory_backends_are_cache_backend_subclasses(self):
        self.assertTrue(issubclass(FileCacheBackend, CacheBackend))
        self.assertTrue(issubclass(MemoryCacheBackend, CacheBackend))


class FileCacheBackendTestCase(unittest.TestCase):
    """The file backend is the default and must keep its historical behaviour."""

    def setUp(self):
        self.cachedir = tempfile.mkdtemp(prefix='googlescraper_cache_test_')
        self.config = {
            'do_caching': True,
            'cachedir': self.cachedir,
            'minimize_caching_files': True,
            'compress_cached_files': False,
        }

    def tearDown(self):
        shutil.rmtree(self.cachedir, ignore_errors=True)

    def test_cache_manager_defaults_to_file_backend(self):
        manager = CacheManager(self.config)
        self.assertIsInstance(manager.backend, FileCacheBackend)

    def test_set_then_get_roundtrips_data_through_a_file(self):
        backend = FileCacheBackend(self.config)
        backend.set('somehash.cache', '<html>hi</html>')

        self.assertTrue(os.path.exists(os.path.join(self.cachedir, 'somehash.cache')))
        self.assertEqual(backend.get('somehash.cache'), '<html>hi</html>')

    def test_get_returns_none_for_missing_key(self):
        backend = FileCacheBackend(self.config)
        self.assertIsNone(backend.get('doesnotexist.cache'))

    def test_clear_removes_all_cached_files(self):
        backend = FileCacheBackend(self.config)
        backend.set('a.cache', 'aaa')
        backend.set('b.cache', 'bbb')
        self.assertEqual(len(os.listdir(self.cachedir)), 2)

        backend.clear()
        self.assertEqual(os.listdir(self.cachedir), [])

    def test_cache_manager_cache_results_and_get_cached_roundtrip(self):
        manager = CacheManager(self.config)
        parser = FakeParser('<html>google result</html>')

        manager.cache_results(parser, 'panama', 'google', 'http', 1)
        cached = manager.get_cached('panama', 'google', 'http', 1)

        self.assertEqual(cached, '<html>google result</html>')

    def test_cache_manager_clear_cache_empties_file_backend(self):
        manager = CacheManager(self.config)
        parser = FakeParser('<html>google result</html>')
        manager.cache_results(parser, 'panama', 'google', 'http', 1)

        manager.clear_cache()

        self.assertFalse(manager.get_cached('panama', 'google', 'http', 1))


class MemoryCacheBackendTestCase(unittest.TestCase):
    """The in-memory backend must never touch the file system."""

    def setUp(self):
        self.cachedir = tempfile.mkdtemp(prefix='googlescraper_cache_test_unused_')
        self.config = {
            'do_caching': True,
            'cachedir': self.cachedir,
            'cache_backend': 'memory',
            'minimize_caching_files': True,
        }

    def tearDown(self):
        shutil.rmtree(self.cachedir, ignore_errors=True)

    def test_cache_manager_can_be_configured_to_use_memory_backend(self):
        manager = CacheManager(self.config)
        self.assertIsInstance(manager.backend, MemoryCacheBackend)

    def test_set_then_get_roundtrips_data_without_touching_disk(self):
        backend = MemoryCacheBackend()
        backend.set('somehash.cache', '<html>hi</html>')

        self.assertEqual(backend.get('somehash.cache'), '<html>hi</html>')
        self.assertEqual(os.listdir(self.cachedir), [])

    def test_get_returns_none_for_missing_key(self):
        backend = MemoryCacheBackend()
        self.assertIsNone(backend.get('doesnotexist'))

    def test_clear_empties_the_in_memory_store(self):
        backend = MemoryCacheBackend()
        backend.set('a', 'aaa')
        backend.set('b', 'bbb')

        backend.clear()

        self.assertIsNone(backend.get('a'))
        self.assertIsNone(backend.get('b'))

    def test_cache_manager_cache_results_and_get_cached_roundtrip(self):
        manager = CacheManager(self.config)
        parser = FakeParser('<html>google result</html>')

        manager.cache_results(parser, 'panama', 'google', 'http', 1)
        cached = manager.get_cached('panama', 'google', 'http', 1)

        self.assertEqual(cached, '<html>google result</html>')

    def test_cache_manager_clear_cache_empties_memory_backend(self):
        manager = CacheManager(self.config)
        parser = FakeParser('<html>google result</html>')
        manager.cache_results(parser, 'panama', 'google', 'http', 1)

        manager.clear_cache()

        self.assertFalse(manager.get_cached('panama', 'google', 'http', 1))


class CacheManagerCustomBackendTestCase(unittest.TestCase):
    """CacheManager also accepts an already constructed backend instance."""

    def test_explicit_backend_instance_is_used_as_is(self):
        backend = MemoryCacheBackend()
        manager = CacheManager({'do_caching': True}, backend=backend)
        self.assertIs(manager.backend, backend)


if __name__ == '__main__':
    unittest.main()
