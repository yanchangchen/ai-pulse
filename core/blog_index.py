"""Parse and search the development blog (``BLOG.md``).

The blog is a newest-first sequence of ``## <date range> — <title>`` entries.
This module splits it into searchable entries for the Dev Blog UI page
(``pages/10_Blog.py``). Pure functions, no Streamlit — unit-testable.
"""
import re
from typing import Dict, List, Optional

_ENTRY_HEADING = re.compile(r"^##\s+(?P<title>.+?)\s*$", re.MULTILINE)
_LABELS_LINE = re.compile(r"^labels:\s*(.+)$", re.IGNORECASE)


def _extract_labels(body: str):
    """Pull a leading ``Labels: a, b, c`` line out of an entry body.

    The line is metadata for the UI filter, not prose — it is removed from
    the rendered body and returned as a lowercase label list. Entries
    without the line get ``[]`` (backwards compatible).
    """
    first, sep, rest = body.partition("\n")
    m = _LABELS_LINE.match(first.strip())
    if not m:
        return [], body
    labels = [t.strip().lower() for t in m.group(1).split(",") if t.strip()]
    return labels, (rest.strip() if sep else "")


def split_blog_entries(markdown_text: str) -> List[Dict]:
    """Split blog markdown into entries, preserving file order (newest first).

    Each entry: ``{"title": str, "date_label": str, "body": str,
    "labels": List[str]}``.
    ``date_label`` is the leading date range when the heading follows the
    ``## YYYY-MM-DD[ to YYYY-MM-DD] — Title`` convention, else "".
    ``labels`` comes from an optional ``Labels: a, b, c`` line immediately
    after the heading (stripped from the body).
    Content before the first ``##`` heading (the blog preamble) is dropped.
    """
    if not markdown_text:
        return []
    matches = list(_ENTRY_HEADING.finditer(markdown_text))
    entries: List[Dict] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown_text)
        body = markdown_text[start:end].strip()
        heading = m.group("title")
        date_label = ""
        title = heading
        # "2026-11-10 to 2026-11-11 — Title" or "2026-03-03 — Title"
        dm = re.match(r"^(\d{4}-\d{2}-\d{2}(?:\s+to\s+\d{4}-\d{2}-\d{2})?)\s*[—–-]\s*(.*)$", heading)
        if dm:
            date_label = dm.group(1).strip()
            title = dm.group(2).strip() or heading
        labels, body = _extract_labels(body)
        entries.append({
            "title": title, "date_label": date_label,
            "body": body, "labels": labels,
        })
    return entries


def collect_labels(entries: List[Dict]) -> List[str]:
    """Sorted unique labels across entries (for the UI filter widget)."""
    return sorted({l for e in entries for l in e.get("labels", [])})


def filter_entries(
    entries: List[Dict], query: str = "", labels: Optional[List[str]] = None,
) -> List[Dict]:
    """Search + label filter combined.

    Search terms are AND (every term must appear); selected labels are OR
    (an entry matches when it carries ANY selected label). The two filters
    intersect. Empty query and empty/None labels return all entries.
    """
    out = search_entries(entries, query)
    if labels:
        wanted = {str(l).strip().lower() for l in labels if str(l).strip()}
        out = [e for e in out if wanted & set(e.get("labels", []))]
    return out


def search_entries(entries: List[Dict], query: str) -> List[Dict]:
    """Case-insensitive AND search across entry title + body.

    Every whitespace-separated term must appear somewhere in the entry
    (substring match, so partial words work). Empty/whitespace query returns
    all entries unchanged.
    """
    terms = [t.lower() for t in (query or "").split() if t.strip()]
    if not terms:
        return list(entries)
    out = []
    for e in entries:
        haystack = f"{e.get('title', '')} {e.get('date_label', '')} {e.get('body', '')}".lower()
        if all(t in haystack for t in terms):
            out.append(e)
    return out