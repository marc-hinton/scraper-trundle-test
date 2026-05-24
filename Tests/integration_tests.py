#!/usr/bin/python3
# -*- coding: utf-8 -*-

import os
import unittest
import time
import datetime
import threading
from GoogleScraper import scrape_with_config
from GoogleScraper.parsing import get_parser_by_search_engine
from GoogleScraper.config import get_config
from GoogleScraper.database import get_session, Worker
from GoogleScraper.worker_registry import WorkerRegistry
from collections import Counter

config = get_config()
base = os.path.dirname(os.path.realpath(__file__))

all_search_engines = config.get('supported_search_engines')


class GoogleScraperIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        pass

    def tearDown(self):
        pass

    ### Test (very) static parsing for all search engines. The html files are saved in 'data/uncompressed_serp_pages/'
    # The sample files may become old and the SERP format may change over time. But this is the only
    # way to assert that a certain url or piece must be in the results.
    # If the SERP format changes, update accordingly (after all, this shouldn't happen that often).

    def get_parser_for_file(self, se, file, **kwargs):
        file = os.path.join(base, file)
        with open(file, 'r') as f:
            html = f.read()
            parser = get_parser_by_search_engine(se)
            parser = parser(config, html, **kwargs)

        return parser

    def assert_around_10_results_with_snippets(self, parser, delta=4):
        self.assertAlmostEqual(
            len([v['snippet'] for v in parser.search_results['results'] if v['snippet'] is not None]), 10, delta=delta)

    def assert_atleast90percent_of_items_are_not_None(self, parser, exclude_keys={'snippet'}):
        for result_type, res in parser.search_results.items():

            c = Counter()
            for item in res:
                for key, value in item.items():
                    if value is None:
                        c[key] += 1
            for key, value in c.items():
                if key not in exclude_keys:
                    assert (len(res) / int(value)) >= 9, key + ' has too many times a None value: ' + '{}/{}'.format(
                        int(value), len(res))

    def test_parse_google(self):
        parser = self.get_parser_for_file('google', 'data/uncompressed_serp_pages/abrakadabra_google_de_ip.html')

        assert '232.000.000 Ergebnisse' in parser.num_results_for_query
        assert len(parser.search_results['results']) == 12, len(parser.search_results)
        assert all([v['visible_link'] for v in parser.search_results['results']])
        assert all([v['link'] for v in parser.search_results['results']])
        self.assert_around_10_results_with_snippets(parser)
        assert any(['www.extremnews.com' in v['visible_link'] for v in parser.search_results[
            'results']]), 'Theres a link in this serp page with visible url "www.extremnews.com"'
        assert any(
            ['er Noise-Rock-Band Sonic Youth und wurde' in v['snippet'] for v in parser.search_results['results'] if
             v['snippet']]), 'Specific string not found in snippet.'
        self.assert_atleast90percent_of_items_are_not_None(parser)

    def test_parse_bing(self):

        parser = self.get_parser_for_file('bing', 'data/uncompressed_serp_pages/hello_bing_de_ip.html')

        assert '16.900.000 results' == parser.num_results_for_query
        assert len(parser.search_results['results']) == 12, len(parser.search_results['results'])
        assert all([v['visible_link'] for v in parser.search_results['results']])
        assert all([v['link'] for v in parser.search_results['results']])
        self.assert_around_10_results_with_snippets(parser)
        assert any(['Hello Kitty Online Shop - Hello' in v['title'] for v in
                    parser.search_results['results']]), 'Specific title not found in snippet.'
        self.assert_atleast90percent_of_items_are_not_None(parser)

    def test_parse_yahoo(self):

        parser = self.get_parser_for_file('yahoo', 'data/uncompressed_serp_pages/snow_yahoo_de_ip.html')

        assert '19,400,000 Ergebnisse' == parser.num_results_for_query
        assert len(parser.search_results['results']) >= 10, len(parser.search_results['results'])
        assert len([v['visible_link'] for v in parser.search_results['results'] if
                    v['visible_link']]) == 10, 'Not 10 elements with a visible link in yahoo serp page'
        assert all([v['link'] for v in parser.search_results['results']])
        self.assert_around_10_results_with_snippets(parser)
        assert any(
            [' crystalline water ice that falls from clouds. Since snow is composed of small ic' in v['snippet'] for v
             in parser.search_results['results'] if v['snippet']]), 'Specific string not found in snippet.'
        self.assert_atleast90percent_of_items_are_not_None(parser)

    def test_parse_yandex(self):
        return
        parser = self.get_parser_for_file('yandex', 'data/uncompressed_serp_pages/game_yandex_de_ip.html')

        assert '2 029 580' in parser.num_results_for_query
        assert len(parser.search_results['results']) == 10, len(parser.search_results['results'])
        assert len([v['visible_link'] for v in parser.search_results['results'] if
                    v['visible_link']]) == 10, 'Not 10 elements with a visible link in yandex serp page'
        assert all([v['link'] for v in parser.search_results['results']])
        self.assert_around_10_results_with_snippets(parser)
        assert any(['n play games to compile games statist' in v['snippet'] for v in parser.search_results['results'] if
                    v['snippet']]), 'Specific string not found in snippet.'
        self.assert_atleast90percent_of_items_are_not_None(parser)

    def test_parse_baidu(self):

        parser = self.get_parser_for_file('baidu', 'data/uncompressed_serp_pages/number_baidu_de_ip.html')

        assert '100,000,000' in parser.num_results_for_query
        assert len(parser.search_results['results']) >= 6, len(parser.search_results['results'])
        assert all([v['link'] for v in parser.search_results['results']])
        self.assert_around_10_results_with_snippets(parser, delta=5)
        self.assert_atleast90percent_of_items_are_not_None(parser)

    def test_parse_duckduckgo(self):

        parser = self.get_parser_for_file('duckduckgo', 'data/uncompressed_serp_pages/mountain_duckduckgo_de_ip.html')

        # duckduckgo is a biatch

    def test_parse_ask(self):

        parser = self.get_parser_for_file('ask', 'data/uncompressed_serp_pages/fellow_ask_de_ip.html')

        assert len(parser.search_results['results']) >= 10, len(parser.search_results['results'])
        assert len([v['visible_link'] for v in parser.search_results['results'] if
                    v['visible_link']]) == 10, 'Not 10 elements with a visible link in ask serp page'
        assert all([v['link'] for v in parser.search_results['results']])
        self.assert_around_10_results_with_snippets(parser)
        self.assert_atleast90percent_of_items_are_not_None(parser)

    ### test csv output

    def test_csv_output_static(self):
        """Test csv output.

        Test parsing 4 html pages with two queries and two pages per query and
        transforming the results to csv format.

        The cached file should be saved in 'data/csv_tests/', there should
        be as many files as search_engine * pages_for_keyword

        The keyword used in the static SERP pages MUST be 'some words'

        The filenames must be in the GoogleScraper cache format.
        """

        import csv
        from GoogleScraper.output_converter import csv_fieldnames

        number_search_engines = len(all_search_engines)
        csv_outfile = os.path.join(base, 'data/tmp/csv_test.csv')

        config = {
            'keyword': 'some words',
            'search_engines': all_search_engines,
            'num_pages_for_keyword': 2,
            'scrape_method': 'selenium',
            'cachedir': os.path.join(base, 'data/csv_tests/'),
            'do_caching': True,
            'verbosity': 0,
            'output_filename': csv_outfile,
        }
        search = scrape_with_config(config)

        assert os.path.exists(csv_outfile), '{} does not exist'.format(csv_outfile)

        reader = csv.reader(open(csv_outfile, 'rt'))

        # the items that should always have a value:
        notnull = (
            'link', 'query', 'rank', 'domain', 'title', 'link_type', 'scrape_method', 'page_number',
            'search_engine_name',
            'snippet')

        for rownum, row in enumerate(reader):
            if rownum == 0:
                header = row
                header_keys = set(row)
                assert header_keys.issubset(set(csv_fieldnames)), 'Invalid CSV header: {}'.format(header)

            for item in notnull:
                assert row[header.index(item)], '{} has a item that has no value: {}'.format(item, row)

        self.assertAlmostEqual(number_search_engines * 2 * 10, rownum, delta=30)

    ### test json output

    def test_json_output_static(self):
        """Test json output.

        """

        import json

        number_search_engines = len(all_search_engines)
        json_outfile = os.path.join(base, 'data/tmp/json_test.json')

        config = {
            'keyword': 'some words',
            'search_engines': all_search_engines,
            'num_pages_for_keyword': 2,
            'scrape_method': 'selenium',
            'cachedir': os.path.join(base, 'data/json_tests/'),
            'do_caching': True,
            'verbosity': 0,
            'output_filename': json_outfile
        }
        search = scrape_with_config(config)

        assert os.path.exists(json_outfile), '{} does not exist'.format(json_outfile)

        file = open(json_outfile, 'r')
        try:
            results = json.load(file)
        except ValueError as e:
            print('Cannot parse output json file {}. Reason: {}'.format(json_outfile, e))
            raise e

        # the items that should always have a value:
        notnull = ('link', 'rank', 'domain', 'title', 'link_type')
        num_results = 0
        for item in results:

            for k, v in item.items():

                if k == 'results':

                    for res in v:
                        num_results += 1

                        for item in notnull:
                            assert res[item], '{} has a item that has no value: {}'.format(item, res)

        self.assertAlmostEqual(number_search_engines * 2 * 10, num_results, delta=30)

    ### test correct handling of SERP page that has no results for search query.

    def test_no_results_for_query_google(self):
        parser = self.get_parser_for_file('google', 'data/uncompressed_no_results_serp_pages/google.html')

        assert parser.effective_query == '"be dealt and be evaluated"', 'No effective query.'

    def test_no_results_for_query_yandex(self):
        parser = self.get_parser_for_file('yandex', 'data/uncompressed_no_results_serp_pages/yandex.html')

        assert parser.effective_query == 'food', 'Wrong effective query. {}'.format(parser.effective_query)

    def test_no_results_for_query_bing(self):
        parser = self.get_parser_for_file('bing', 'data/uncompressed_no_results_serp_pages/bing.html')

        assert parser.effective_query == 'food', 'Wrong effective query. {}'.format(parser.effective_query)

    def test_no_results_for_query_ask(self):
        parser = self.get_parser_for_file('ask', 'data/uncompressed_no_results_serp_pages/ask.html')

        assert parser.effective_query == 'food', 'Wrong effective query. {}'.format(parser.effective_query)

    ### test correct parsing of the current page number.

    def test_page_number_selector_yandex(self):
        parser = self.get_parser_for_file('yandex', 'data/page_number_selector/yandex_5.html')
        assert parser.page_number == 5, 'Wrong page number. Got {}'.format(parser.page_number)

    def test_page_number_selector_google(self):
        """Google is a bitch in testing this. While saving the html file, the selected
        page is set back to 1. So page_number is always one."""
        parser = self.get_parser_for_file('google', 'data/page_number_selector/google_8.html')
        assert parser.page_number == 1, 'Wrong page number. Got {}'.format(parser.page_number)

    def test_page_number_selector_bing(self):
        parser = self.get_parser_for_file('bing', 'data/page_number_selector/bing_5.html')
        assert parser.page_number == 5, 'Wrong page number. Got {}'.format(parser.page_number)

    def test_page_number_selector_yahoo(self):
        parser = self.get_parser_for_file('yahoo', 'data/page_number_selector/yahoo_3.html')
        assert parser.page_number == 3, 'Wrong page number. Got {}'.format(parser.page_number)

    def test_page_number_selector_baidu(self):
        parser = self.get_parser_for_file('baidu', 'data/page_number_selector/baidu_9.html')
        assert parser.page_number == 9, 'Wrong page number. Got {}'.format(parser.page_number)

    def test_page_number_selector_ask(self):
        parser = self.get_parser_for_file('ask', 'data/page_number_selector/ask_7.html')
        assert parser.page_number == 7, 'Wrong page number. Got {}'.format(parser.page_number)

    ### test all SERP object indicate no results for all search engines.

    def test_no_results_serp_object(self):

        config = {
            'keyword': 'asdfasdfa7654567654345654343sdfasd',
            'search_engines': all_search_engines,
            'num_pages_for_keyword': 1,
            'scrape_method': 'selenium',
            'cachedir': os.path.join(base, 'data/no_results/'),
            'do_caching': True,
            'verbosity': 1,
        }
        search = scrape_with_config(config)

        assert search.number_search_engines_used == len(all_search_engines)
        assert len(search.used_search_engines.split(',')) == len(search.used_search_engines.split(','))
        assert search.number_proxies_used == 1
        assert search.number_search_queries == 1
        assert search.started_searching < search.stopped_searching

        assert len(all_search_engines) == len(search.serps), 'Not enough results. Expected: {}, got {}'.format(
            len(all_search_engines), len(search.serps))

        for serp in search.serps:
            assert serp.has_no_results_for_query(), 'num_results must be 0 but is {}. {}'.format(serp.num_results,
                                                                                                 serp.links)

            # some search engine do alternative searches instead of yielding
            # nothing at all.

            if serp.search_engine_name in ('google', 'bing'):
                assert serp.effective_query, '{} must have an effective query when a keyword has no results.'.format(
                    serp.search_engine_name)

    def test_no_results2_static(self):

        query = '"Find ich besser als einfach nur den Propheten zu zeichnen, denn das ist nur reine Provokation. Was Titanic macht ist Satire."'

        for search_engine in ('google', 'duckduckgo', 'bing', 'yahoo'):
            parser = self.get_parser_for_file(search_engine, 'data/no_results_literal/{}.html'.format(search_engine),
                                              query=query)

            assert parser.num_results == 0 or parser.effective_query, 'No results must be true for search engine {}! But got {} serp entries and effective query: {}.'.format(
                search_engine, parser.num_results, parser.effective_query)

            ### test correct parsing of the number of results for the query..

    def test_csv_file_header_always_the_same(self):
        """
        Check that csv files have always the same order in their header.
        """
        csv_outfile_1 = os.path.join(base, 'data/tmp/csvout1.csv')
        csv_outfile_2 = os.path.join(base, 'data/tmp/csvout2.csv')

        config = {
            'keyword': 'some words',
            'search_engines': all_search_engines,
            'num_pages_for_keyword': 2,
            'scrape_method': 'selenium',
            'cachedir': os.path.join(base, 'data/csv_tests/'),
            'do_caching': True,
            'verbosity': 0,
            'output_filename': csv_outfile_1,
        }
        search = scrape_with_config(config)

        search = scrape_with_config(config)
        config.update({'output_filename': csv_outfile_2})
        search = scrape_with_config(config)

        assert os.path.isfile(csv_outfile_1) and os.path.isfile(csv_outfile_2)

        file1 = open(csv_outfile_1, 'rt')
        file2 = open(csv_outfile_2, 'rt')

        import csv
        reader1, reader2 = csv.DictReader(file1), csv.DictReader(file2)

        header1, header2 = reader1.fieldnames, reader2.fieldnames
        from GoogleScraper.output_converter import csv_fieldnames

        assert header1 == header2 == csv_fieldnames

    def test_duckduckgo_http_mode_works(self):
        """
        duckduckgo has a non javascript version that should
        be queried when using http mode
        """

        parser = self.get_parser_for_file('duckduckgo', 'data/various/duckduckgo_http_mode_december_2015.html',
                                          query='what happened')

        assert parser.num_results > 8

        for result_type, data in parser.search_results.items():
            if result_type == 'normal':
                assert len(data) > 8

            for serp in data:
                assert isinstance(serp['rank'], int)
                assert len(serp['link']) > 8
                assert serp['title']
                assert len(serp['snippet']) > 5


class WorkerRegistryTestCase(unittest.TestCase):
    """Test cases for the worker registry and heartbeat health monitoring system."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = get_config()
        # Create a test-specific config with short timeouts
        self.test_config = self.config.copy()
        self.test_config['heartbeat_interval'] = 1
        self.test_config['heartbeat_timeout'] = 2
        self.test_config['database_name'] = 'test_worker_registry'
        self.registry = WorkerRegistry(self.test_config)

        # Clean up any existing workers
        self._cleanup_test_workers()

    def tearDown(self):
        """Clean up after tests."""
        self.registry.stop_health_check()
        self._cleanup_test_workers()

    def _cleanup_test_workers(self):
        """Clean up test workers from the database."""
        try:
            session_factory = get_session(self.test_config)
            session = session_factory()
            try:
                session.query(Worker).filter(
                    Worker.hostname.in_(['test_worker_1', 'test_worker_2', 'test_worker_3'])
                ).delete()
                session.commit()
            finally:
                session.close()
        except Exception:
            pass

    def test_worker_registration(self):
        """Test basic worker registration."""
        worker = self.registry.register_worker('test_worker_1', 'http', 5)

        assert worker is not None
        assert worker.hostname == 'test_worker_1'
        assert worker.worker_type == 'http'
        assert worker.concurrent_job_capacity == 5
        assert worker.is_active is True
        assert worker.id is not None

    def test_worker_registration_with_invalid_hostname(self):
        """Test worker registration fails with invalid hostname."""
        with self.assertRaises(ValueError):
            self.registry.register_worker('', 'http', 5)

    def test_worker_registration_with_invalid_type(self):
        """Test worker registration fails with invalid worker type."""
        with self.assertRaises(ValueError):
            self.registry.register_worker('test_worker_1', 'invalid_type', 5)

    def test_worker_registration_with_invalid_capacity(self):
        """Test worker registration fails with invalid capacity."""
        with self.assertRaises(ValueError):
            self.registry.register_worker('test_worker_1', 'http', 0)

    def test_worker_re_registration(self):
        """Test that re-registering a worker marks it as active."""
        # Register first time
        worker1 = self.registry.register_worker('test_worker_1', 'http', 5)
        worker1_id = worker1.id

        # Mark as inactive manually
        session_factory = get_session(self.test_config)
        session = session_factory()
        try:
            worker = session.query(Worker).filter(Worker.id == worker1_id).first()
            worker.is_active = False
            session.commit()
        finally:
            session.close()

        # Re-register
        worker2 = self.registry.register_worker('test_worker_1', 'http', 5)
        assert worker2.id == worker1_id
        assert worker2.is_active is True

    def test_heartbeat_updates_timestamp(self):
        """Test that heartbeat updates the last_heartbeat timestamp."""
        worker = self.registry.register_worker('test_worker_1', 'http', 5)
        original_heartbeat = worker.last_heartbeat

        # Wait a bit and send heartbeat
        time.sleep(0.1)
        success = self.registry.heartbeat(worker.id, current_job_count=2, cpu_usage=50.0, memory_usage=60.0)

        assert success is True

        # Verify heartbeat was updated
        session_factory = get_session(self.test_config)
        session = session_factory()
        try:
            updated_worker = session.query(Worker).filter(Worker.id == worker.id).first()
            assert updated_worker.last_heartbeat > original_heartbeat
        finally:
            session.close()

    def test_heartbeat_with_invalid_worker_id(self):
        """Test heartbeat with non-existent worker ID."""
        success = self.registry.heartbeat(99999, current_job_count=2)
        assert success is False

    def test_heartbeat_with_invalid_cpu_usage(self):
        """Test heartbeat fails with invalid CPU usage."""
        worker = self.registry.register_worker('test_worker_1', 'http', 5)
        with self.assertRaises(ValueError):
            self.registry.heartbeat(worker.id, cpu_usage=150.0)

    def test_heartbeat_with_invalid_memory_usage(self):
        """Test heartbeat fails with invalid memory usage."""
        worker = self.registry.register_worker('test_worker_1', 'http', 5)
        with self.assertRaises(ValueError):
            self.registry.heartbeat(worker.id, memory_usage=-10.0)

    def test_health_check_marks_inactive(self):
        """Test that health check marks workers as inactive after timeout."""
        # Register a worker
        worker = self.registry.register_worker('test_worker_1', 'http', 5)
        assert worker.is_active is True

        # Manually set last_heartbeat to past
        session_factory = get_session(self.test_config)
        session = session_factory()
        try:
            w = session.query(Worker).filter(Worker.id == worker.id).first()
            w.last_heartbeat = datetime.datetime.utcnow() - datetime.timedelta(seconds=5)
            session.commit()
        finally:
            session.close()

        # Run health check
        inactive_count = self.registry.perform_health_check()
        assert inactive_count == 1

        # Verify worker is now inactive
        session_factory = get_session(self.test_config)
        session = session_factory()
        try:
            w = session.query(Worker).filter(Worker.id == worker.id).first()
            assert w.is_active is False
        finally:
            session.close()

    def test_health_check_keeps_active_workers(self):
        """Test that health check keeps recently active workers marked as active."""
        worker = self.registry.register_worker('test_worker_1', 'http', 5)

        # Send a heartbeat
        self.registry.heartbeat(worker.id)

        # Run health check
        inactive_count = self.registry.perform_health_check()
        assert inactive_count == 0

        # Verify worker is still active
        session_factory = get_session(self.test_config)
        session = session_factory()
        try:
            w = session.query(Worker).filter(Worker.id == worker.id).first()
            assert w.is_active is True
        finally:
            session.close()

    def test_concurrent_heartbeats(self):
        """Test that concurrent heartbeats from same worker are handled gracefully."""
        worker = self.registry.register_worker('test_worker_1', 'http', 5)

        # Send multiple heartbeats concurrently
        results = []
        def send_heartbeat():
            success = self.registry.heartbeat(worker.id)
            results.append(success)

        threads = [threading.Thread(target=send_heartbeat) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All should succeed
        assert all(results)
        assert len(results) == 5

    def test_get_active_workers(self):
        """Test retrieving list of active workers."""
        # Register multiple workers
        w1 = self.registry.register_worker('test_worker_1', 'http', 5)
        w2 = self.registry.register_worker('test_worker_2', 'selenium', 3)

        # Both should be in active list
        active = self.registry.get_active_workers()
        assert len(active) >= 2
        hostnames = [w.hostname for w in active]
        assert 'test_worker_1' in hostnames
        assert 'test_worker_2' in hostnames

    def test_get_worker_by_id(self):
        """Test retrieving worker by ID."""
        worker = self.registry.register_worker('test_worker_1', 'http', 5)

        retrieved = self.registry.get_worker_by_id(worker.id)
        assert retrieved is not None
        assert retrieved.hostname == 'test_worker_1'

    def test_get_worker_by_hostname(self):
        """Test retrieving worker by hostname."""
        worker = self.registry.register_worker('test_worker_1', 'http', 5)

        retrieved = self.registry.get_worker_by_hostname('test_worker_1')
        assert retrieved is not None
        assert retrieved.id == worker.id

    def test_health_check_thread(self):
        """Test that health check thread runs in background."""
        self.registry.start_health_check()

        # Register a worker
        worker = self.registry.register_worker('test_worker_1', 'http', 5)

        # Manually make it stale
        session_factory = get_session(self.test_config)
        session = session_factory()
        try:
            w = session.query(Worker).filter(Worker.id == worker.id).first()
            w.last_heartbeat = datetime.datetime.utcnow() - datetime.timedelta(seconds=10)
            session.commit()
        finally:
            session.close()

        # Wait for health check to run
        time.sleep(2)

        # Worker should now be marked inactive
        session_factory = get_session(self.test_config)
        session = session_factory()
        try:
            w = session.query(Worker).filter(Worker.id == worker.id).first()
            assert w.is_active is False
        finally:
            session.close()


if __name__ == '__main__':
    unittest.main(warnings='ignore')
