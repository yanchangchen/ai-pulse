# Implementation Plan: Phase 4.2 — Fallback Ledger Asymmetry Fix

**Selected Design**: **Option B** (Union of Section 1 lead articles + Section 5 Further Reading articles).  
**Status**: Ready for implementation (saved for execution).  
**Tracking Issue**: [Future.md](../Future.md) — Item 4.2.

---

## 1. Executive Summary & Root Cause

### The Problem
When the Model Gateway triggers a fallback (e.g. gateway deterministic fallback, refusal text detection, or unhandled gateway exception), or when the user selects `"⚡ Non-LLM Extractive Only"` mode, `core/summariser.py` calls `extractive_theme_summary(theme_name, articles)`.

Currently, `extractive_theme_summary()` does:
```python
included_hashes = [processed_articles.article_hash(a) for a in articles]
```
This treats **all** articles passed into the fallback as "processed" and writes them into `processed_articles`.

### Why This Is Harmful
1. **Silent Backlog Drain**: If 35 articles are passed to the fallback, all 35 are marked as processed. Next run, `processed_articles.filter_unprocessed(theme, articles)` removes them, so the LLM will **never** synthesize these articles even after quota or gateway recovery.
2. **Deceptive 4.1 Visibility**: 4.1 reports `35 of 35 articles reflected` with `0 in backlog`, when only ~3–5 articles were actually cited.

### The Solution (Option B)
Only mark articles as processed if they were **explicitly surfaced by name and link** in the generated brief:
- **Section 1 ("WHAT IS HAPPENING")**: up to 3 lead articles (`top_articles`).
- **Section 5 ("STRATEGIC FURTHER READING")**: up to 5 curated articles (`reading_pool`).
- **Surfaced Set**: $\text{top\_articles} \cup \text{reading\_pool}$ (typically 3 to 5 unique articles).

All other articles (the remaining ~30) remain **unprocessed** in the backlog, so:
- They stay eligible for full LLM synthesis in future runs.
- 4.1 truthful metrics show: `5 of 35 articles reflected (30 in backlog)`.

---

## 2. File-by-File Implementation Steps

### Step 1: Track Surfaced Articles in `core/non_llm_summariser.py`

**File**: [core/non_llm_summariser.py](file:///c:/claude/ai-pulse/core/non_llm_summariser.py)  
**Target Function**: `generate_non_llm_theme_summary(theme_name: str, articles: List[Dict]) -> Dict[str, Any]` (around line 263)

#### Changes:
1. In Section 1, `top_articles` collects up to 3 articles (already in code, lines 314–340).
2. In Section 6 (Section 5 header), `reading_pool` collects up to 5 articles (already in code, line 431).
3. Compute the union of surfaced articles, preserving uniqueness by content hash or object identity:
   ```python
   # Deduplicate surfaced articles by article content hash (or title/link fallback)
   surfaced_map = {}
   for a in top_articles + reading_pool:
       h = a.get("content_hash") or f"{a.get('title')}:{a.get('link')}"
       if h not in surfaced_map:
           surfaced_map[h] = a
   surfaced_articles = list(surfaced_map.values())
   ```
4. Attach `_surfaced_articles` to the returned dictionary:
   ```python
   return {
       "what_is_happening": what_happening,
       "engineering_tradeoffs": eng_text,
       "product_impact": prod_text,
       "why_it_matters": why_matters,
       "what_to_watch": what_to_watch,
       "further_reading": further_reading,
       "_surfaced_articles": surfaced_articles,
   }
   ```
   *Note: Adding `_surfaced_articles` as an extra key keeps full backwards compatibility with any existing caller expecting string keys.*

---

### Step 2: Fix Ledger Asymmetry in `core/summariser.py`

**File**: [core/summariser.py](file:///c:/claude/ai-pulse/core/summariser.py)  
**Target Function 1**: `extractive_theme_summary(theme_name: str, articles: List[Dict])` (around line 880)

#### Changes:
1. Extract the actually-surfaced articles:
   ```python
   summary = generate_non_llm_theme_summary(theme_name, articles)
   surfaced_articles = summary.get("_surfaced_articles")
   if surfaced_articles is None:
       # Safe fallback: at least the top 3 by relevance
       surfaced_articles = articles[:3]

   included_hashes = [
       processed_articles.article_hash(a) for a in surfaced_articles
   ]
   ```
2. Compute the truthful skipped complement:
   ```python
   surfaced_hash_set = set(included_hashes)
   skipped_articles = [
       a for a in articles if processed_articles.article_hash(a) not in surfaced_hash_set
   ]
   skipped_hashes = [
       processed_articles.article_hash(a) for a in skipped_articles
   ]
   skipped_payload = [
       {
           "title": a.get("title", ""),
           "source": a.get("source_name", a.get("source", "")),
           "link": a.get("link", ""),
           "hash": processed_articles.article_hash(a),
       }
       for a in skipped_articles
   ]
   ```
3. Update `summary` and `_generation_log`:
   ```python
   summary = _with_provenance(
       summary, source="extractive_fallback",
       model=None, article_count=len(articles),
       total_articles=len(articles),
       included_count=len(included_hashes),
       skipped_count=len(skipped_articles),
       skipped_hashes=skipped_hashes,
       skipped_articles=skipped_payload,
       note="non_llm_extractive",
   )
   summary["article_count"] = len(articles)
   summary["total_articles"] = len(articles)
   summary["included_count"] = len(included_hashes)
   summary["skipped_count"] = len(skipped_articles)
   summary["skipped_hashes"] = skipped_hashes
   summary["skipped_articles"] = skipped_payload
   return summary, included_hashes
   ```

**Target Function 2**: `_generate_all_summaries_async` (around line 958)

#### Changes:
In the user-mode check (`user_mode == "⚡ Non-LLM Extractive Only"`):
```python
if user_mode == "⚡ Non-LLM Extractive Only":
    logger.info("User selected Non-LLM Extractive Only mode. Generating LexRank/Luhn summary for %s", theme)
    summary, included = extractive_theme_summary(theme, articles)
    summaries[theme] = summary
    if included:
        processed_articles.mark_processed(theme, included)
    processed_per_theme[theme] = included
    any_synthesised = True
    continue
```
*Note: This ensures that user-selected non-LLM mode also records only surfaced articles as processed, instead of skipping ledger updates or marking all.*

---

### Step 3: Targeted Unit Tests in `tests/test_summariser.py`

**File**: [tests/test_summariser.py](file:///c:/claude/ai-pulse/tests/test_summariser.py)

#### Test Cases to Add:
1. `test_extractive_fallback_marks_only_surfaced_articles()`:
   - Provide 20 articles to `extractive_theme_summary("Frontier Models & Benchmarks", articles)`.
   - Assert `len(included_hashes) <= 8` (typically 3 to 5), and strictly `< 20`.
   - Assert `summary["included_count"] == len(included_hashes)`.
   - Assert `summary["skipped_count"] == 20 - len(included_hashes)`.
   - Assert `summary["total_articles"] == 20`.
   - Assert `len(summary["skipped_articles"]) == summary["skipped_count"]`.

2. `test_extractive_fallback_surfaced_articles_are_in_summary_text()`:
   - Verify every article hash in `included_hashes` corresponds to an article whose title appears in either `summary["what_is_happening"]` or `summary["further_reading"]`.

3. `test_non_llm_mode_backlog_carryover()`:
   - Simulate a run with 15 articles under non-LLM mode.
   - Verify `processed_articles.filter_unprocessed()` on the same theme still retains the ~10–12 unsurfaced articles for future runs.

---

## 3. Verification Commands

To verify once implemented (Fast Inner Loop):
```bash
python -m pytest tests/test_summariser.py tests/test_non_llm_summariser.py
```

Expected execution time: `< 2.5s`.

---

## 4. Post-Implementation Checklist

- [ ] Apply edits to `core/non_llm_summariser.py`
- [ ] Apply edits to `core/summariser.py`
- [ ] Add tests to `tests/test_summariser.py`
- [ ] Run targeted pytest suite
- [ ] Update checkbox `4.2` in [Future.md](../Future.md)
