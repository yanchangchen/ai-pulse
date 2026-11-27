"""Parse and search the development blog (``BLOG.md``).

The blog is a newest-first sequence of ``## <date range> — <title>`` entries.
This module splits it into searchable entries for the Dev Blog UI page
(``pages/10_Blog.py``). Pure functions, no Streamlit — unit-testable.
"""
import re
from typing import Dict, List

_ENTRY_HEADING = re.compile(r"^##\s+(?P<title>.+?)\s*$", re.MULTILINE)


def split_blog_entries(markdown_text: str) -> List[Dict]:
    """Split blog markdown into entries, preserving file order (newest first).

    Each entry: ``{"title": str, "date_label": str, "body": str}``.
    ``date_label`` is the leading date range when the heading follows the
    ``## YYYY-MM-DD[ to YYYY-MM-DD] — Title`` convention, else "".
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
        entries.append({"title": title, "date_label": date_label, "body": body})
    return entries


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