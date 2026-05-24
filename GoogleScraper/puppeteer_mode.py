# -*- coding: utf-8 -*-

import subprocess
import json
import threading
import os
import sys
import logging
from pathlib import Path

from GoogleScraper.scraping import SearchEngineScrape, get_base_search_url_by_search_engine
from GoogleScraper.user_agents import random_user_agent

logger = logging.getLogger(__name__)


class PuppeteerScraper(SearchEngineScrape, threading.Thread):
    """Puppeteer-based scraper that uses headless Chrome via Node.js Puppeteer.
    
    This scraper launches Node.js Puppeteer scripts as subprocesses to handle
    browser automation, JavaScript rendering, and page scraping with lower
    resource overhead compared to Selenium.
    """

    def __init__(self, config, *args, browser_num=1, **kwargs):
        """Initialize a PuppeteerScraper instance.
        
        Args:
            config: Configuration dictionary
            browser_num: Browser instance number for tracking
            *args: Additional positional arguments for SearchEngineScrape
            **kwargs: Additional keyword arguments for SearchEngineScrape
        """
        threading.Thread.__init__(self)
        SearchEngineScrape.__init__(self, config, *args, **kwargs)
        
        self.browser_num = browser_num
        self.scrape_method = 'puppeteer'
        
        # Get puppeteer-specific config
        self.timeout = self.config.get('puppeteer_timeout', 30)
        self.headless_mode = self.config.get('puppeteer_headless_mode', True)
        
        # Get the Node.js scraper script path
        self._script_path = self._get_scraper_script_path()
        
        # Get the base search url
        self.base_search_url = get_base_search_url_by_search_engine(
            self.config, self.search_engine_name, self.scrape_method
        )
        
        # User agent for requests
        self.user_agent = random_user_agent()
        
        # Log instance creation
        super().instance_creation_info(self.__class__.__name__)

    def _get_scraper_script_path(self):
        """Get the path to the Node.js scraper script.
        
        Returns:
            Path to the node_scripts/scraper.js file
        """
        # Get the directory where this file is located
        current_dir = Path(__file__).parent.parent
        script_path = current_dir / 'node_scripts' / 'scraper.js'
        
        if not script_path.exists():
            logger.warning(f'Scraper script not found at {script_path}')
        
        return str(script_path)

    def set_proxy(self):
        """Install a proxy on the communication channel."""
        # Proxy is handled in the subprocess call
        pass

    def switch_proxy(self, proxy):
        """Switch the proxy on the communication channel."""
        # Update the proxy for subsequent requests
        self.proxy = proxy

    def proxy_check(self, proxy):
        """Check whether the assigned proxy works correctly."""
        # For Puppeteer, we can make a simple request to check the proxy
        return True

    def handle_request_denied(self, status_code):
        """Generic behaviour when search engines detect our scraping."""
        self.status = 'Malicious request detected: {}'.format(status_code)

    def search(self, *args, **kwargs):
        """Execute the search using Puppeteer.
        
        This method is called by the threading framework and orchestrates
        the search process for all keywords and pages.
        """
        self.before_search()
        
        if not self.startable:
            logger.error('Scraper instance is not startable')
            return
        
        # Iterate through all keywords and pages
        for query, pages in self.jobs.items():
            self.query = query
            
            for page_number in pages:
                self.page_number = page_number
                
                # Apply detection prevention sleep
                self.detection_prevention_sleep()
                
                # Perform the actual search
                self._do_search()
                
                # Store and process results
                self.after_search()

    def _do_search(self):
        """Execute a single search query using Puppeteer subprocess."""
        try:
            # Build the input data for the Node.js script
            input_data = {
                'search_engine': self.search_engine_name,
                'query': self.query,
                'timeout': self.timeout * 1000,  # Convert to milliseconds
                'headless': self.headless_mode,
                'user_agent': self.user_agent,
                'headers': {
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate',
                    'Connection': 'keep-alive'
                }
            }
            
            # Add proxy configuration if available
            if self.proxy:
                input_data['proxy_config'] = {
                    'host': self.proxy.host,
                    'port': self.proxy.port,
                    'proto': getattr(self.proxy, 'proto', 'http')
                }
            
            # Call the Node.js scraper script
            result = self._call_puppeteer_script(input_data)
            
            if result and result.get('success'):
                self.html = result.get('html', '')
                self.requested_at = None
            else:
                logger.error(f'Puppeteer scraping failed: {result.get("error", "Unknown error")}')
                self.html = ''
        
        except Exception as e:
            logger.error(f'Error during search: {e}')
            self.html = ''

    def _call_puppeteer_script(self, input_data):
        """Call the Node.js Puppeteer script as a subprocess.
        
        Args:
            input_data: Dictionary with scraping parameters
            
        Returns:
            Dictionary with 'success' and 'html' or 'error' keys
        """
        try:
            # Check if Node.js is available
            node_path = self.config.get('node_path', 'node')
            
            # Prepare the subprocess
            process = subprocess.Popen(
                [node_path, self._script_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Send input data as JSON
            input_json = json.dumps(input_data)
            
            # Execute with timeout
            try:
                stdout, stderr = process.communicate(
                    input=input_json,
                    timeout=self.timeout + 5  # Add buffer to Node timeout
                )
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                logger.error(f'Puppeteer script timeout after {self.timeout}s')
                return {
                    'success': False,
                    'error': f'Script timeout after {self.timeout}s'
                }
            
            # Parse the output
            if stdout:
                try:
                    result = json.loads(stdout)
                    return result
                except json.JSONDecodeError as e:
                    logger.error(f'Failed to parse Puppeteer output: {e}')
                    logger.error(f'Stdout: {stdout}')
                    logger.error(f'Stderr: {stderr}')
                    return {
                        'success': False,
                        'error': f'Invalid JSON output from script: {e}'
                    }
            else:
                logger.error(f'No output from Puppeteer script. Stderr: {stderr}')
                return {
                    'success': False,
                    'error': f'No output from script. Error: {stderr}'
                }
        
        except FileNotFoundError:
            logger.error(f'Node.js not found at {node_path}')
            return {
                'success': False,
                'error': f'Node.js not found at {node_path}'
            }
        except Exception as e:
            logger.error(f'Error calling Puppeteer script: {e}')
            return {
                'success': False,
                'error': str(e)
            }

    def __call__(self):
        """Make the scraper callable to execute the search.
        
        Returns:
            self with populated html results
        """
        self.search()
        return self

    def run(self):
        """Override threading.Thread.run() to execute search."""
        self.search()
