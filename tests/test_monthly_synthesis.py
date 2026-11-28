"""
Tests for core/monthly_synthesis.py (Phase 6.1 — Tier-3 monthly rollups).

All offline: the conftest autouse stub keeps Supabase unavailable unless a
test patches core.supabase_client.get_supabase_manager with its own mock.
"""

import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.monthly_synthesis import (
    _build_rollup_input,
    _parse_synthesis_response,
    _synthesize_theme_month,
    compute_due_syntheses,
    synthesize_due_monthly,
)
from core.ai_gateway import TaskType

TODAY = date(2026, 11, 28)
THEMES = ["Theme A", "Theme B"]

_SETTINGS = {
    "monthly_synthesis_enabled": True,
    "monthly_refresh_days": 14,
    "monthly_synthesis_max_tokens": 2500,
    "monthly_synthesis_temperature": 0.3,
    "monthly_synthesis_max_input_chars": 120_000,
    "monthly_synthesis_max_runs": 40,
    "monthly_synthesis_max_per_run": 8,
}


def _row(month, theme, updated_at):
    return {"year_month": month, "theme_name": theme, "updated_at": updated_at}


def _brief(run_date, theme="Theme A", happening="Something happened.", **extra):
    return {
        "run_date": run_date,
        "run_timestamp": f"{run_date}T12:00:00+00:00",
        "theme_name": theme,
        "what_is_happening": happening,
        "why_it_matters": "It matters.",
        "what_to_watch": "Watch this.",
        **extra,
    }


# ---------------------------------------------------------------------------
# compute_due_syntheses
# ---------------------------------------------------------------------------

def test_missing_completed_month_is_due():
    due = compute_due_syntheses([], ["2026-06"], THEMES, today=TODAY)
    assert due == [("2026-06", "Theme A"), ("2026-06", "Theme B")]


def test_fresh_current_month_not_due():
    rows = [_row("2026-11", "Theme A", "2026-11-20T00:00:00+00:00")]
    due = compute_due_syntheses(rows, ["2026-11"], ["Theme A"], today=TODAY)
    assert due == []


def test_current_month_stale_boundary_exactly_refresh_days():
    rows = [_row("2026-11", "Theme A", "2026-11-14T00:00:00+00:00")]
    due = compute_due_syntheses(rows, ["2026-11"], ["Theme A"], today=TODAY, refresh_days=14)
    assert due == [("2026-11", "Theme A")]
    # One day fresher -> not due.
    rows = [_row("2026-11", "Theme A", "2026-11-15T00:00:00+00:00")]
    assert compute_due_syntheses(rows, ["2026-11"], ["Theme A"], today=TODAY) == []


def test_completed_month_written_mid_month_regenerates_once():
    rows = [_row("2026-10", "Theme A", "2026-10-20T00:00:00+00:00")]
    due = compute_due_syntheses(rows, ["2026-10"], ["Theme A"], today=TODAY)
    assert due == [("2026-10", "Theme A")]
    # Written after month end -> final, never due again.
    rows = [_row("2026-10", "Theme A", "2026-11-02T00:00:00+00:00")]
    assert compute_due_syntheses(rows, ["2026-10"], ["Theme A"], today=TODAY) == []


def test_month_without_data_excluded():
    assert compute_due_syntheses([], [], THEMES, today=TODAY) == []


def test_future_month_excluded():
    assert compute_due_syntheses([], ["2026-12"], THEMES, today=TODAY) == []


def test_force_and_force_months():
    rows = [
        _row("2026-10", "Theme A", "2026-11-02T00:00:00+00:00"),
        _row("2026-11", "Theme A", "2026-11-27T00:00:00+00:00"),
    ]
    months = ["2026-10", "2026-11"]
    due = compute_due_syntheses(rows, months, ["Theme A"], today=TODAY, force=True)
    assert due == [("2026-10", "Theme A"), ("2026-11", "Theme A")]
    due = compute_due_syntheses(
        rows, months, ["Theme A"], today=TODAY, force=True, force_months=["2026-10"],
    )
    assert due == [("2026-10", "Theme A")]


def test_due_order_oldest_month_first_then_theme_order():
    due = compute_due_syntheses([], ["2026-07", "2026-06"], THEMES, today=TODAY)
    assert due == [
        ("2026-06", "Theme A"), ("2026-06", "Theme B"),
        ("2026-07", "Theme A"), ("2026-07", "Theme B"),
    ]


def test_clock_skew_future_updated_at_clamped():
    rows = [_row("2026-11", "Theme A", "2026-11-30T00:00:00+00:00")]
    assert compute_due_syntheses(rows, ["2026-11"], ["Theme A"], today=TODAY) == []


def test_refresh_days_knob_respected():
    rows = [_row("2026-11", "Theme A", "2026-11-20T00:00:00+00:00")]
    due = compute_due_syntheses(rows, ["2026-11"], ["Theme A"], today=TODAY, refresh_days=7)
    assert due == [("2026-11", "Theme A")]


def test_unparseable_updated_at_regenerates():
    rows = [_row("2026-10", "Theme A", "not-a-timestamp")]
    due = compute_due_syntheses(rows, ["2026-10"], ["Theme A"], today=TODAY)
    assert due == [("2026-10", "Theme A")]


def test_naive_and_z_timestamps_parsed_as_utc():
    rows = [_row("2026-11", "Theme A", "2026-11-14T00:00:00Z")]
    assert compute_due_syntheses(rows, ["2026-11"], ["Theme A"], today=TODAY) == [("2026-11", "Theme A")]
    rows = [_row("2026-11", "Theme A", "2026-11-14T00:00:00")]
    assert compute_due_syntheses(rows, ["2026-11"], ["Theme A"], today=TODAY) == [("2026-11", "Theme A")]


# ---------------------------------------------------------------------------
# _build_rollup_input
# ---------------------------------------------------------------------------

def test_input_truncation_budget():
    long_brief = _brief("2026-06-01", happening="x" * 5000)
    text, n = _build_rollup_input([long_brief], max_input_chars=1200, max_runs=40)
    assert n == 1
    happening_line = next(l for l in text.splitlines() if l.startswith("WHAT HAPPENED:"))
    # per-brief floor 1200 -> 60% = 720 chars for the happening section.
    assert len(happening_line) <= len("WHAT HAPPENED: ") + 720
    assert happening_line.endswith("…")


def test_input_sampling_keeps_first_and_last():
    briefs = [_brief(f"2026-06-{d:02d}") for d in range(1, 11)]
    text, n = _build_rollup_input(briefs, max_input_chars=120_000, max_runs=4)
    assert n == 4
    assert "--- 2026-06-01 ---" in text
    assert "--- 2026-06-10 ---" in text


def test_input_drops_skipped_and_empty_briefs():
    briefs = [
        _brief("2026-06-01", generation_source="gateway:skipped"),
        _brief("2026-06-02", happening="   "),
        _brief("2026-06-03"),
    ]
    text, n = _build_rollup_input(briefs, max_input_chars=120_000, max_runs=40)
    assert n == 1
    assert "--- 2026-06-03 ---" in text
    assert "2026-06-01" not in text


def test_input_empty_when_no_usable_briefs():
    assert _build_rollup_input([], 120_000, 40) == ("", 0)


def test_input_links_extracted_deduped_and_capped():
    fr = (
        "- [A] | [S] | [https://a.example/1]\n"
        "- [B] | [S] | [https://b.example/2]\n"
        "- [C] | [S] | [https://a.example/1]\n"
        "- [D] | [S] | [https://c.example/3]\n"
        "- [E] | [S] | [https://d.example/4]\n"
    )
    text, _ = _build_rollup_input([_brief("2026-06-01", further_reading=fr)], 120_000, 40)
    links_line = next(l for l in text.splitlines() if l.startswith("LINKS:"))
    assert links_line == "LINKS: https://a.example/1 https://b.example/2 https://c.example/3"


def test_input_no_links_line_without_further_reading():
    text, _ = _build_rollup_input([_brief("2026-06-01")], 120_000, 40)
    assert "LINKS:" not in text


def test_input_chronological_order():
    briefs = [_brief("2026-06-05"), _brief("2026-06-01"), _brief("2026-06-03")]
    text, _ = _build_rollup_input(briefs, 120_000, 40)
    positions = [text.index(f"--- 2026-06-{d:02d} ---") for d in (1, 3, 5)]
    assert positions == sorted(positions)


# ---------------------------------------------------------------------------
# _parse_synthesis_response
# ---------------------------------------------------------------------------

def test_parse_clean_fenced_json():
    text = (
        "PART 1 — MACRO SUMMARY\nThe month saw major moves.\n\n"
        '```json\n[{"date": "2026-06-03", "milestone": "Model X launched", '
        '"source_link": "https://a.example/1"}]\n```'
    )
    prose, milestones = _parse_synthesis_response(text)
    assert prose == "The month saw major moves."
    assert milestones == [{
        "milestone": "Model X launched", "date": "2026-06-03",
        "source_link": "https://a.example/1",
    }]


def test_parse_bare_fence_and_unfenced_list():
    prose, m = _parse_synthesis_response('Prose here.\n```\n[{"milestone": "A"}]\n```')
    assert prose == "Prose here."
    assert m == [{"milestone": "A", "date": None}]
    prose, m = _parse_synthesis_response('Prose.\n[{"milestone": "B"}]')
    assert prose == "Prose."
    assert m[0]["milestone"] == "B"


def test_parse_malformed_json_keeps_prose():
    prose, m = _parse_synthesis_response("Good prose.\n```json\n[{broken\n```")
    assert prose == "Good prose."
    assert m == []


def test_parse_non_list_json_yields_empty():
    _, m = _parse_synthesis_response('Prose.\n```json\n{"milestone": "A"}\n```')
    assert m == []


def test_parse_validation_drops_bad_entries_and_caps():
    items = (
        [{'milestone': ''}, {'no_milestone': 1}, 'string',
         {'milestone': 'ok', 'date': 20260601, 'source_link': 'ftp://bad'},
         {'milestone': 'no link', 'source_link': 'javascript:alert(1)'}]
        + [{'milestone': f'm{i}'} for i in range(20)]
    )
    import json
    text = "Prose.\n```json\n" + json.dumps(items) + "\n```"
    _, m = _parse_synthesis_response(text)
    assert len(m) == 12
    assert m[0] == {"milestone": "ok", "date": "20260601"}
    assert m[1] == {"milestone": "no link", "date": None}


def test_parse_no_json_at_all():
    prose, m = _parse_synthesis_response("Just prose, no milestones.")
    assert prose == "Just prose, no milestones."
    assert m == []


# ---------------------------------------------------------------------------
# _synthesize_theme_month (patched gateway)
# ---------------------------------------------------------------------------

def _gateway_result(text, method="llm", success=True):
    result = MagicMock()
    result.is_success.return_value = success
    result.result = text
    result.provenance = SimpleNamespace(method=method, provider="qwen", model="qwen3.8-flash")
    return result


def _run_sync(coro):
    return asyncio.run(coro)


def test_synthesize_success_shapes_dict_and_request():
    gateway = MagicMock()
    gateway.execute = AsyncMock(return_value=_gateway_result(
        'The arc of the month.\n```json\n[{"milestone": "X launched", "date": "2026-06-03"}]\n```'
    ))
    with patch("core.monthly_synthesis.get_gateway", return_value=gateway):
        out = _run_sync(_synthesize_theme_month("Theme A", "2026-06", [_brief("2026-06-03")], _SETTINGS))
    assert out["macro_summary"] == "The arc of the month."
    assert out["key_milestones"][0]["milestone"] == "X launched"
    assert out["source_run_count"] == 1
    assert out["generation_source"] == "qwen:qwen3.8-flash"
    request = gateway.execute.call_args.args[0]
    assert request.task == TaskType.SYNTHESISE
    assert request.allow_deterministic_fallback is False


def test_synthesize_failure_returns_none():
    gateway = MagicMock()
    gateway.execute = AsyncMock(return_value=_gateway_result("err", success=False))
    with patch("core.monthly_synthesis.get_gateway", return_value=gateway):
        assert _run_sync(_synthesize_theme_month("Theme A", "2026-06", [_brief("2026-06-03")], _SETTINGS)) is None


def test_synthesize_deterministic_provenance_returns_none():
    gateway = MagicMock()
    gateway.execute = AsyncMock(return_value=_gateway_result("extractive text", method="deterministic"))
    with patch("core.monthly_synthesis.get_gateway", return_value=gateway):
        assert _run_sync(_synthesize_theme_month("Theme A", "2026-06", [_brief("2026-06-03")], _SETTINGS)) is None


def test_synthesize_empty_prose_returns_none():
    gateway = MagicMock()
    gateway.execute = AsyncMock(return_value=_gateway_result('```json\n[{"milestone": "A"}]\n```'))
    with patch("core.monthly_synthesis.get_gateway", return_value=gateway):
        assert _run_sync(_synthesize_theme_month("Theme A", "2026-06", [_brief("2026-06-03")], _SETTINGS)) is None


def test_synthesize_no_usable_briefs_skips_gateway():
    gateway = MagicMock()
    gateway.execute = AsyncMock()
    with patch("core.monthly_synthesis.get_gateway", return_value=gateway):
        out = _run_sync(_synthesize_theme_month(
            "Theme A", "2026-06", [_brief("2026-06-03", generation_source="gateway:skipped")], _SETTINGS,
        ))
    assert out is None
    gateway.execute.assert_not_called()


def test_synthesize_gateway_exception_returns_none():
    gateway = MagicMock()
    gateway.execute = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("core.monthly_synthesis.get_gateway", return_value=gateway):
        assert _run_sync(_synthesize_theme_month("Theme A", "2026-06", [_brief("2026-06-03")], _SETTINGS)) is None


# ---------------------------------------------------------------------------
# synthesize_due_monthly (shared runner)
# ---------------------------------------------------------------------------

def _manager_mock(existing=None, months=None, briefs=None):
    manager = MagicMock()
    manager.is_available.return_value = True
    manager.get_monthly_syntheses.return_value = existing if existing is not None else []
    manager.get_run_months.return_value = months if months is not None else ["2026-06"]
    manager.get_theme_briefs_for_month.return_value = briefs if briefs is not None else [
        _brief("2026-06-03", theme=t) for t in ("Theme A", "Theme B")
    ]
    manager.upsert_monthly_synthesis.return_value = True
    return manager


def test_runner_offline_returns_zero():
    manager = MagicMock()
    manager.is_available.return_value = False
    with patch("core.supabase_client.get_supabase_manager", return_value=manager):
        assert synthesize_due_monthly() == 0
    manager.get_monthly_syntheses.assert_not_called()


def test_runner_disabled_kill_switch():
    manager = _manager_mock()
    settings = dict(_SETTINGS, monthly_synthesis_enabled=False)
    with patch("core.supabase_client.get_supabase_manager", return_value=manager), \
         patch("config.settings.get_monthly_synthesis_settings", return_value=settings):
        assert synthesize_due_monthly() == 0
    manager.get_run_months.assert_not_called()


def test_runner_dry_run_no_llm_no_writes():
    from config.themes import THEME_ORDER
    manager = _manager_mock()
    with patch("core.supabase_client.get_supabase_manager", return_value=manager), \
         patch("config.settings.get_monthly_synthesis_settings", return_value=_SETTINGS), \
         patch("core.monthly_synthesis._synthesize_theme_month", new_callable=AsyncMock) as synth:
        messages = []
        count = synthesize_due_monthly(dry_run=True, progress=messages.append)
    assert count == len(THEME_ORDER)  # one month x 7 themes, none existing
    synth.assert_not_called()
    manager.upsert_monthly_synthesis.assert_not_called()
    assert all(m.startswith("[ROLLUP] due:") for m in messages)


def test_runner_per_theme_exception_isolated():
    manager = _manager_mock()
    good = {"macro_summary": "p", "key_milestones": [], "source_run_count": 1, "generation_source": "qwen:m"}

    async def _side_effect(theme, month, briefs, settings):
        if theme == "Theme A":
            raise RuntimeError("one theme fails")
        return good

    with patch("core.supabase_client.get_supabase_manager", return_value=manager), \
         patch("config.settings.get_monthly_synthesis_settings", return_value=_SETTINGS), \
         patch("core.monthly_synthesis.THEME_ORDER", ["Theme A", "Theme B"]), \
         patch("core.monthly_synthesis._synthesize_theme_month", side_effect=_side_effect):
        count = synthesize_due_monthly()
    assert count == 1
    assert manager.upsert_monthly_synthesis.call_count == 1
    assert manager.upsert_monthly_synthesis.call_args.args[1] == "Theme B"


def test_runner_explicit_limit_caps_calls():
    manager = _manager_mock()
    with patch("core.supabase_client.get_supabase_manager", return_value=manager), \
         patch("config.settings.get_monthly_synthesis_settings", return_value=_SETTINGS), \
         patch("core.monthly_synthesis.THEME_ORDER", ["Theme A", "Theme B"]), \
         patch("core.monthly_synthesis._synthesize_theme_month", new_callable=AsyncMock) as synth:
        synth.return_value = {"macro_summary": "p", "key_milestones": [], "source_run_count": 1, "generation_source": "qwen:m"}
        count = synthesize_due_monthly(limit=1)
    assert count == 1
    assert synth.call_count == 1


def test_runner_default_limit_uses_settings_cap():
    manager = _manager_mock(months=["2026-05", "2026-06"])
    settings = dict(_SETTINGS, monthly_synthesis_max_per_run=1)
    with patch("core.supabase_client.get_supabase_manager", return_value=manager), \
         patch("config.settings.get_monthly_synthesis_settings", return_value=settings), \
         patch("core.monthly_synthesis.THEME_ORDER", ["Theme A", "Theme B"]), \
         patch("core.monthly_synthesis._synthesize_theme_month", new_callable=AsyncMock) as synth:
        synth.return_value = {"macro_summary": "p", "key_milestones": [], "source_run_count": 1, "generation_source": "qwen:m"}
        count = synthesize_due_monthly()
    assert count == 1
    # Oldest month drains first.
    assert manager.upsert_monthly_synthesis.call_args.args[0] == "2026-05"


def test_runner_month_filter():
    manager = _manager_mock(months=["2026-06", "2026-07"])
    with patch("core.supabase_client.get_supabase_manager", return_value=manager), \
         patch("config.settings.get_monthly_synthesis_settings", return_value=_SETTINGS), \
         patch("core.monthly_synthesis.THEME_ORDER", ["Theme A"]), \
         patch("core.monthly_synthesis._synthesize_theme_month", new_callable=AsyncMock) as synth:
        synth.return_value = {"macro_summary": "p", "key_milestones": [], "source_run_count": 1, "generation_source": "qwen:m"}
        count = synthesize_due_monthly(months=["2026-07"])
    assert count == 1
    assert manager.upsert_monthly_synthesis.call_args.args[0] == "2026-07"
    manager.get_theme_briefs_for_month.assert_called_once_with("2026-07")


def test_runner_failed_synthesis_writes_no_row():
    manager = _manager_mock()
    with patch("core.supabase_client.get_supabase_manager", return_value=manager), \
         patch("config.settings.get_monthly_synthesis_settings", return_value=_SETTINGS), \
         patch("core.monthly_synthesis.THEME_ORDER", ["Theme A"]), \
         patch("core.monthly_synthesis._synthesize_theme_month", new_callable=AsyncMock, return_value=None):
        count = synthesize_due_monthly()
    assert count == 0
    manager.upsert_monthly_synthesis.assert_not_called()


def test_runner_never_raises_on_manager_error():
    manager = MagicMock()
    manager.is_available.return_value = True
    manager.get_monthly_syntheses.side_effect = RuntimeError("db down")
    with patch("core.supabase_client.get_supabase_manager", return_value=manager), \
         patch("config.settings.get_monthly_synthesis_settings", return_value=_SETTINGS):
        assert synthesize_due_monthly() == 0


# ---------------------------------------------------------------------------
# Settings merge
# ---------------------------------------------------------------------------

def test_monthly_synthesis_settings_merge_key_filtered():
    from config.settings import get_monthly_synthesis_settings
    with patch("config.settings.load_custom_settings",
               return_value={"monthly_refresh_days": 7, "unrelated_key": 123}):
        settings = get_monthly_synthesis_settings()
    assert settings["monthly_refresh_days"] == 7
    assert settings["monthly_synthesis_enabled"] is True
    assert "unrelated_key" not in settings