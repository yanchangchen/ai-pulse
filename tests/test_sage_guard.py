"""Tests for core/sage_guard.py (Phase 8.2 grounding guard).

Files isolated to tmp_path (the test_auto_remediation pattern); Supabase
is a MagicMock manager, never the production project.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from core import sage_guard as guard


@pytest.fixture(autouse=True)
def isolate_files(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "STATE_FILE", tmp_path / "guard_state.json")
    monkeypatch.setattr("config.settings.CUSTOM_SETTINGS_FILE", tmp_path / "custom_settings.json")


def _manager(scores, mode=None):
    m = MagicMock()
    m.is_available.return_value = True
    rows = [
        {"score": s, "created_at": f"2026-11-{i + 1:02d}T00:00:00+00:00",
         "answer_mode": mode or "fast"}
        for i, s in enumerate(scores)
    ]
    m.get_sage_evaluations.return_value = rows
    m.get_app_setting.return_value = None
    m.upsert_app_setting.return_value = True
    return m


def _enable():
    guard.set_enabled(True)


# ---------------------------------------------------------------------------
# Rolling metric (shared definition)
# ---------------------------------------------------------------------------

def test_rolling_mean_window_and_order():
    m = _manager([0.5] * 15 + [1.0] * 10)  # 25 rows; last 20 = 10x0.5 + 10x1.0
    mean, n = guard.rolling_sage_faithfulness(m)
    assert n == 20
    assert abs(mean - 0.75) < 1e-9


def test_rolling_mode_filter():
    m = _manager([0.5] * 12)
    for r in m.get_sage_evaluations.return_value:
        r["answer_mode"] = "deep"
    mean, n = guard.rolling_sage_faithfulness(m, mode="fast")
    assert (mean, n) == (0.0, 0)
    mean, n = guard.rolling_sage_faithfulness(m, mode="deep")
    assert n == 12 and mean == 0.5


def test_rolling_empty_and_unavailable():
    m = _manager([])
    assert guard.rolling_sage_faithfulness(m) == (0.0, 0)
    m.get_sage_evaluations.side_effect = RuntimeError("down")
    assert guard.rolling_sage_faithfulness(m) == (0.0, 0)


# ---------------------------------------------------------------------------
# Enablement + trigger / revert
# ---------------------------------------------------------------------------

def test_disabled_by_default_no_action():
    m = _manager([0.1] * 15)
    assert guard.check_and_act(m) == {}
    from config.settings import load_custom_settings
    assert not load_custom_settings().get(guard.STRICT_FLAG)


def test_trigger_tightens_and_audits():
    _enable()
    m = _manager([0.5] * 12)
    with patch("core.supabase_client.get_supabase_manager", return_value=m):
        action = guard.check_and_act(m)
    assert action["action"] == "tighten_grounding"
    assert action["rolling_mean"] == 0.5
    from config.settings import load_custom_settings
    assert load_custom_settings()[guard.STRICT_FLAG] is True
    # State write-through: local mirror + app_settings.
    state = json.loads(guard.STATE_FILE.read_text(encoding="utf-8"))
    assert state["strict"] is True
    assert state["history"][-1]["action"] == "tighten_grounding"
    m.upsert_app_setting.assert_called()
    assert m.upsert_app_setting.call_args.args[0] == guard.STATE_KEY


def test_min_n_not_met_no_action():
    _enable()
    m = _manager([0.1] * 5)
    assert guard.check_and_act(m) == {}


def test_hysteresis_between_trigger_and_recover_holds():
    _enable()
    guard.check_and_act(_manager([0.5] * 12))  # tighten
    # 0.82 is above trigger (0.8) but below recover (0.85): stay strict.
    assert guard.check_and_act(_manager([0.82] * 12)) == {}
    from config.settings import load_custom_settings
    assert load_custom_settings()[guard.STRICT_FLAG] is True


def test_recovery_reverts_automatically():
    _enable()
    guard.check_and_act(_manager([0.5] * 12))
    action = guard.check_and_act(_manager([0.9] * 12))
    assert action["action"] == "revert_grounding"
    from config.settings import load_custom_settings
    assert load_custom_settings()[guard.STRICT_FLAG] is False
    state = json.loads(guard.STATE_FILE.read_text(encoding="utf-8"))
    assert state["strict"] is False
    assert state["history"][-1]["action"] == "revert_grounding"


def test_above_threshold_never_triggers():
    _enable()
    assert guard.check_and_act(_manager([0.95] * 12)) == {}


def test_never_raises_on_manager_failure():
    _enable()
    m = MagicMock()
    m.is_available.return_value = True
    m.get_sage_evaluations.side_effect = RuntimeError("db down")
    assert guard.check_and_act(m) == {}


def test_history_capped():
    _enable()
    m_low = _manager([0.5] * 12)
    m_high = _manager([0.9] * 12)
    for _ in range(guard.HISTORY_CAP + 5):
        guard.check_and_act(m_low)
        guard.check_and_act(m_high)
    state = json.loads(guard.STATE_FILE.read_text(encoding="utf-8"))
    assert len(state["history"]) == guard.HISTORY_CAP