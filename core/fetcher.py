"""
News fetching module for AI Pulse.
Fetches AI news from RSS feeds and web sources.
"""

import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from dateutil import parser as date_parser
import logging
import time
import hashlib
from typing import List, Dict, Optional

from config.sources import SOURCES, WEB_SCRAPE_SOURCES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Time range for news (past 14 days)
DAYS_LOOKBACK = 14


def parse_date(date_str: str) -> Optional[datetime]:
    """Parse various date formats into datetime object."""
    if not date_str:
        return None
    try:
        return date_parser.parse(date_str)
    except Exception:
        return None


def is_within_range(dt: datetime) -> bool:
    """Check if date is within the past 14 days."""
    if dt is None:
        return False
    cutoff = datetime.now() - timedelta(days=DAYS_LOOKBACK)
    return dt >= cutoff


def extract_date_from_entry(entry) -> Optional[datetime]:
    """Extract and parse date from a feed entry."""
    # Try different date fields
    for field in ['published_parsed', 'updated_parsed', 'dc_date', 'published', 'updated']:
        if hasattr(entry, field):
            value = getattr(entry, field)
            if value:
                if hasattr(value, 'tm_year'):  # It's a time_struct
                    try:
                        return datetime.fromtimestamp(time.mktime(value))
                    except Exception:
                        continue
                elif isinstance(value, str):
                    dt = parse_date(value)
                    if dt:
                        return dt
    return None


def fetch_rss_feed(source: Dict) -> List[Dict]:
    """Fetch and parse an RSS feed."""
    items = []
    source_name = source["name"]
    url = source["url"]

    try:
        logger.info(f"Fetching RSS feed: {source_name}")
        feed = feedparser.parse(url)

        if feed.bozo and not feed.entries:
            logger.warning(f"Feed may be malformed: {source_name}")
            return items

        for entry in feed.entries:
            # Extract date
            dt = extract_date_from_entry(entry)

            if dt and not is_within_range(dt):
                continue

            # Extract title
            title = getattr(entry, 'title', '') or ''

            # Extract summary/description
            summary = ''
            if hasattr(entry, 'summary'):
                summary = entry.summary
            elif hasattr(entry, 'description'):
                summary = entry.description
            # Clean HTML from summary
            if summary:
                soup = BeautifulSoup(summary, 'html.parser')
                summary = soup.get_text(separator=' ', strip=True)

            # Extract link
            link = getattr(entry, 'link', '') or ''

            if not title:
                continue

            # Create unique ID for deduplication
            item_id = hashlib.md5(f"{link}{title}".encode()).hexdigest()

            item = {
                'id': item_id,
                'title': title,
                'summary': summary[:500] if summary else '',  # Limit summary length
                'link': link,
                'published_date': dt.isoformat() if dt else None,
                'source_name': source_name
            }
            items.append(item)

        logger.info(f"Fetched {len(items)} items from {source_name}")

    except Exception as e:
        logger.error(f"Error fetching {source_name}: {str(e)}")

    return items


def scrape_web_source(source: Dict) -> List[Dict]:
    """Scrape headlines from a web source using BeautifulSoup."""
    items = []
    source_name = source["name"]
    url = source["url"]

    try:
        logger.info(f"Scraping web source: {source_name}")

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')

        # Find articles/posts - try common patterns
        article_elements = soup.find_all(['article', 'div', 'li'], class_=lambda x: x and any(
            term in str(x).lower() for term in ['post', 'article', 'item', 'card', 'entry']
        ))

        if not article_elements:
            # Fallback: find all links that might be articles
            article_elements = soup.find_all('a', href=True)

        for elem in article_elements[:20]:  # Limit to 20 items
            title = ''
            summary = ''
            link = ''

            # Try to extract title
            title_elem = elem.find(['h1', 'h2', 'h3', 'h4'])
            if title_elem:
                title = title_elem.get_text(strip=True)
            else:
                title = elem.get_text(strip=True)[:100]

            # Try to extract link
            if elem.name == 'a':
                link = elem.get('href', '')
            else:
                link_elem = elem.find('a', href=True)
                if link_elem:
                    link = link_elem.get('href', '')

            # Make absolute URL
            if link and not link.startswith('http'):
                from urllib.parse import urljoin
                link = urljoin(url, link)

            if not title or len(title) < 10:
                continue

            # Use current date as fallback
            dt = datetime.now()

            if not is_within_range(dt):
                continue

            item_id = hashlib.md5(f"{link}{title}".encode()).hexdigest()

            item = {
                'id': item_id,
                'title': title,
                'summary': summary,
                'link': link,
                'published_date': dt.isoformat(),
                'source_name': source_name
            }
            items.append(item)

        logger.info(f"Scraped {len(items)} items from {source_name}")

    except Exception as e:
        logger.error(f"Error scraping {source_name}: {str(e)}")

    return items


def fetch_all_news() -> List[Dict]:
    """Fetch news from all configured sources."""
    all_items = []
    seen_urls = set()

    # Fetch RSS feeds
    rss_sources = [s for s in SOURCES if s["type"] == "rss"]
    for source in rss_sources:
        items = fetch_rss_feed(source)
        for item in items:
            if item['link'] not in seen_urls:
                seen_urls.add(item['link'])
                all_items.append(item)

    # Fetch web sources
    web_sources = [s for s in SOURCES if s["type"] == "web"]
    for source in web_sources:
        items = scrape_web_source(source)
        for item in items:
            if item['link'] not in seen_urls:
                seen_urls.add(item['link'])
                all_items.append(item)

    logger.info(f"Total unique articles fetched: {len(all_items)}")
    return all_items


def get_source_stats(all_items: List[Dict]) -> Dict[str, int]:
    """Get article count per source."""
    stats = {}
    for item in all_items:
        source = item['source_name']
        stats[source] = stats.get(source, 0) + 1
    return stats
