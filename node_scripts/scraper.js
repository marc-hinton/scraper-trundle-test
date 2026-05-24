const puppeteer = require('puppeteer');

/**
 * Main Puppeteer scraper that reads JSON input from stdin and outputs results to stdout.
 * Input format: JSON with keys:
 *   - search_engine: name of search engine (google, bing, etc.)
 *   - query: search query string
 *   - proxy_config: optional proxy configuration with host, port, proto
 *   - headers: optional custom headers
 *   - user_agent: optional user agent string
 * 
 * Output format: JSON with keys:
 *   - success: boolean indicating if scraping was successful
 *   - html: HTML content of the page
 *   - error: error message if unsuccessful
 */

async function scrapeWithPuppeteer(inputData) {
  let browser = null;
  try {
    const {
      search_engine,
      query,
      proxy_config,
      headers,
      user_agent,
      timeout = 30000,
      headless = true
    } = inputData;

    // Build launch options
    const launchOptions = {
      headless: headless,
      args: ['--no-sandbox', '--disable-setuid-sandbox']
    };

    // Add proxy support if configured
    if (proxy_config && proxy_config.host && proxy_config.port) {
      const proxyUrl = `${proxy_config.proto || 'http'}://${proxy_config.host}:${proxy_config.port}`;
      launchOptions.args.push(`--proxy-server=${proxyUrl}`);
    }

    browser = await puppeteer.launch(launchOptions);
    const page = await browser.newPage();

    // Set user agent
    if (user_agent) {
      await page.setUserAgent(user_agent);
    }

    // Set custom headers
    if (headers && typeof headers === 'object') {
      await page.setExtraHTTPHeaders(headers);
    }

    // Set viewport
    await page.setViewport({ width: 1920, height: 1080 });

    // Build the search URL
    let searchUrl = buildSearchUrl(search_engine, query);

    // Navigate to the search page
    await page.goto(searchUrl, { 
      waitUntil: 'networkidle2',
      timeout: timeout
    });

    // Get the page HTML
    const html = await page.content();

    await browser.close();

    return {
      success: true,
      html: html
    };

  } catch (error) {
    if (browser) {
      await browser.close();
    }
    return {
      success: false,
      error: error.message || String(error)
    };
  }
}

/**
 * Build the search URL for different search engines
 */
function buildSearchUrl(searchEngine, query) {
  const encodedQuery = encodeURIComponent(query);
  
  switch (searchEngine.toLowerCase()) {
    case 'google':
      return `https://www.google.com/search?q=${encodedQuery}&hl=en`;
    case 'bing':
      return `https://www.bing.com/search?q=${encodedQuery}`;
    case 'yahoo':
      return `https://de.search.yahoo.com/search?p=${encodedQuery}`;
    case 'yandex':
      return `https://www.yandex.ru/search/?text=${encodedQuery}`;
    case 'duckduckgo':
      return `https://duckduckgo.com/?q=${encodedQuery}`;
    case 'ask':
      return `https://www.ask.com/web?q=${encodedQuery}`;
    case 'baidu':
      return `https://www.baidu.com/s?wd=${encodedQuery}`;
    default:
      return `https://www.google.com/search?q=${encodedQuery}&hl=en`;
  }
}

/**
 * Read JSON from stdin and process it
 */
function readStdin() {
  return new Promise((resolve, reject) => {
    let data = '';
    
    process.stdin.setEncoding('utf8');
    process.stdin.on('readable', () => {
      let chunk;
      while ((chunk = process.stdin.read()) !== null) {
        data += chunk;
      }
    });
    
    process.stdin.on('end', () => {
      try {
        const inputData = JSON.parse(data);
        resolve(inputData);
      } catch (e) {
        reject(new Error('Invalid JSON input: ' + e.message));
      }
    });
    
    process.stdin.on('error', (err) => {
      reject(err);
    });
  });
}

/**
 * Main entry point
 */
async function main() {
  try {
    const inputData = await readStdin();
    const result = await scrapeWithPuppeteer(inputData);
    console.log(JSON.stringify(result));
    process.exit(result.success ? 0 : 1);
  } catch (error) {
    const result = {
      success: false,
      error: error.message || String(error)
    };
    console.log(JSON.stringify(result));
    process.exit(1);
  }
}

main();
