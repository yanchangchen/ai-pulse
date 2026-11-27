"""Tests for the eval-set registry (core/eval_sets.py) and the retrieval
eval's wiring to it (core/retrieval_eval.py)."""
import json

from core.eval_sets import (
    DEFAULT_PAGE_SIZE,
    discover_eval_sets,
    load_eval_set,
    paginate,
    get_latest_results,
)


def test_discover_finds_retrieval_set():
    sets = discover_eval_sets()
    ids = [s["id"] for s in sets]
    assert "retrieval-hit-rate" in ids
    s = next(x for x in sets if x["id"] == "retrieval-hit-rate")
    assert s["name"]
    assert s["metric"] == "Recall@k"
    assert len(s["questions"]) == 24


def test_retrieval_set_questions_are_well_formed():
    s = load_eval_set("retrieval-hit-rate")
    assert s is not None
    ids = set()
    for q in s["questions"]:
        assert q.get("question")
        assert q.get("expected_keywords"), "every question needs ground-truth keywords"
        assert all(isinstance(k, str) and k == k.lower() for k in q["expected_keywords"])
        assert q.get("k", 10) > 0
        assert q.get("id") and q["id"] not in ids, "question ids must be unique"
        ids.add(q["id"])


def test_load_unknown_set_returns_none():
    assert load_eval_set("does-not-exist") is None


def test_discover_skips_invalid_files(tmp_path):
    (tmp_path / "good.json").write_text(json.dumps({
        "id": "good", "name": "Good", "questions": [{"question": "q?"}],
    }), encoding="utf-8")
    (tmp_path / "no_questions.json").write_text(json.dumps({
        "id": "bad", "name": "Bad", "questions": [],
    }), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "not_object.json").write_text("[1, 2]", encoding="utf-8")
    sets = discover_eval_sets(tmp_path)
    assert [s["id"] for s in sets] == ["good"]


def test_discover_missing_directory(tmp_path):
    assert discover_eval_sets(tmp_path / "nope") == []


def test_paginate_basic():
    items = list(range(45))
    page_items, page, total = paginate(items, 1, 20)
    assert page_items == list(range(20)) and page == 1 and total == 3
    page_items, page, total = paginate(items, 3, 20)
    assert page_items == list(range(40, 45)) and page == 3 and total == 3


def test_paginate_clamps_out_of_range():
    items = list(range(5))
    page_items, page, total = paginate(items, 99, 20)
    assert page == 1 and total == 1 and page_items == items
    page_items, page, total = paginate(items, -3, 20)
    assert page == 1 and page_items == items


def test_paginate_empty_list():
    page_items, page, total = paginate([], 1, 20)
    assert page_items == [] and page == 1 and total == 1


def test_paginate_default_page_size():
    assert DEFAULT_PAGE_SIZE == 20
    items = list(range(21))
    _, _, total = paginate(items, 1)
    assert total == 2


def test_get_latest_results_offline_is_none():
    # conftest pins Supabase offline; must degrade, not raise.
    s = load_eval_set("retrieval-hit-rate")
    assert get_latest_results(s) is None
    assert get_latest_results({"id": "x"}) is None  # no results_key


def test_retrieval_eval_loads_from_registry():
    from core.retrieval_eval import load_questions

    questions = load_questions()
    assert len(questions) == 24
    assert questions[0]["id"] == "q01"


def test_retrieval_eval_recall_carries_id():
    from core.retrieval_eval import _recall_for

    q = {"id": "q01", "question": "test?", "expected_keywords": ["alpha", "beta"]}
    r = _recall_for(q, [{"chunk_text": "Alpha only"}], 10)
    assert r["id"] == "q01"
    assert r["recall"] == 0.5 and r["hits"] == 1 and r["expected"] == 2