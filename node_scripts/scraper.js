#!/usr/bin/env node

/**
 * Puppeteer-based web scraper that accepts JSON input via stdin
 * and outputs results as JSON on stdout.
 * 
 * Input JSON format:
 * {
 *   "search_engine": "google",
 *   "query": "search term",
 *   "page_number": 1,
 *   "proxy_config": {
 *     "host": "proxy_host",
 *     "port": "proxy_port",
 *     "protocol": "http"
 *   },
 *   "timeout": 30000,
 *   "headless": true,
 *   "user_agent": "Mozilla/5.0..."
 * }
 * 
 * Output JSON format:
 * {
 *   "success": true,
 *   "html": "...",
 *   "url": "...",
 *   "timestamp": "2026-05-24T..."
 * }
 */

const puppeteer = require('puppeteer');

// Parse command-line arguments
const args = process.argv.slice(2);
let inputData = '';

// Read JSON input from stdin
process.stdin.on('data', (chunk) => {
  inputData += chunk.toString();
});

process.stdin.on('end', async () => {
  try {
    let input = {};
    try {
      input = JSON.parse(inputData);
    } catch (e) {
      // If no input or invalid JSON, use defaults
      input = {};
    }

    const result = await runScraper(input);
    console.log(JSON.stringify(result));
    process.exit(0);
  } catch (error) {
    console.log(JSON.stringify({
      success: false,
      error: error.message,
      timestamp: new Date().toISOString()
    }));
    process.exit(1);
  }
});

async function runScraper(input) {
  const {
    search_engine = 'google',
    query = '',
    page_number = 1,
    proxy_config = null,
    timeout = 30000,
    headless = true,
    user_agent = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
  } = input;

  let browser = null;
  let page = null;

  try {
    // Build launch options
    const launchOptions = {
      headless: headless,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
      ]
    };

    // Add proxy support
    if (proxy_config && proxy_config.host && proxy_config.port) {
      const proxyUrl = `${proxy_config.protocol || 'http'}://${proxy_config.host}:${proxy_config.port}`;
      launchOptions.args.push(`--proxy-server=${proxyUrl}`);
    }

    browser = await puppeteer.launch(launchOptions);
    page = await browser.newPage();

    // Set viewport and user agent
    await page.setViewport({ width: 1920, height: 1080 });
    await page.setUserAgent(user_agent);

    // Set timeout
    page.setDefaultTimeout(timeout);

    // Build search URL
    const searchUrl = buildSearchUrl(search_engine, query, page_number);

    // Navigate to search engine
    await page.goto(searchUrl, { waitUntil: 'networkidle2' });

    // Get page content
    const html = await page.content();
    const currentUrl = page.url();

    return {
      success: true,
      html: html,
      url: currentUrl,
      search_engine: search_engine,
      query: query,
      page_number: page_number,
      timestamp: new Date().toISOString()
    };

  } finally {
    if (browser) {
      try {
        await browser.close();
      } catch (e) {
        // Ignore errors on cleanup
      }
    }
  }
}

function buildSearchUrl(searchEngine, query, pageNumber = 1) {
  const encodedQuery = encodeURIComponent(query);
  let baseUrl = 'https://www.google.com/search?';
  let params = {};

  switch (searchEngine.toLowerCase()) {
    case 'google':
      params.hl = 'en';
      params.q = encodedQuery;
      if (pageNumber > 1) {
        params.start = String((pageNumber - 1) * 10);
      }
      baseUrl = 'https://www.google.com/search?';
      break;

    case 'bing':
      params.q = encodedQuery;
      if (pageNumber > 1) {
        params.first = String(1 + ((pageNumber - 1) * 10));
      }
      baseUrl = 'https://www.bing.com/search?';
      break;

    case 'yahoo':
      params.p = encodedQuery;
      if (pageNumber > 1) {
        params.b = String(1 + ((pageNumber - 1) * 10));
      }
      baseUrl = 'https://search.yahoo.com/search?';
      break;

    case 'duckduckgo':
      params.q = encodedQuery;
      baseUrl = 'https://duckduckgo.com/html/?';
      break;

    case 'baidu':
      params.wd = encodedQuery;
      if (pageNumber > 1) {
        params.pn = String((pageNumber - 1) * 10);
      }
      baseUrl = 'http://www.baidu.com/s?';
      break;

    case 'yandex':
      params.text = encodedQuery;
      if (pageNumber > 1) {
        params.p = String(pageNumber - 1);
      }
      baseUrl = 'http://yandex.ru/yandsearch?';
      break;

    default:
      params.q = encodedQuery;
      if (pageNumber > 1) {
        params.start = String((pageNumber - 1) * 10);
      }
  }

  const queryString = Object.keys(params)
    .map(key => `${encodeURIComponent(key)}=${params[key]}`)
    .join('&');

  return baseUrl + queryString;
}
