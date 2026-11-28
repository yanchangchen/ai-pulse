"""
Monthly synthesis rollups (Phase 6.1 — Tier-3 hierarchical memory).

Generates one macro synthesis per theme per month from that month's Tier-2
daily theme summaries, stored in Supabase `monthly_syntheses`. Sage's
tier-aware consumption lands in Phase 6.2.

Cadence rules (compute_due_syntheses):
* a (month, theme) with no row is due — completed months are synthesized
  once, idempotent;
* the CURRENT month re-synthesizes every `monthly_refresh_days` (default 14);
* a completed month whose row was written mid-month (updated_at before the
  next month's start, UTC) regenerates once after rollover so the final
  rollup covers the full month — then never again.

Runners: best-effort post-save step in core/bg_refresher.py and the
`backfill_monthly_syntheses.py` CLI (future cron entry point) share
`synthesize_due_monthly`. Supabase is the single source of truth: offline
or pre-migration the job skips silently; it never raises and never fails
the pipeline. Gateway failure writes no row, so the pair is retried on the
next run.
"""

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from config.themes import THEME_ORDER
from core import supabase_client
from core.ai_gateway import get_gateway, AITaskRequest, TaskType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s\)\]\|,<>\"']+")
_JSON_FENCE_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_BARE_FENCE_RE = re.compile(r"```\s*(\[.*?\])\s*```", re.DOTALL)


# ---------------------------------------------------------------------------
# Due-check (pure, unit-testable)
# ---------------------------------------------------------------------------

def _parse_ts(value) -> Optional[datetime]:
    """Parse an ISO timestamp; naive values are assumed UTC."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _next_month_start(year_month: str) -> datetime:
    """First day of the following month, 00:00 UTC."""
    year, month = int(year_month[:4]), int(year_month[5:7])
    if month == 12:
        return datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(year, month + 1, 1, tzinfo=timezone.utc)


def compute_due_syntheses(
    existing_rows: List[Dict],
    months_with_data: List[str],
    themes: List[str],
    today: Optional[date] = None,
    refresh_days: int = 14,
    force: bool = False,
    force_months: Optional[List[str]] = None,
) -> List[Tuple[str, str]]:
    """Which (year_month, theme_name) pairs need (re-)synthesis.

    Sorted oldest month first, `themes` order within a month, so the
    archive drains chronologically. `today` is injectable for tests; when
    given, age arithmetic uses that date at 00:00 UTC.
    """
    now = (
        datetime.now(timezone.utc) if today is None
        else datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    )
    current_month = now.strftime("%Y-%m")
    existing = {
        (r.get("year_month"), r.get("theme_name")): r
        for r in (existing_rows or [])
    }
    force_set = set(force_months) if force_months else None

    due: List[Tuple[str, str]] = []
    for month in sorted(set(months_with_data or [])):
        if month > current_month:
            continue  # no future months
        for theme in themes:
            if force and (force_set is None or month in force_set):
                due.append((month, theme))
                continue
            row = existing.get((month, theme))
            if row is None:
                due.append((month, theme))
                continue
            updated = _parse_ts(row.get("updated_at"))
            if updated is None:
                due.append((month, theme))  # unreadable stamp -> regenerate
                continue
            if month == current_month:
                age = max(now - updated, timedelta(0))  # clamp clock skew
                if age >= timedelta(days=refresh_days):
                    due.append((month, theme))
            elif updated < _next_month_start(month):
                # Written mid-month: regenerate once after rollover.
                due.append((month, theme))
    return due


# ---------------------------------------------------------------------------
# Input assembly
# ---------------------------------------------------------------------------

def _truncate(text: str, budget: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= budget else text[: max(budget - 1, 0)] + "…"


def _usable_briefs(briefs: List[Dict]) -> List[Dict]:
    """Drop skipped-placeholder and empty briefs; chronological order."""
    usable = [
        b for b in (briefs or [])
        if b.get("generation_source") != "gateway:skipped"
        and (b.get("what_is_happening") or "").strip()
    ]
    usable.sort(key=lambda b: (str(b.get("run_date") or ""), str(b.get("run_timestamp") or "")))
    return usable


def _build_rollup_input(
    briefs: List[Dict], max_input_chars: int, max_runs: int,
) -> Tuple[str, int]:
    """Render one theme-month's briefs into bounded chronological blocks.

    Feeds only the narrative pillars (what_is_happening / why_it_matters /
    what_to_watch). If more than `max_runs` briefs remain, keeps an
    evenly-spaced chronological sample including first and last so month
    coverage is preserved. LINKS lines carry up to 3 real URLs extracted
    from further_reading — the only URL source the model may cite.
    Returns (blocks_text, n_briefs_used).
    """
    usable = _usable_briefs(briefs)
    if not usable:
        return "", 0
    if max_runs and len(usable) > max_runs:
        n = len(usable)
        if max_runs == 1:
            usable = [usable[-1]]
        else:
            idx = sorted({round(i * (n - 1) / (max_runs - 1)) for i in range(max_runs)})
            usable = [usable[i] for i in idx]

    count = len(usable)
    per_brief = max(1200, int(max_input_chars) // count)
    happening_budget = int(per_brief * 0.6)
    why_budget = int(per_brief * 0.2)
    watch_budget = per_brief - happening_budget - why_budget

    blocks: List[str] = []
    for b in usable:
        lines = [
            f"--- {b.get('run_date') or 'unknown date'} ---",
            f"WHAT HAPPENED: {_truncate(b.get('what_is_happening', ''), happening_budget)}",
            f"WHY IT MATTERS: {_truncate(b.get('why_it_matters', ''), why_budget)}",
            f"WATCHLIST: {_truncate(b.get('what_to_watch', ''), watch_budget)}",
        ]
        links = list(dict.fromkeys(_URL_RE.findall(b.get("further_reading") or "")))[:3]
        if links:
            lines.append("LINKS: " + " ".join(links))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks), count


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_synthesis_response(text: str) -> Tuple[str, List[Dict]]:
    """Split the response into (macro prose, validated milestones list).

    Milestones come from the last ```json fence (fallbacks: bare fence,
    first-[…last-] slice). Any parse/validation failure yields [] while
    keeping the prose — a synthesis is never failed over its milestones.
    """
    text = str(text or "")
    json_str = None
    prose_end = len(text)

    fences = list(_JSON_FENCE_RE.finditer(text))
    if fences:
        json_str = fences[-1].group(1)
        prose_end = fences[-1].start()
    else:
        bare = list(_BARE_FENCE_RE.finditer(text))
        if bare:
            json_str = bare[-1].group(1)
            prose_end = bare[-1].start()
        else:
            first, last = text.find("["), text.rfind("]")
            if 0 <= first < last:
                json_str = text[first:last + 1]
                prose_end = first

    prose = text[:prose_end].strip()
    # Drop a leading "PART 1 — MACRO SUMMARY" style header if present.
    prose = re.sub(r"^\s*PART\s*1[^\n]*\n?", "", prose, flags=re.IGNORECASE).strip()

    milestones: List[Dict] = []
    if json_str:
        try:
            parsed = json.loads(json_str)
            if isinstance(parsed, list):
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    milestone = item.get("milestone")
                    if not isinstance(milestone, str) or not milestone.strip():
                        continue
                    entry = {"milestone": milestone.strip()}
                    d = item.get("date")
                    entry["date"] = str(d) if d else None
                    link = item.get("source_link")
                    if isinstance(link, str) and link.startswith("http"):
                        entry["source_link"] = link
                    milestones.append(entry)
                    if len(milestones) >= 12:
                        break
        except (ValueError, TypeError):
            milestones = []
    return prose, milestones


# ---------------------------------------------------------------------------
# LLM synthesis (one theme-month)
# ---------------------------------------------------------------------------

async def _synthesize_theme_month(
    theme_name: str, year_month: str, briefs: List[Dict], settings: Dict,
) -> Optional[Dict]:
    """One SYNTHESISE gateway call; None on any failure (no row written).

    allow_deterministic_fallback=False: an extractive "macro synthesis"
    would be misleading, so a failed call is retried on the next run
    instead of persisting a degraded rollup.
    """
    blocks, n_used = _build_rollup_input(
        briefs,
        int(settings.get("monthly_synthesis_max_input_chars", 120_000)),
        int(settings.get("monthly_synthesis_max_runs", 40)),
    )
    if not n_used:
        return None

    usable = _usable_briefs(briefs)
    first_date = usable[0].get("run_date") or year_month
    last_date = usable[-1].get("run_date") or year_month

    system_prompt = (
        "You are an expert AI engineering analyst writing a monthly macro "
        "synthesis for senior tech leaders. Precision over hype; flowing "
        "prose; never invent facts, dates, or URLs."
    )
    user_prompt = f"""Write the MONTHLY MACRO SYNTHESIS for theme "{theme_name}" covering {year_month}
({n_used} daily briefs, {first_date} to {last_date}).

--- DAILY BRIEFS (chronological) ---
{blocks}
--- END OF DAILY BRIEFS ---

Produce exactly two parts:

PART 1 — MACRO SUMMARY: flowing prose, 300-450 words. The month's arc: the 3-6
dominant developments and how they connect, chronological where dates are given.
PRESERVATION MANDATE: name every major model, company, or release that recurs
across the briefs or marks a turning point — compress wording, never coverage.
Cite source names in parentheses. No bullet points. No facts, dates, or figures
absent from the briefs.

PART 2 — KEY MILESTONES: a fenced ```json block containing a list of 5-12 objects:
[{{"date": "YYYY-MM-DD", "milestone": "one concrete sentence", "source_link": "https://..."}}]
- date: the brief date where the development first appeared; null if unclear.
- source_link: ONLY a URL copied VERBATIM from a LINKS line above; omit the key
  entirely when no matching link exists. Never invent URLs.
Output nothing after the JSON block.
"""
    request = AITaskRequest(
        task=TaskType.SYNTHESISE,
        input=f"System: {system_prompt}\n\nUser: {user_prompt}",
        temperature=float(settings.get("monthly_synthesis_temperature", 0.3)),
        max_tokens=int(settings.get("monthly_synthesis_max_tokens", 2500)),
        allow_deterministic_fallback=False,
        metadata={"year_month": year_month, "theme_name": theme_name},
    )
    try:
        result = await get_gateway().execute(request)
    except Exception as exc:
        logger.warning("Monthly synthesis call failed for %s/%s: %s", year_month, theme_name, exc)
        return None

    if not result.is_success():
        logger.warning("Monthly synthesis failed for %s/%s (no row written; retried next run)", year_month, theme_name)
        return None
    prov = result.provenance
    if prov.method == "deterministic":
        logger.info("Monthly synthesis got deterministic fallback for %s/%s; skipping", year_month, theme_name)
        return None

    prose, milestones = _parse_synthesis_response(str(result.result))
    if not prose.strip():
        logger.warning("Monthly synthesis returned empty prose for %s/%s; skipping", year_month, theme_name)
        return None

    return {
        "macro_summary": prose,
        "key_milestones": milestones,
        "source_run_count": n_used,
        "generation_source": f"{prov.provider}:{prov.model}" if prov.provider else "llm:unknown",
    }


# ---------------------------------------------------------------------------
# Shared runner (pipeline hook + CLI)
# ---------------------------------------------------------------------------

def synthesize_due_monthly(
    limit: Optional[int] = None,
    months: Optional[List[str]] = None,
    force: bool = False,
    dry_run: bool = False,
    progress: Optional[Callable[[str], None]] = None,
) -> int:
    """Synthesize all due (month, theme) pairs; returns the count written.

    limit: None -> the pipeline cost cap (`monthly_synthesis_max_per_run`);
    0 -> unlimited (CLI backfill default); >0 -> explicit cap.
    months: restrict to these 'YYYY-MM' values (CLI --month).
    Never raises; offline/disabled/pre-migration returns 0 silently.
    """
    def _report(msg: str) -> None:
        if progress:
            try:
                progress(msg)
            except Exception:
                pass
        else:
            logger.info(msg)

    written = 0
    try:
        from config.settings import get_monthly_synthesis_settings
        settings = get_monthly_synthesis_settings()
        if not settings.get("monthly_synthesis_enabled", True):
            return 0

        manager = supabase_client.get_supabase_manager()
        if not manager.is_available():
            logger.debug("Monthly synthesis skipped: Supabase unavailable")
            return 0

        existing = manager.get_monthly_syntheses() or []
        months_with_data = manager.get_run_months() or []
        due = compute_due_syntheses(
            existing, months_with_data, THEME_ORDER,
            refresh_days=int(settings.get("monthly_refresh_days", 14)),
            force=force, force_months=months if force else None,
        )
        if months:
            month_set = set(months)
            due = [(m, t) for (m, t) in due if m in month_set]

        cap = int(settings.get("monthly_synthesis_max_per_run", 8)) if limit is None else int(limit)
        if cap > 0:
            due = due[:cap]
        if not due:
            return 0

        if dry_run:
            for month, theme in due:
                _report(f"[ROLLUP] due: {month} - {theme}")
            return len(due)

        # Group by month so each month's briefs are fetched once for all themes.
        by_month: Dict[str, List[str]] = {}
        for month, theme in due:
            by_month.setdefault(month, []).append(theme)

        async def _run() -> int:
            count = 0
            for month, themes in by_month.items():
                briefs = manager.get_theme_briefs_for_month(month) or []
                by_theme: Dict[str, List[Dict]] = {}
                for b in briefs:
                    by_theme.setdefault(b.get("theme_name"), []).append(b)
                for theme in themes:
                    try:
                        result = await _synthesize_theme_month(
                            theme, month, by_theme.get(theme, []), settings,
                        )
                        if result is None:
                            _report(f"[ROLLUP] {month} - {theme}: skipped (no briefs or synthesis failed)")
                            continue
                        ok = manager.upsert_monthly_synthesis(
                            month, theme,
                            result["macro_summary"], result["key_milestones"],
                            result["source_run_count"], result["generation_source"],
                        )
                        if ok:
                            count += 1
                            _report(
                                f"[ROLLUP] {month} - {theme}: synthesized from "
                                f"{result['source_run_count']} briefs ({result['generation_source']})"
                            )
                        else:
                            _report(f"[ROLLUP] {month} - {theme}: write failed")
                    except Exception as exc:
                        logger.warning("Monthly synthesis error for %s/%s: %s", month, theme, exc)
            return count

        written = asyncio.run(_run())
        return written
    except Exception as exc:
        logger.warning("Monthly synthesis step failed: %s", exc)
        return written