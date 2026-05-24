# -*- coding: utf-8 -*-

import json
import subprocess
import os
import sys
import datetime
import threading
import logging
from queue import Queue

from GoogleScraper.scraping import SearchEngineScrape, get_base_search_url_by_search_engine
from GoogleScraper.user_agents import random_user_agent

logger = logging.getLogger(__name__)


class PuppeteerScraper(SearchEngineScrape, threading.Timer):
    """Headless browser scraper using Puppeteer via Node.js subprocess.
    
    This scraper launches Node.js Puppeteer scripts to control a headless
    browser and scrape search engine results. It manages a pool of browser
    instances for efficiency and communicates with the Node.js process via
    JSON over stdin/stdout.
    """

    # Class-level browser pool shared across instances
    _browser_pools = {}
    _pool_locks = {}

    def __init__(self, config, *args, time_offset=0.0, **kwargs):
        """Initialize a PuppeteerScraper instance.
        
        Args:
            config: Configuration dictionary
            time_offset: Offset for timer-based execution
            **kwargs: Additional arguments passed to SearchEngineScrape
        """
        threading.Timer.__init__(self, time_offset, self.search)
        SearchEngineScrape.__init__(self, config, *args, **kwargs)

        # Set the scrape method
        self.scrape_method = 'puppeteer'

        # Puppeteer-specific configuration
        self.puppeteer_timeout = int(self.config.get('puppeteer_timeout', 30000))  # milliseconds
        self.headless_mode = self.config.get('puppeteer_headless_mode', True)
        self.browser_count = int(self.config.get('puppeteer_browser_count', 3))

        # Get the base search URL
        self.base_search_url = get_base_search_url_by_search_engine(
            self.config, self.search_engine_name, self.scrape_method)

        # Path to the Node.js scraper script
        self.scraper_script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'node_scripts', 'scraper.js'
        )

        # Initialize or get browser pool for this search engine
        if self.search_engine_name not in PuppeteerScraper._browser_pools:
            PuppeteerScraper._browser_pools[self.search_engine_name] = Queue(maxsize=self.browser_count)
            PuppeteerScraper._pool_locks[self.search_engine_name] = threading.Lock()

        self.browser_pool = PuppeteerScraper._browser_pools[self.search_engine_name]
        self.pool_lock = PuppeteerScraper._pool_locks[self.search_engine_name]

        super().instance_creation_info(self.__class__.__name__)

    def set_proxy(self):
        """Install a proxy for use with Puppeteer."""
        # Proxy configuration is passed to the Node.js script
        pass

    def switch_proxy(self, proxy):
        """Switch the proxy for the Puppeteer instance."""
        # Proxy can be switched by passing different config to next request
        pass

    def proxy_check(self, proxy):
        """Check whether the assigned proxy works correctly."""
        # For now, assume proxy is valid
        # Could implement actual check if needed
        return True

    def handle_request_denied(self, status_code=''):
        """Handle request denial by the search engine."""
        super().handle_request_denied(status_code)

    def build_search(self):
        """Build the search parameters for the Puppeteer request."""
        # Search parameters are built in the Node.js script
        pass

    def search(self, rand=True, timeout=None):
        """Execute a search using Puppeteer.
        
        Args:
            rand: Whether to randomize user agent
            timeout: Override timeout value
            
        Returns:
            True if search was successful, False otherwise
        """
        success = True

        self.build_search()

        try:
            super().detection_prevention_sleep()
            super().keyword_info()

            # Prepare input for Node.js Puppeteer script
            user_agent = random_user_agent(only_desktop=True) if rand else 'Mozilla/5.0'

            proxy_config = None
            if self.proxy:
                proxy_config = {
                    'host': self.proxy.host,
                    'port': self.proxy.port,
                    'protocol': self.proxy.proto if hasattr(self.proxy, 'proto') else 'http'
                }

            search_input = {
                'search_engine': self.search_engine_name,
                'query': self.query,
                'page_number': self.page_number,
                'proxy_config': proxy_config,
                'timeout': timeout or self.puppeteer_timeout,
                'headless': self.headless_mode,
                'user_agent': user_agent
            }

            # Call the Node.js Puppeteer script
            self.html = self._execute_puppeteer(search_input)

            if not self.html:
                success = False
                self.status = 'No HTML content returned from Puppeteer'

            self.requested_at = datetime.datetime.utcnow()

        except Exception as e:
            self.status = 'Puppeteer search error: {}'.format(str(e))
            logger.error('Puppeteer search failed for query "{}": {}'.format(self.query, str(e)))
            success = False

        super().after_search()

        return success

    def _execute_puppeteer(self, search_input):
        """Execute the Node.js Puppeteer script and return the HTML content.
        
        Args:
            search_input: Dictionary with search parameters
            
        Returns:
            The HTML content of the search results page
            
        Raises:
            Exception: If the script execution fails
        """
        try:
            # Convert input to JSON
            input_json = json.dumps(search_input)

            # Execute the Node.js script
            process = subprocess.Popen(
                ['node', self.scraper_script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            # Communicate with the process
            stdout, stderr = process.communicate(input=input_json, timeout=self.puppeteer_timeout / 1000 + 5)

            if process.returncode != 0:
                logger.error('Puppeteer script failed with return code {}: {}'.format(process.returncode, stderr))
                raise Exception('Puppeteer script execution failed: {}'.format(stderr))

            # Parse the result
            try:
                result = json.loads(stdout)
            except json.JSONDecodeError as e:
                logger.error('Failed to parse Puppeteer output as JSON: {}'.format(stdout))
                raise Exception('Invalid JSON output from Puppeteer: {}'.format(str(e)))

            if not result.get('success', False):
                error_msg = result.get('error', 'Unknown error')
                logger.error('Puppeteer execution error: {}'.format(error_msg))
                raise Exception('Puppeteer error: {}'.format(error_msg))

            return result.get('html', '')

        except subprocess.TimeoutExpired:
            logger.error('Puppeteer script timeout after {} seconds'.format(self.puppeteer_timeout / 1000))
            raise Exception('Puppeteer timeout')
        except FileNotFoundError:
            raise Exception('Node.js not found in PATH or scraper.js not found at {}'.format(self.scraper_script))

    def run(self):
        """Run the scraper in a separate thread."""
        super().before_search()

        if self.startable:
            for self.query, self.pages_per_keyword in self.jobs.items():
                for self.page_number in self.pages_per_keyword:
                    if not self.search(rand=True):
                        self.missed_keywords.add(self.query)
