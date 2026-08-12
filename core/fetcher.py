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

from config.settings import (
    DAYS_LOOKBACK,
    FETCH_WORKERS,
    RSS_FETCH_RETRIES,
    RSS_FETCH_TIMEOUT,
    RSS_SUMMARY_MAX_CHARS,
)
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
    """Fetch and parse an RSS feed, with timeout and retry on transient errors."""
    items: List[Dict] = []
    source_name = source["name"]
    url = source["url"]

    # Retry transient failures (network blips, slow feeds) up to RSS_FETCH_RETRIES
    # times.  feedparser raises on hard transport errors and we re-parse on retries.
    feed = None
    last_error: Optional[Exception] = None
    for attempt in range(1, RSS_FETCH_RETRIES + 2):  # 1 initial + N retries
        try:
            logger.debug(
                "Fetching RSS feed: %s (attempt %d/%d)",
                source_name, attempt, RSS_FETCH_RETRIES + 1,
            )
            # feedparser 6.x doesn't follow 30x redirects on its own
            # (the body's parsed as if it were the final response, which
            # on some sites is an empty HTML stub), and it sends a default
            # python-urllib User-Agent that several big AI sites block
            # outright.  Use requests for the transport (so redirects
            # actually follow and we send a real UA), then hand the
            # response body to feedparser for XML/Atom parsing.
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (compatible; AIPulse/1.0; "
                    "+https://github.com/yanchangchen/ai-pulse)"
                ),
                "Accept": "application/rss+xml, application/atom+xml, "
                          "application/xml;q=0.9, */*;q=0.8",
            }
            resp = requests.get(
                url,
                headers=headers,
                timeout=RSS_FETCH_TIMEOUT,
                allow_redirects=True,
            )
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            if feed.bozo and not feed.entries:
                # Malformed feed — not a transient error, give up immediately.
                logger.warning("Feed may be malformed: %s", source_name)
                return items
            last_error = None
            break
        except Exception as e:
            last_error = e
            if attempt <= RSS_FETCH_RETRIES:
                # Exponential-ish backoff: 0.5s, 1.0s, 1.5s ...
                time.sleep(0.5 * attempt)
                continue
            logger.error(
                "Error fetching %s after %d attempts: %s",
                source_name, attempt, e,
            )
            return items

    if feed is None:
        # Should not reach here, but be defensive.
        if last_error:
            logger.error("Error fetching %s: %s", source_name, last_error)
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

        # Use extracted publish date, defaulting to current crawl timestamp if missing
        crawl_dt = datetime.now(timezone.utc)
        pub_date_str = (dt or crawl_dt).isoformat()

        item = {
            'id': item_id,
            'title': title,
            'summary': summary[:RSS_SUMMARY_MAX_CHARS] if summary else '',
            'link': link,
            'published_date': pub_date_str,
            'source_name': source_name
        }
        items.append(item)

    logger.debug("Fetched %d items from %s", len(items), source_name)
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

                # Apply the same freshness filter used in the heuristic branch
                # below — keeps old items from leaking through when the page
                # doesn't expose a parseable date.
                if not is_within_range(dt):
                    continue

                # Extract summary/dek text
                summary = ""
                summary_sel = selectors.get("summary")
                container = parent_a if parent_a else title_elem.parent

                if summary_sel and container:
                    sum_elem = container.find(summary_sel) or container.find_next_sibling(summary_sel)
                    if sum_elem and sum_elem != title_elem:
                        summary = sum_elem.get_text(separator=' ', strip=True)

                if not summary and container:
                    p_elem = container.find('p') or container.find_next_sibling('p')
                    if p_elem:
                        summary = p_elem.get_text(separator=' ', strip=True)

                item_id = hashlib.md5(f"{link}{title}".encode()).hexdigest()

                items.append({
                    'id': item_id,
                    'title': title,
                    'summary': summary[:RSS_SUMMARY_MAX_CHARS] if summary else '',
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

                summary = ""
                p_elem = elem.find('p')
                if p_elem:
                    summary = p_elem.get_text(separator=' ', strip=True)

                item_id = hashlib.md5(f"{link}{title}".encode()).hexdigest()

                items.append({
                    'id': item_id,
                    'title': title,
                    'summary': summary[:RSS_SUMMARY_MAX_CHARS] if summary else '',
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


def diagnose_source(source: Dict) -> Dict:
    """
    Perform a deep diagnostic probe on a news source to evaluate health, HTTP status,
    Content-Type, RSS/HTML structure, and generate plain-English troubleshooting advice.
    """
    source_name = source["name"]
    url = source["url"]
    source_type = source.get("type", "rss")
    
    start_time = time.time()
    result = {
        "name": source_name,
        "url": url,
        "type": source_type,
        "status_code": None,
        "latency_ms": 0,
        "content_type": "",
        "content_length": 0,
        "items_found": 0,
        "healthy": False,
        "error_summary": "",
        "explanation": "",
        "recommendation": ""
    }
    
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/rss+xml, application/atom+xml, text/html, application/xhtml+xml, */*;q=0.8"
    }

    try:
        resp = requests.get(url, headers=headers, timeout=12, allow_redirects=True)
        latency = int((time.time() - start_time) * 1000)
        result["latency_ms"] = latency
        result["status_code"] = resp.status_code
        result["content_type"] = resp.headers.get("Content-Type", "")
        result["content_length"] = len(resp.content)

        if resp.status_code != 200:
            result["healthy"] = False
            result["error_summary"] = f"HTTP {resp.status_code} Error"
            if resp.status_code == 404:
                result["explanation"] = "The server returned 404 Not Found. The feed or blog endpoint URL has been moved or deprecated by the content publisher."
                result["recommendation"] = "Search the provider's website for an updated RSS endpoint URL or switch to web scraping mode with CSS selectors."
            elif resp.status_code == 403:
                result["explanation"] = "The server returned 403 Forbidden. The provider blocks automated scraping or requires specific headers/cookies."
                result["recommendation"] = "Verify User-Agent header rules or check if the site uses Cloudflare anti-bot protection."
            elif resp.status_code >= 500:
                result["explanation"] = f"The publisher's server encountered an internal error (HTTP {resp.status_code})."
                result["recommendation"] = "This is typically a transient server failure. Retry later."
            else:
                result["explanation"] = f"Unexpected HTTP response code {resp.status_code}."
                result["recommendation"] = "Inspect response headers and authentication requirements."
            return result

        # Processing HTTP 200 response
        if source_type == "rss":
            feed = feedparser.parse(resp.content)
            entries_count = len(feed.entries)
            result["items_found"] = entries_count

            if entries_count > 0:
                result["healthy"] = True
                result["error_summary"] = "None (Healthy)"
                result["explanation"] = f"Successfully parsed {entries_count} RSS/Atom feed entries."
                result["recommendation"] = "No action needed."
            else:
                result["healthy"] = False
                if feed.bozo:
                    result["error_summary"] = "Malformed XML Feed"
                    result["explanation"] = "The URL returned HTTP 200, but the body contains HTML or malformed XML rather than valid RSS/Atom tags."
                    result["recommendation"] = "Switch source type to 'web' with CSS selectors or check for dedicated XML feed endpoint."
                else:
                    result["error_summary"] = "0 Feed Entries Found"
                    result["explanation"] = "Valid XML feed structure returned, but contains 0 active article entries."
                    result["recommendation"] = "Check if articles fall outside the lookback window or if feed requires parameters."

        elif source_type == "web":
            soup = BeautifulSoup(resp.content, 'html.parser')
            selectors = _get_scrape_selectors(source_name)
            
            if selectors:
                title_elems = soup.select(selectors.get("title", "h2, h3"))
                found_titles = [t.get_text(strip=True) for t in title_elems if len(t.get_text(strip=True)) > 10]
                result["items_found"] = len(found_titles)
            else:
                article_elements = soup.find_all(['article', 'div', 'li', 'a'])
                result["items_found"] = min(len(article_elements), 20)

            if result["items_found"] > 0:
                result["healthy"] = True
                result["error_summary"] = "None (Healthy)"
                result["explanation"] = f"Successfully scraped {result['items_found']} article elements using BeautifulSoup."
                result["recommendation"] = "No action needed."
            else:
                result["healthy"] = False
                result["error_summary"] = "0 Scraped Elements"
                result["explanation"] = "The page loaded HTTP 200, but no HTML elements matched the configured CSS selectors. The site may render content dynamically via client-side JavaScript (Next.js/React)."
                result["recommendation"] = "Update CSS selectors in config/sources.py (WEB_SCRAPE_SOURCES)."

    except requests.exceptions.Timeout:
        result["healthy"] = False
        result["error_summary"] = "Request Timeout"
        result["explanation"] = "The connection timed out after 12 seconds."
        result["recommendation"] = "Check publisher server uptime or increase timeout configuration."
    except Exception as exc:
        result["healthy"] = False
        result["error_summary"] = f"Fetch Error: {type(exc).__name__}"
        result["explanation"] = f"An exception occurred while querying source: {exc}"
        result["recommendation"] = "Inspect network connection or endpoint configuration."

    return result
