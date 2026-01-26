import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import logging

logger = logging.getLogger(__name__)

def get_common_headers():
    """Return common browser headers to avoid basic bot detection."""
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

def get_deep_links(url: str) -> list:
    """
    Level 1 Crawl: Fetch the URL and return all unique links found in <a> tags.
    """
    try:
        logger.info(f"🕷️ Crawling: {url}")
        session = requests.Session()
        session.headers.update(get_common_headers())
        
        response = session.get(url, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        base_domain = urlparse(url).netloc
        
        found_links = set()
        
        # Find all valid links
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            full_url = urljoin(url, href)
            parsed = urlparse(full_url)
            
            # Basic filters:
            # 1. Must be http/https
            if parsed.scheme not in ('http', 'https'):
                continue
                
            # 2. Skip common junk and non-content pages
            if any(x in full_url.lower() for x in ['javascript:', 'mailto:', '#', 'login', 'register', '/tags/', '/categories/', '/search/']):
                continue

            found_links.add(full_url)
            
        logger.info(f"✅ Found {len(found_links)} links on {url}")
        return list(found_links)

    except Exception as e:
        logger.error(f"❌ Crawl failed for {url}: {e}")
        return []
