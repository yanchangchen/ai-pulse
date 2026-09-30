"""Tests for the evaluation pause/resume + checkpoint system.

Covers core/eval_controller.py (checkpoint I/O, EvalControl,
EvaluationRunner) and the control-parameter threading in
core/evaluator.py (pause mid-loop, resume-skip from cache, the DB retry
loop at persist time, and the double-insert guard).

No real LLM and no real Supabase: LLMs are MagicMocks, Supabase managers
are MagicMocks (the conftest autouse fixture already pins the real
singleton offline).
"""

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import core.eval_controller as ec
from core.eval_controller import (
    EvalControl,
    EvaluationPaused,
    EvaluationRunner,
    atomic_write_json,
    clear_checkpoint,
    load_checkpoint,
    new_checkpoint,
    save_checkpoint,
)
from core.llm_client import LLMClientError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_checkpoint(tmp_path, monkeypatch):
    """Point every default-path user at a temp file and reset the runner
    state, so tests never touch the real data/ directory."""
    ckpt_path = tmp_path / "evaluation_checkpoint.json"
    monkeypatch.setattr(ec, "DEFAULT_CHECKPOINT_PATH", ckpt_path)
    # Fast retry/save cadence for tests.
    monkeypatch.setattr(ec, "DB_RETRY_SECONDS", 0.02)
    monkeypatch.setattr(ec, "CHECKPOINT_MIN_SAVE_INTERVAL", 0.0)
    yield ckpt_path
    # Reset the sys-hung runner state in place (the module keeps a
    # reference to the same dict object).
    ec._EVAL_STATE.update({
        "status": "idle", "error": None, "thread": None,
        "control": None, "report": None, "db_row_id": None,
    })


def _control(state=None, path=None, pause_event=None):
    return EvalControl(state or new_checkpoint({}), path, pause_event)


def _article(i, theme="Agentic Systems & DevTools"):
    return {"id": f"a{i}", "title": f"Title {i}", "summary": f"Summary {i}",
            "theme_name": theme}


def _cat_llm(theme="Agentic Systems & DevTools"):
    llm = MagicMock()
    llm.generate.return_value = theme
    return llm


# ---------------------------------------------------------------------------
# Checkpoint I/O
# ---------------------------------------------------------------------------


class TestCheckpointIO:
    def test_round_trip(self, _isolated_checkpoint):
        state = new_checkpoint({"run_ids": ["r1"], "lookback_days": 7,
                                "threshold": 0.8, "judge_selection": "all",
                                "judge_model": None})
        assert save_checkpoint(state, _isolated_checkpoint) is True
        loaded = load_checkpoint(_isolated_checkpoint)
        assert loaded == state

    def test_no_tmp_file_left_behind(self, _isolated_checkpoint):
        save_checkpoint(new_checkpoint({}), _isolated_checkpoint)
        assert not list(_isolated_checkpoint.parent.glob("*.tmp"))

    def test_missing_file_returns_none(self, _isolated_checkpoint):
        assert load_checkpoint(_isolated_checkpoint) is None

    def test_corrupt_file_quarantined(self, _isolated_checkpoint):
        _isolated_checkpoint.parent.mkdir(parents=True, exist_ok=True)
        _isolated_checkpoint.write_text("{not valid json", encoding="utf-8")
        assert load_checkpoint(_isolated_checkpoint) is None
        assert _isolated_checkpoint.with_suffix(".json.corrupt").exists()

    def test_unknown_version_returns_none(self, _isolated_checkpoint):
        atomic_write_json(_isolated_checkpoint, {"version": 999})
        assert load_checkpoint(_isolated_checkpoint) is None

    def test_clear_checkpoint(self, _isolated_checkpoint):
        save_checkpoint(new_checkpoint({}), _isolated_checkpoint)
        clear_checkpoint(_isolated_checkpoint)
        assert not _isolated_checkpoint.exists()


# ---------------------------------------------------------------------------
# EvalControl thread safety
# ---------------------------------------------------------------------------


class TestEvalControl:
    def test_concurrent_records_all_saved(self, _isolated_checkpoint):
        control = _control(path=_isolated_checkpoint)

        def worker(n):
            for i in range(25):
                control.record("categoriser", f"r{n}", f"item{i}", {"correct": True})

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        control.save(force=True)
        state = load_checkpoint(_isolated_checkpoint)
        items = state["item_results"]["categoriser"]
        assert sum(len(v) for v in items.values()) == 100

    def test_seed_pair_cache_flattens_uniqueness(self):
        state = new_checkpoint({})
        state["item_results"]["uniqueness"] = {
            "r1": {"within|r1|A|B": {"overlap": 0.3}, "cross|r1|A": {"overlap": 0.2}},
        }
        control = _control(state=state)
        assert control.seed_pair_cache() == {"within|r1|A|B": 0.3, "cross|r1|A": 0.2}


# ---------------------------------------------------------------------------
# Judge pause / resume-skip
# ---------------------------------------------------------------------------


class TestCategoriserPauseResume:
    def test_pause_mid_loop_raises_and_checkpoints(self, _isolated_checkpoint):
        from core.evaluator import categoriser_judge

        control = _control(path=_isolated_checkpoint)
        articles = {"r1": [_article(i) for i in range(3)]}
        llm = _cat_llm()

        call_count = {"n": 0}

        def _gen(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                control.request_pause()
            return "Agentic Systems & DevTools"

        llm.generate.side_effect = _gen

        with pytest.raises(EvaluationPaused):
            categoriser_judge(llm, articles, control=control)

        control.save(force=True)
        state = load_checkpoint(_isolated_checkpoint)
        judged = state["item_results"]["categoriser"]["r1"]
        # First item succeeded and is checkpointed; the 2nd (which
        # triggered the pause) completed its LLM call and is also saved;
        # the 3rd never started.
        assert 1 <= len(judged) <= 2
        assert judged["a0"]["correct"] is True

    def test_resume_skips_cached_items(self, _isolated_checkpoint):
        from core.evaluator import categoriser_judge

        articles = {"r1": [_article(i) for i in range(3)]}

        # Full run without control — the reference score.
        llm_full = _cat_llm()
        full_score, _, full_raw = categoriser_judge(llm_full, articles)

        # Pre-seed the checkpoint with the first two items.
        state = new_checkpoint({})
        state["item_results"]["categoriser"]["r1"] = {
            "a0": {"correct": True, "predicted": "Agentic Systems & DevTools"},
            "a1": {"correct": True, "predicted": "Agentic Systems & DevTools"},
        }
        control = _control(state=state, path=_isolated_checkpoint)

        llm = _cat_llm()
        score, _, raw = categoriser_judge(llm, articles, control=control)
        # Only the uncached third item goes to the LLM.
        assert llm.generate.call_count == 1
        assert score == full_score
        assert raw["samples_judged"] == full_raw["samples_judged"]

    def test_failed_items_not_checkpointed(self, _isolated_checkpoint):
        from core.evaluator import categoriser_judge

        control = _control(path=_isolated_checkpoint)
        llm = MagicMock()
        llm.generate.side_effect = LLMClientError("boom")
        categoriser_judge(llm, {"r1": [_article(0)]}, control=control)
        control.save(force=True)
        state = load_checkpoint(_isolated_checkpoint)
        # No run key at all — failures are never checkpointed.
        assert state["item_results"]["categoriser"].get("r1", {}) == {}

    def test_no_control_backward_compat(self):
        from core.evaluator import categoriser_judge

        llm = _cat_llm()
        score, per_theme, raw = categoriser_judge(llm, {"r1": [_article(0)]})
        assert score == 1.0
        assert raw["samples_judged"] == 1


class TestFaithfulnessKeying:
    def _run(self, control=None):
        from core.evaluator import faithfulness_judge

        summaries = {
            "r1": {"A": {"what_is_happening": "text one", "engineering_tradeoffs": "", "product_impact": ""}},
            "r2": {"A": {"what_is_happening": "text two", "engineering_tradeoffs": "", "product_impact": ""}},
        }
        articles = {"r1": [], "r2": []}
        llm = MagicMock()
        llm.generate.return_value = '{"score": 0.9}'
        return faithfulness_judge(llm, summaries, articles, control=control), llm

    def test_checkpoint_keyed_by_run_id(self, _isolated_checkpoint):
        control = _control(path=_isolated_checkpoint)
        (score, raw), llm = self._run(control=control)
        assert llm.generate.call_count == 2  # one per run
        control.save(force=True)
        state = load_checkpoint(_isolated_checkpoint)
        faith = state["item_results"]["faithfulness"]
        # Same item_id "A|what_is_happening" in both runs, keyed separately.
        assert faith["r1"]["A|what_is_happening"]["score"] == 0.9
        assert faith["r2"]["A|what_is_happening"]["score"] == 0.9

    def test_resume_replays_cached(self, _isolated_checkpoint):
        state = new_checkpoint({})
        state["item_results"]["faithfulness"]["r1"] = {
            "A|what_is_happening": {"score": 0.9},
        }
        control = _control(state=state, path=_isolated_checkpoint)
        (score, raw), llm = self._run(control=control)
        # Only r2's item goes to the LLM; r1 replays from the checkpoint.
        assert llm.generate.call_count == 1


class TestUniquenessSeeding:
    def test_cached_pair_skips_llm(self, _isolated_checkpoint):
        from core.evaluator import uniqueness_judge

        text_a = "new agentic framework supports multi-step tool use and planning"
        text_b = "agent orchestration tools integrate with planning and memory"
        summaries = {"r1": {
            "A": {"what_is_happening": text_a, "why_it_matters": ""},
            "B": {"what_is_happening": text_b, "why_it_matters": ""},
        }}
        state = new_checkpoint({})
        state["item_results"]["uniqueness"]["r1"] = {
            "within|r1|A|B": {"overlap": 0.4},
        }
        control = _control(state=state, path=_isolated_checkpoint)

        llm = MagicMock()
        llm.generate.return_value = '{"overlap": 0.5}'
        uniqueness_judge(llm, summaries, control=control)
        # The seeded pair is a cache hit — no LLM call at all.
        assert llm.generate.call_count == 0

    def test_new_pairs_still_call_llm_and_record(self, _isolated_checkpoint):
        from core.evaluator import uniqueness_judge

        text_a = "new agentic framework supports multi-step tool use and planning"
        text_b = "agent orchestration tools integrate with planning and memory"
        summaries = {"r1": {
            "A": {"what_is_happening": text_a, "why_it_matters": ""},
            "B": {"what_is_happening": text_b, "why_it_matters": ""},
        }}
        control = _control(path=_isolated_checkpoint)
        llm = MagicMock()
        llm.generate.return_value = '{"overlap": 0.5}'
        uniqueness_judge(llm, summaries, control=control)
        assert llm.generate.call_count == 1
        control.save(force=True)
        state = load_checkpoint(_isolated_checkpoint)
        assert state["item_results"]["uniqueness"]["r1"]["within|r1|A|B"]["overlap"] == 0.5


# ---------------------------------------------------------------------------
# Persist phase: DB retry, pause, double-insert guard
# ---------------------------------------------------------------------------


def _execute_args():
    """Minimal arguments for _execute_judges_and_build_report in
    deterministic-only mode (no LLM, no gateway needed)."""
    runs = [{"id": "r1", "run_timestamp": "2026-09-01T00:00:00Z", "run_date": "2026-09-01", "total_articles": 1}]
    return dict(
        runs=runs,
        articles_by_run={"r1": [_article(0)]},
        summaries_by_run={"r1": {}},
        prior_summaries_by_run={},
        threshold=0.8,
        lookback_days=7,
        supabase=MagicMock(),
        judge_selection="deterministic",
    )


class TestPersistRetry:
    def test_retry_then_success(self, _isolated_checkpoint):
        from core.evaluator import _execute_judges_and_build_report

        control = _control(path=_isolated_checkpoint)
        with patch("core.evaluator.insert_quality_evaluation",
                   side_effect=[None, None, {"id": "row-1"}]) as insert:
            report = _execute_judges_and_build_report(**_execute_args(), control=control)
        assert insert.call_count == 3
        assert report.db_row_id == "row-1"
        # Completed + file removed.
        assert not _isolated_checkpoint.exists()
        assert control.state["status"] == "completed"
        assert control.state["db_row_id"] == "row-1"

    def test_pause_during_awaiting_db(self, _isolated_checkpoint):
        from core.evaluator import _execute_judges_and_build_report

        control = _control(path=_isolated_checkpoint)
        control.set_status("running")  # the runner normally does this first
        calls = {"n": 0}

        def _insert(supabase, payload, on_error=None):
            calls["n"] += 1
            # First attempt fails (DB "down"); the pause request lands
            # while the retry loop is waiting for the database.
            if calls["n"] >= 2:
                control.request_pause()
            return None

        with patch("core.evaluator.insert_quality_evaluation", side_effect=_insert):
            with pytest.raises(EvaluationPaused):
                _execute_judges_and_build_report(**_execute_args(), control=control)

        # The full payload sat in the checkpoint before the insert attempts.
        state = load_checkpoint(_isolated_checkpoint)
        assert state["status"] == "paused"
        assert state["phase"] == "persist"
        assert state["final_payload"]["runs_evaluated"] == ["r1"]
        assert state["final_report"]["run_ids"] == ["r1"]

    def test_resumed_persist_inserts_once(self, _isolated_checkpoint):
        from core.evaluator import persist_final_payload

        control = _control(path=_isolated_checkpoint)
        control.set_final(
            {"runs_evaluated": ["r1"], "threshold": 0.8},
            {"run_ids": ["r1"]},
            [{"kind": "watchlist_term", "term": "mcp"}],
        )
        with patch("core.evaluator.insert_quality_evaluation", return_value={"id": "row-9"}) as insert, \
             patch("core.quality_schema.insert_keyword_suggestions") as kw_insert:
            report_dict, db_row_id = persist_final_payload(MagicMock(), control)
        assert db_row_id == "row-9"
        assert insert.call_count == 1
        assert kw_insert.call_count == 1
        # Inserted rows pass the evaluation_id.
        assert kw_insert.call_args.kwargs.get("evaluation_id") == "row-9"
        assert report_dict["run_ids"] == ["r1"]
        assert not _isolated_checkpoint.exists()

    def test_no_control_legacy_path_no_retry(self):
        from core.evaluator import _execute_judges_and_build_report

        with patch("core.evaluator.insert_quality_evaluation", return_value=None) as insert:
            report = _execute_judges_and_build_report(**_execute_args())
        assert insert.call_count == 1  # no retry loop without a control
        assert report.db_row_id is None


class TestInsertErrorSurfacing:
    """Fix A: insert failures must carry their actual error message into
    the awaiting-db state (a schema mismatch previously looked identical
    to a network blip and retried forever, silently)."""

    def test_on_error_callback_receives_message(self):
        from core.quality_schema import insert_quality_evaluation

        sb = MagicMock()
        sb.is_available.return_value = True
        sb.client.table.side_effect = RuntimeError("PGRST204 column missing")
        captured = []
        result = insert_quality_evaluation(sb, {}, on_error=captured.append)
        assert result is None
        assert captured == ["PGRST204 column missing"]

    def test_on_error_callback_errors_are_swallowed(self):
        from core.quality_schema import insert_quality_evaluation

        sb = MagicMock()
        sb.is_available.return_value = True
        sb.client.table.side_effect = RuntimeError("boom")

        def bad_cb(msg):
            raise ValueError("callback itself broke")

        # Must not raise.
        assert insert_quality_evaluation(sb, {}, on_error=bad_cb) is None

    def test_db_error_recorded_then_cleared(self, _isolated_checkpoint):
        from core.evaluator import _execute_judges_and_build_report

        control = _control(path=_isolated_checkpoint)
        control.set_status("running")
        calls = {"n": 0}

        def _insert(supabase, payload, on_error=None):
            calls["n"] += 1
            if calls["n"] == 1:
                if on_error:
                    on_error("PGRST204: no such column")
                return None
            return {"id": "row-1"}

        with patch("core.evaluator.insert_quality_evaluation", side_effect=_insert):
            report = _execute_judges_and_build_report(**_execute_args(), control=control)
        assert report.db_row_id == "row-1"
        # Cleared again once the insert succeeds.
        assert control.state.get("db_error") is None

    def test_db_error_visible_while_awaiting_db(self, _isolated_checkpoint):
        from core.evaluator import _execute_judges_and_build_report

        control = _control(path=_isolated_checkpoint)
        control.set_status("running")
        calls = {"n": 0}

        def _insert(supabase, payload, on_error=None):
            calls["n"] += 1
            if calls["n"] >= 2:
                control.request_pause()
            if on_error:
                on_error("schema cache miss")
            return None

        with patch("core.evaluator.insert_quality_evaluation", side_effect=_insert):
            with pytest.raises(EvaluationPaused):
                _execute_judges_and_build_report(**_execute_args(), control=control)

        state = load_checkpoint(_isolated_checkpoint)
        assert state["status"] == "paused"
        assert state["db_error"] == "schema cache miss"


class TestKeywordSuggestionStatus:
    """Fix C: pending suggestions get applied/dismissed via status updates."""

    def _sb(self, updated=None):
        sb = MagicMock()
        sb.is_available.return_value = True
        sb.client.table.return_value.update.return_value.in_.return_value \
            .execute.return_value = MagicMock(data=updated or [{"id": "a"}, {"id": "b"}])
        return sb

    def test_update_returns_row_count(self):
        from core.quality_schema import update_keyword_suggestion_status

        sb = self._sb(updated=[{"id": "a"}, {"id": "b"}])
        assert update_keyword_suggestion_status(sb, ["a", "b"], "applied") == 2
        # The status payload and id filter are passed through.
        sb.client.table.assert_called_once_with("keyword_suggestions")
        sb.client.table.return_value.update.assert_called_once_with({"status": "applied"})
        sb.client.table.return_value.update.return_value.in_.assert_called_once_with("id", ["a", "b"])

    def test_update_unavailable_or_empty(self):
        from core.quality_schema import update_keyword_suggestion_status

        assert update_keyword_suggestion_status(None, ["a"], "dismissed") == 0
        sb = self._sb()
        assert update_keyword_suggestion_status(sb, [], "dismissed") == 0
        sb.is_available.return_value = False
        assert update_keyword_suggestion_status(sb, ["a"], "dismissed") == 0

    def test_update_never_raises(self):
        from core.quality_schema import update_keyword_suggestion_status

        sb = MagicMock()
        sb.is_available.return_value = True
        sb.client.table.side_effect = RuntimeError("rls denied")
        assert update_keyword_suggestion_status(sb, ["a"], "applied") == 0


class TestDoubleInsertGuard:
    def test_completed_checkpoint_refuses_resume(self, _isolated_checkpoint):
        state = new_checkpoint({})
        state["status"] = "completed"
        state["db_row_id"] = "row-1"
        save_checkpoint(state, _isolated_checkpoint)
        with patch("core.evaluator.run_weekly_evaluation") as fake_run:
            assert EvaluationRunner.resume() is False
            fake_run.assert_not_called()


# ---------------------------------------------------------------------------
# Runner lifecycle
# ---------------------------------------------------------------------------


class TestRunnerLifecycle:
    def test_start_refuses_second_run(self):
        release = threading.Event()

        def fake_eval(**kwargs):
            release.wait(timeout=5)
            return MagicMock()

        with patch("core.evaluator.run_weekly_evaluation", side_effect=fake_eval):
            assert EvaluationRunner.start({}) is True
            assert EvaluationRunner.is_running()
            assert EvaluationRunner.start({}) is False
            release.set()
            # Let the worker finish.
            deadline = time.monotonic() + 5
            while EvaluationRunner.is_running() and time.monotonic() < deadline:
                time.sleep(0.02)

    def test_pause_then_worker_exits_paused(self, _isolated_checkpoint):
        def fake_eval(**kwargs):
            control = kwargs.get("control")
            for _ in range(500):
                if control is not None and control.pause_requested:
                    raise EvaluationPaused("test")
                time.sleep(0.01)
            return MagicMock()

        with patch("core.evaluator.run_weekly_evaluation", side_effect=fake_eval):
            assert EvaluationRunner.start({}) is True
            EvaluationRunner.request_pause()
            deadline = time.monotonic() + 5
            while EvaluationRunner.is_running() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not EvaluationRunner.is_running()
            assert EvaluationRunner.get_status()["status"] == "paused"
            state = load_checkpoint(_isolated_checkpoint)
            assert state["status"] == "paused"

    def test_discard_deletes_checkpoint(self, _isolated_checkpoint):
        save_checkpoint(new_checkpoint({}), _isolated_checkpoint)
        assert EvaluationRunner.discard() is True
        assert not _isolated_checkpoint.exists()

    def test_resume_uses_checkpoint_config(self, _isolated_checkpoint):
        state = new_checkpoint({"run_ids": ["r1", "r2"], "lookback_days": 14,
                                "threshold": 0.7, "judge_selection": "deterministic"})
        state["item_results"]["categoriser"]["r1"] = {"a0": {"correct": True}}
        save_checkpoint(state, _isolated_checkpoint)

        captured = {}

        def fake_for_runs(run_ids, **kwargs):
            captured["run_ids"] = run_ids
            captured.update(kwargs)
            return MagicMock()

        with patch("core.evaluator.run_evaluation_for_runs", side_effect=fake_for_runs):
            assert EvaluationRunner.resume() is True
            deadline = time.monotonic() + 5
            while EvaluationRunner.is_running() and time.monotonic() < deadline:
                time.sleep(0.02)
        assert captured["run_ids"] == ["r1", "r2"]
        assert captured["lookback_days"] == 14


# ---------------------------------------------------------------------------
# Entry points and loaders
# ---------------------------------------------------------------------------


class TestEntryPoints:
    def test_run_ids_pinned_before_judging(self, _isolated_checkpoint):
        from core.evaluator import run_weekly_evaluation

        control = _control(path=_isolated_checkpoint)
        runs = [{"id": "r1", "run_timestamp": "2026-09-01T00:00:00Z",
                 "run_date": "2026-09-01", "total_articles": 1}]
        with patch("core.evaluator._load_recent_runs", return_value=runs), \
             patch("core.evaluator._load_articles_for_run", return_value=[]), \
             patch("core.evaluator._load_summaries_for_run", return_value={}), \
             patch("core.evaluator.insert_quality_evaluation", return_value={"id": "row-1"}):
            run_weekly_evaluation(
                supabase=MagicMock(), lookback_days=7,
                judge_selection="deterministic", control=control,
            )
        assert control.state["config"]["run_ids"] == ["r1"]

    def test_articles_sorted_for_deterministic_sample(self):
        from core.evaluator import _load_articles_for_run

        sb = MagicMock()
        sb.client.table.return_value.select.return_value.eq.return_value \
            .execute.return_value = MagicMock(data=[
                {"id": "b", "theme_name": "A", "title": "t", "summary": "s"},
                {"id": "a", "theme_name": "A", "title": "t", "summary": "s"},
                {"id": "c", "theme_name": "A", "title": "t", "summary": "s"},
            ])
        arts = _load_articles_for_run(sb, "r1")
        assert [a["id"] for a in arts] == ["a", "b", "c"]


class TestReportRoundTrip:
    def test_from_dict_reverses_to_dict(self):
        from core.evaluator import EvaluationReport

        report = EvaluationReport(
            run_ids=["r1"], run_timestamps=["2026-09-01T00:00:00Z"],
            threshold=0.8, classifier_score=0.9, faithfulness_score=0.8,
            uniqueness_score=0.7, grounding_score=1.0,
            structural_compliance_score=0.9, coverage_score=0.8,
            temporal_coherence_score=0.7, per_theme_classifier={"A": 0.9},
            per_run_scores=[], recommendations=["r"], raw_metrics={"k": 1},
            generated_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            db_row_id="row-1", keyword_suggestions={"theme_suggestions": {}},
        )
        d = report.to_dict()
        # to_dict must be JSON-serialisable (it goes into the checkpoint).
        json.dumps(d)
        rebuilt = EvaluationReport.from_dict(d)
        assert rebuilt == report


# ---------------------------------------------------------------------------
# Weekly evaluator stays off (manual triggering only)
# ---------------------------------------------------------------------------


def test_weekly_evaluator_remains_noop():
    from core.weekly_evaluator import maybe_start_weekly_evaluator

    maybe_start_weekly_evaluator()
    assert EvaluationRunner.is_running() is False
