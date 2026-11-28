"""Tests for core/sage_exemplars.py (Phase 8.1 exemplar memory)."""

from unittest.mock import MagicMock, patch

from core.sage_exemplars import build_exemplar_block

_SETTINGS = {"sage_exemplars_enabled": True, "sage_exemplar_max_chars": 2500}


def _manager(rows=None, feedback=None):
    m = MagicMock()
    m.is_available.return_value = True
    m.get_sage_evaluations.return_value = rows or []
    m.get_sage_reply_feedback.return_value = feedback or []
    return m


def _row(score, question="Q?", answer="A.", claims=None, created="2026-11-20T00:00:00+00:00"):
    return {"score": score, "question": question, "answer_text": answer,
            "unsupported_claims": claims, "created_at": created}


def _build(m, settings=None):
    with patch("config.settings.get_sage_settings", return_value=settings or _SETTINGS):
        return build_exemplar_block(manager=m)


def test_positive_exemplar_from_high_score():
    m = _manager([_row(1.0, "How did X evolve?", "A grounded chronological answer.")])
    block = _build(m)
    assert "GOOD EXAMPLE" in block
    assert "How did X evolve?" in block
    assert "A grounded chronological answer." in block
    assert block.startswith("--- EXEMPLARS")
    assert block.endswith("--- END EXEMPLARS ---")


def test_negative_exemplars_from_claims_and_corrections():
    m = _manager(
        [_row(0.5, claims=["X did Y (unsupported)", "another bad claim", "third claim"])],
        feedback=[{"rating": "down", "correction": "The date was wrong, it was June not May.",
                   "question": "When did X ship?"},
                  {"rating": "up", "correction": "ignored"}],
    )
    block = _build(m)
    assert "AVOID (judge-flagged" in block
    assert "X did Y" in block
    assert "READER CORRECTION" in block
    assert "June not May" in block
    # max 2 claims per row
    assert block.count("AVOID") == 2


def test_low_score_without_claims_teaches_nothing():
    m = _manager([_row(0.5)])
    assert _build(m) == ""


def test_empty_corpus_empty_block():
    assert _build(_manager()) == ""


def test_kill_switch():
    m = _manager([_row(1.0)])
    assert _build(m, settings=dict(_SETTINGS, sage_exemplars_enabled=False)) == ""


def test_unavailable_manager_empty():
    m = MagicMock()
    m.is_available.return_value = False
    assert _build(m) == ""


def test_budget_trims_block():
    long_answer = "word " * 500
    m = _manager([_row(1.0, answer=long_answer), _row(0.99, answer=long_answer,
                                                        created="2026-11-19T00:00:00+00:00")])
    block = _build(m, settings=dict(_SETTINGS, sage_exemplar_max_chars=800))
    assert len(block) <= 800
    assert block.endswith("--- END EXEMPLARS ---")


def test_never_raises():
    m = MagicMock()
    m.is_available.return_value = True
    m.get_sage_evaluations.side_effect = RuntimeError("down")
    assert _build(m) == ""


def test_newest_positives_preferred():
    m = _manager([
        _row(1.0, "older", created="2026-11-01T00:00:00+00:00"),
        _row(1.0, "newest", created="2026-11-25T00:00:00+00:00"),
        _row(1.0, "middle", created="2026-11-10T00:00:00+00:00"),
    ])
    block = _build(m)
    assert "newest" in block and "middle" in block
    assert "older" not in block  # MAX_POSITIVES = 2