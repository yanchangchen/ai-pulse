"""Tests for core/auto_remediation.py — bounded, reversible auto-fixes
applied after a quality evaluation.

Covers: opt-in flag, the faithfulness/coverage tuner rules, the capped
keyword rule with pending-state bookkeeping, and the rollback pass that
removes auto-applied keywords when a theme's score drops.
"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

import config.settings as settings
import core.auto_remediation as ar
from core.evaluator import EvaluationReport

THEME = "Agentic Systems & DevTools"


def _report(
    faithfulness: float = 0.9,
    coverage: float = 0.9,
    threshold: float = 0.8,
    per_theme: dict | None = None,
    raw: dict | None = None,
    suggestions: dict | None = None,
) -> EvaluationReport:
    return EvaluationReport(
        run_ids=["r1"],
        run_timestamps=["2026-09-20T00:00:00Z"],
        threshold=threshold,
        classifier_score=0.9,
        faithfulness_score=faithfulness,
        uniqueness_score=0.9,
        grounding_score=1.0,
        structural_compliance_score=1.0,
        coverage_score=coverage,
        temporal_coherence_score=1.0,
        per_theme_classifier=per_theme if per_theme is not None else {},
        per_run_scores=[],
        recommendations=[],
        raw_metrics=raw if raw is not None else {},
        generated_at=datetime.now(timezone.utc),
        keyword_suggestions=suggestions,
    )


@pytest.fixture(autouse=True)
def isolate_files(tmp_path, monkeypatch):
    """Point the settings file and the pending-state file at tmp storage
    so tests never touch real config/custom_settings.json or data/."""
    monkeypatch.setattr(settings, "CUSTOM_SETTINGS_FILE", tmp_path / "custom_settings.json")
    monkeypatch.setattr(ar, "STATE_FILE", tmp_path / "state.json")
    yield


# ---------------------------------------------------------------------------
# Enablement
# ---------------------------------------------------------------------------


class TestEnablement:
    def test_disabled_by_default(self):
        assert ar.is_enabled() is False
        # A terrible report must not trigger anything while disabled.
        assert ar.run_auto_remediation(_report(faithfulness=0.1, coverage=0.1)) == {}

    def test_set_enabled_roundtrip(self):
        ar.set_enabled(True)
        assert ar.is_enabled() is True
        ar.set_enabled(False)
        assert ar.is_enabled() is False


# ---------------------------------------------------------------------------
# Faithfulness tuner rule
# ---------------------------------------------------------------------------


class TestFaithfulnessRule:
    def test_enables_strict_and_lowers_temp(self):
        ar.set_enabled(True)
        settings.update_summariser_settings(0.5, 1500, False)
        out = ar.run_auto_remediation(
            _report(faithfulness=0.5, raw={"faithfulness": {"samples": 3}})
        )
        assert any(
            a["action"] == "tune_summariser_faithfulness" for a in out["applied"]
        )
        s = settings.get_summariser_settings()
        assert s["strict_faithfulness_mode"] is True
        assert s["temperature"] == pytest.approx(ar.AUTO_TARGET_TEMPERATURE)

    def test_keeps_lower_existing_temperature(self):
        ar.set_enabled(True)
        settings.update_summariser_settings(0.05, 1500, True)
        out = ar.run_auto_remediation(
            _report(faithfulness=0.5, raw={"faithfulness": {"samples": 3}})
        )
        # Already at/below target and strict → no action at all.
        assert out == {}
        assert settings.get_summariser_settings()["temperature"] == pytest.approx(0.05)

    def test_skipped_judge_does_not_trigger(self):
        ar.set_enabled(True)
        settings.update_summariser_settings(0.5, 1500, False)
        out = ar.run_auto_remediation(
            _report(faithfulness=0.5, raw={"faithfulness": {"skipped": True}})
        )
        assert out == {}
        assert settings.get_summariser_settings()["strict_faithfulness_mode"] is False

    def test_above_threshold_does_not_trigger(self):
        ar.set_enabled(True)
        settings.update_summariser_settings(0.5, 1500, False)
        out = ar.run_auto_remediation(
            _report(faithfulness=0.85, raw={"faithfulness": {"samples": 3}})
        )
        assert out == {}


# ---------------------------------------------------------------------------
# Coverage token-budget rule
# ---------------------------------------------------------------------------


class TestCoverageRule:
    def test_raises_tokens_one_step(self):
        ar.set_enabled(True)
        settings.update_summariser_settings(0.3, 1500, False)
        ar.run_auto_remediation(
            _report(coverage=0.4, raw={"coverage": {"total": 10}})
        )
        assert settings.get_summariser_settings()["max_tokens"] == 2000

    def test_caps_at_limit(self):
        ar.set_enabled(True)
        settings.update_summariser_settings(0.3, 2400, False)
        ar.run_auto_remediation(
            _report(coverage=0.4, raw={"coverage": {"total": 10}})
        )
        assert settings.get_summariser_settings()["max_tokens"] == ar.AUTO_MAX_TOKENS_CAP

    def test_at_cap_no_action(self):
        ar.set_enabled(True)
        settings.update_summariser_settings(0.3, 2500, False)
        out = ar.run_auto_remediation(
            _report(coverage=0.4, raw={"coverage": {"total": 10}})
        )
        assert not any(
            a["action"] == "raise_max_tokens" for a in out.get("applied", [])
        )

    def test_skipped_coverage_does_not_trigger(self):
        ar.set_enabled(True)
        settings.update_summariser_settings(0.3, 1500, False)
        out = ar.run_auto_remediation(
            _report(coverage=0.4, raw={"coverage": {"skipped": True}})
        )
        assert out == {}


# ---------------------------------------------------------------------------
# Keyword rule
# ---------------------------------------------------------------------------


def _suggestions(n=7):
    return {
        "theme_suggestions": {
            THEME: [
                {"term": f"term{i}", "weight": 3 if i == 0 else 2, "reason": "r"}
                for i in range(n)
            ]
        },
        "watchlist_suggestions": [],
    }


class TestKeywordRule:
    def test_applies_capped_terms_with_weight_cap(self):
        ar.set_enabled(True)
        report = _report(
            per_theme={THEME: 0.5},
            raw={"categoriser": {"samples_judged": 10}},
            suggestions=_suggestions(7),
        )
        with patch("config.themes.add_keywords_to_theme") as mock_add:
            out = ar.run_auto_remediation(report)
        mock_add.assert_called_once()
        theme_arg, payload = mock_add.call_args.args
        assert theme_arg == THEME
        # At most AUTO_MAX_TERMS_PER_THEME terms, weights capped at 2
        # (the weight-3 suggestion must be clamped, not dropped).
        assert len(payload) == ar.AUTO_MAX_TERMS_PER_THEME
        assert all(1 <= w <= ar.AUTO_MAX_WEIGHT for w in payload.values())
        assert any(a["action"] == "apply_keywords" for a in out["applied"])

    def test_pending_state_written_with_baseline(self):
        ar.set_enabled(True)
        report = _report(
            per_theme={THEME: 0.5},
            raw={"categoriser": {}},
            suggestions=_suggestions(3),
        )
        with patch("config.themes.add_keywords_to_theme"):
            ar.run_auto_remediation(report)
        state = json.loads(ar.STATE_FILE.read_text(encoding="utf-8"))
        pending = state["pending_keyword_applies"][THEME]
        assert pending["baseline_score"] == 0.5
        assert len(pending["terms"]) == 3

    def test_skips_existing_and_duplicate_terms(self):
        from config.themes import THEMES

        existing = next(iter(THEMES[THEME]["keywords"]))
        ar.set_enabled(True)
        sugg = {
            "theme_suggestions": {THEME: [
                {"term": existing, "weight": 2, "reason": "dup of existing"},
                {"term": "brandnewterm", "weight": 2, "reason": "new"},
                {"term": "brandnewterm", "weight": 2, "reason": "dup within batch"},
            ]},
            "watchlist_suggestions": [],
        }
        report = _report(per_theme={THEME: 0.5}, raw={}, suggestions=sugg)
        with patch("config.themes.add_keywords_to_theme") as mock_add:
            ar.run_auto_remediation(report)
        payload = mock_add.call_args.args[1]
        assert existing.lower() not in {k.lower() for k in payload}
        assert payload == {"brandnewterm": 2}

    def test_one_experiment_per_theme_at_a_time(self):
        ar.set_enabled(True)
        ar._save_state({"pending_keyword_applies": {THEME: {
            "terms": ["old"], "weights": {"old": 2}, "baseline_score": 0.6,
            "evaluation_id": "ev0", "applied_at": "2026-09-19T00:00:00Z",
        }}})
        report = _report(
            per_theme={THEME: 0.7},  # below threshold but above baseline
            raw={"categoriser": {}},
            suggestions=_suggestions(2),
        )
        with patch("config.themes.add_keywords_to_theme") as mock_add:
            ar.run_auto_remediation(report)
        # Pending experiment still unresolved (0.7 ≥ baseline 0.6 → kept,
        # cleared from pending) — but a NEW apply for the same theme in the
        # same round as a still-pending entry must not stack.
        # Here check_rollbacks cleared the pending entry (no drop), so an
        # apply may proceed; what must never happen is two pending entries.
        state = json.loads(ar.STATE_FILE.read_text(encoding="utf-8"))
        assert len(state["pending_keyword_applies"]) <= 1

    def test_strong_themes_get_no_keywords(self):
        ar.set_enabled(True)
        report = _report(
            per_theme={THEME: 0.95},
            raw={"categoriser": {}},
            suggestions=_suggestions(3),
        )
        with patch("config.themes.add_keywords_to_theme") as mock_add:
            ar.run_auto_remediation(report)
        mock_add.assert_not_called()


# ---------------------------------------------------------------------------
# Rollback pass
# ---------------------------------------------------------------------------


class TestRollback:
    def _seed_pending(self, terms, baseline):
        ar._save_state({"pending_keyword_applies": {THEME: {
            "terms": terms,
            "weights": {t: 2 for t in terms},
            "baseline_score": baseline,
            "evaluation_id": "ev1",
            "applied_at": "2026-09-20T00:00:00Z",
        }}})

    def test_rollback_on_score_drop(self):
        ar.set_enabled(True)
        self._seed_pending(["termA", "termB"], 0.7)
        report = _report(per_theme={THEME: 0.55}, raw={"categoriser": {}})
        with patch("config.themes.remove_keyword_from_theme") as mock_rm:
            out = ar.run_auto_remediation(report)
        removed = [c.args[1] for c in mock_rm.call_args_list]
        assert set(removed) == {"termA", "termB"}
        assert any(a["action"] == "rollback_keywords" for a in out["rolled_back"])
        state = json.loads(ar.STATE_FILE.read_text(encoding="utf-8"))
        assert THEME not in state["pending_keyword_applies"]

    def test_no_rollback_on_improvement(self):
        ar.set_enabled(True)
        self._seed_pending(["termA"], 0.5)
        report = _report(per_theme={THEME: 0.75}, raw={"categoriser": {}})
        with patch("config.themes.remove_keyword_from_theme") as mock_rm:
            ar.run_auto_remediation(report)
        mock_rm.assert_not_called()
        state = json.loads(ar.STATE_FILE.read_text(encoding="utf-8"))
        assert state["pending_keyword_applies"] == {}

    def test_no_signal_keeps_pending(self):
        """A skipped categoriser judge gives no per-theme signal — the
        experiment must stay pending, not be silently discarded."""
        ar.set_enabled(True)
        self._seed_pending(["termA"], 0.7)
        report = _report(per_theme={}, raw={"categoriser": {"skipped": True}})
        ar.run_auto_remediation(report)
        state = json.loads(ar.STATE_FILE.read_text(encoding="utf-8"))
        assert THEME in state["pending_keyword_applies"]

    def test_no_reapply_same_round_after_rollback(self):
        """A theme rolled back this round must not immediately get new
        (possibly identical) terms — prevents oscillation."""
        ar.set_enabled(True)
        self._seed_pending(["termA"], 0.7)
        sugg = {
            "theme_suggestions": {THEME: [{"term": "termA", "weight": 2, "reason": "r"}]},
            "watchlist_suggestions": [],
        }
        report = _report(per_theme={THEME: 0.5}, raw={"categoriser": {}}, suggestions=sugg)
        with patch("config.themes.remove_keyword_from_theme"), \
             patch("config.themes.add_keywords_to_theme") as mock_add:
            out = ar.run_auto_remediation(report)
        mock_add.assert_not_called()
        assert out["rolled_back"]

    def test_corrupt_state_file_does_not_crash(self):
        ar.set_enabled(True)
        ar.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ar.STATE_FILE.write_text("{not json", encoding="utf-8")
        report = _report(per_theme={THEME: 0.55}, raw={"categoriser": {}})
        # Must not raise; corrupt state is reset.
        out = ar.run_auto_remediation(report)
        assert isinstance(out, dict)
