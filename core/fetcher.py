"""
News fetching module for AI Pulse.
Fetches AI news from RSS feeds and web sources with concurrent execution.
"""

import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from config.settings import DAYS_LOOKBACK, FETCH_WORKERS
from config.sources import SOURCES, WEB_SCRAPE_SOURCES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_date(date_str: str) -> Optional[datetime]:
    """Parse various date formats into a timezone-aware datetime object."""
    if not date_str:
        return None
    try:
        dt = date_parser.parse(date_str)
        # Ensure timezone-aware (assume UTC if naive)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def is_within_range(dt: Optional[datetime]) -> bool:
    """Check if date is within the configured lookback window."""
    if dt is None:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_LOOKBACK)
    # Normalise to UTC for comparison
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= cutoff


def extract_date_from_entry(entry) -> Optional[datetime]:
    """Extract and parse date from a feed entry."""
    for field in ['published_parsed', 'updated_parsed', 'dc_date', 'published', 'updated']:
        if hasattr(entry, field):
            value = getattr(entry, field)
            if value:
                if hasattr(value, 'tm_year'):  # It's a time_struct
                    try:
                        dt = datetime.fromtimestamp(
                            time.mktime(value), tz=timezone.utc
                        )
                        return dt
                    except Exception:
                        continue
                elif isinstance(value, str):
                    dt = parse_date(value)
                    if dt:
                        return dt
    return None


def _try_extract_date_from_html(soup: BeautifulSoup) -> Optional[datetime]:
    """Best-effort date extraction from scraped HTML pages."""
    # 1. <meta> tags
    for attr in ("article:published_time", "datePublished", "date", "DC.date"):
        tag = soup.find("meta", attrs={"property": attr}) or soup.find(
            "meta", attrs={"name": attr}
        )
        if tag and tag.get("content"):
            dt = parse_date(tag["content"])
            if dt:
                return dt

    # 2. <time> elements
    time_tag = soup.find("time", attrs={"datetime": True})
    if time_tag:
        dt = parse_date(time_tag["datetime"])
        if dt:
            return dt

    return None


def fetch_rss_feed(source: Dict) -> List[Dict]:
    """Fetch and parse an RSS feed."""
    items: List[Dict] = []
    source_name = source["name"]
    url = source["url"]

    try:
        logger.debug("Fetching RSS feed: %s", source_name)
        feed = feedparser.parse(url)

        if feed.bozo and not feed.entries:
            logger.warning("Feed may be malformed: %s", source_name)
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
                'summary': summary[:500] if summary else '',
                'link': link,
                'published_date': dt.isoformat() if dt else None,
                'source_name': source_name
            }
            items.append(item)

        logger.debug("Fetched %d items from %s", len(items), source_name)

    except Exception as e:
        logger.error("Error fetching %s: %s", source_name, e)

    return items


def _get_scrape_selectors(source_name: str) -> Optional[Dict]:
    """Look up CSS selectors defined in WEB_SCRAPE_SOURCES."""
    for ws in WEB_SCRAPE_SOURCES:
        if ws["name"] == source_name:
            return ws.get("selectors")
    return None


def scrape_web_source(source: Dict) -> List[Dict]:
    """Scrape headlines from a web source using BeautifulSoup.

    If the source is registered in WEB_SCRAPE_SOURCES with explicit CSS
    selectors, those selectors are used.  Otherwise, generic heuristics
    are applied.
    """
    items: List[Dict] = []
    source_name = source["name"]
    url = source["url"]

    try:
        logger.debug("Scraping web source: %s", source_name)

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')

        # Try to extract a page-level date as fallback
        page_date = _try_extract_date_from_html(soup)

        selectors = _get_scrape_selectors(source_name)

        if selectors:
            # Use configured selectors
            title_elems = soup.select(selectors.get("title", "h2, h3"))
            for title_elem in title_elems[:20]:
                title = title_elem.get_text(strip=True)
                if not title or len(title) < 10:
                    continue

                # Find closest link
                link = ""
                parent_a = title_elem.find_parent("a", href=True)
                if parent_a:
                    link = parent_a["href"]
                else:
                    child_a = title_elem.find("a", href=True)
                    if child_a:
                        link = child_a["href"]

                if link and not link.startswith("http"):
                    link = urljoin(url, link)

                dt = page_date or datetime.now(timezone.utc)
                item_id = hashlib.md5(f"{link}{title}".encode()).hexdigest()

                items.append({
                    'id': item_id,
                    'title': title,
                    'summary': '',
                    'link': link,
                    'published_date': dt.isoformat(),
                    'source_name': source_name,
                })
        else:
            # Generic heuristic scraping (original logic)
            article_elements = soup.find_all(
                ['article', 'div', 'li'],
                class_=lambda x: x and any(
                    term in str(x).lower()
                    for term in ['post', 'article', 'item', 'card', 'entry']
                ),
            )

            if not article_elements:
                article_elements = soup.find_all('a', href=True)

            for elem in article_elements[:20]:
                title = ''
                link = ''

                title_elem = elem.find(['h1', 'h2', 'h3', 'h4'])
                if title_elem:
                    title = title_elem.get_text(strip=True)
                else:
                    title = elem.get_text(strip=True)[:100]

                if elem.name == 'a':
                    link = elem.get('href', '')
                else:
                    link_elem = elem.find('a', href=True)
                    if link_elem:
                        link = link_elem.get('href', '')

                if link and not link.startswith('http'):
                    link = urljoin(url, link)

                if not title or len(title) < 10:
                    continue

                dt = page_date or datetime.now(timezone.utc)

                if not is_within_range(dt):
                    continue

                item_id = hashlib.md5(f"{link}{title}".encode()).hexdigest()

                items.append({
                    'id': item_id,
                    'title': title,
                    'summary': '',
                    'link': link,
                    'published_date': dt.isoformat(),
                    'source_name': source_name,
                })

        logger.debug("Scraped %d items from %s", len(items), source_name)

    except Exception as e:
        logger.error("Error scraping %s: %s", source_name, e)

    return items


def _fetch_source(source: Dict) -> List[Dict]:
    """Dispatch a single source to the correct fetcher."""
    if source["type"] == "rss":
        return fetch_rss_feed(source)
    elif source["type"] == "web":
        return scrape_web_source(source)
    return []


def fetch_all_news() -> List[Dict]:
    """Fetch news from all configured sources concurrently."""
    all_items: List[Dict] = []
    seen_urls: set = set()

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as executor:
        future_to_source = {
            executor.submit(_fetch_source, source): source
            for source in SOURCES
        }

        for future in as_completed(future_to_source):
            source = future_to_source[future]
            try:
                items = future.result()
                for item in items:
                    if item['link'] and item['link'] not in seen_urls:
                        seen_urls.add(item['link'])
                        all_items.append(item)
            except Exception as exc:
                logger.error("Source %s generated an exception: %s", source["name"], exc)

    logger.info("Total unique articles fetched: %d", len(all_items))
    return all_items


def get_source_stats(all_items: List[Dict]) -> Dict[str, int]:
    """Get article count per source."""
    stats: Dict[str, int] = {}
    for item in all_items:
        source = item['source_name']
        stats[source] = stats.get(source, 0) + 1
    return stats
