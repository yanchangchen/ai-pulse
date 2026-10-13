"""Tests for the keyword_suggestions dedupe/apply helpers in
core/quality_schema.py (closed-loop watchlist suggestions)."""
from unittest.mock import MagicMock

import pytest


THEME = "Agentic Systems & DevTools"


@pytest.fixture()
def _clean_themes(monkeypatch):
    """Give get_all_keywords() a deterministic keyword set."""
    from config import themes

    monkeypatch.setattr(themes, "THEMES", {
        THEME: {"keywords": {"MCP": 3, "agentic": 2}},
        "Frontier Models & Benchmarks": {"keywords": {"benchmark": 3}},
    })
    yield themes


class TestFilterKeywordRowsForInsert:
    def _rows(self):
        return [
            {"kind": "watchlist_term", "term": "mcp", "reason": "[Models] dupe"},
            {"kind": "watchlist_term", "term": "agentic sdLC", "reason": "[Models] new"},
            {"kind": "theme_keyword", "theme_name": THEME, "term": "MCP", "suggested_weight": 2},
            {"kind": "theme_keyword", "theme_name": THEME, "term": "fresh-term", "suggested_weight": 2},
        ]

    def test_drops_terms_already_in_any_theme(self, _clean_themes):
        from core.quality_schema import filter_keyword_rows_for_insert

        out = filter_keyword_rows_for_insert(None, self._rows(), active_rows=[])
        terms = {r["term"] for r in out}
        # "mcp" and "MCP" both collapse onto the existing keyword, case-insensitively.
        assert "mcp" not in terms and "MCP" not in terms
        assert "agentic sdLC" in terms and "fresh-term" in terms

    def test_drops_watchlist_term_with_active_db_row(self, _clean_themes):
        from core.quality_schema import filter_keyword_rows_for_insert

        active = [{"kind": "watchlist_term", "term": "agentic sdLC", "status": "pending"}]
        out = filter_keyword_rows_for_insert(None, self._rows(), active_rows=active)
        assert {r["term"] for r in out} == {"fresh-term"}

    def test_drops_theme_row_with_same_theme_and_term(self, _clean_themes):
        from core.quality_schema import filter_keyword_rows_for_insert

        active = [{"kind": "theme_keyword", "theme_name": THEME, "term": "fresh-term", "status": "applied"}]
        out = filter_keyword_rows_for_insert(None, self._rows(), active_rows=active)
        assert {r["term"] for r in out} == {"agentic sdLC"}

    def test_same_term_other_theme_is_kept(self, _clean_themes):
        from core.quality_schema import filter_keyword_rows_for_insert

        rows = [{"kind": "theme_keyword", "theme_name": "Frontier Models & Benchmarks", "term": "fresh-term"}]
        active = [{"kind": "theme_keyword", "theme_name": THEME, "term": "fresh-term"}]
        out = filter_keyword_rows_for_insert(None, rows, active_rows=active)
        assert len(out) == 1

    def test_dedupes_within_batch(self, _clean_themes):
        from core.quality_schema import filter_keyword_rows_for_insert

        rows = [
            {"kind": "watchlist_term", "term": "agentic sdLC"},
            {"kind": "watchlist_term", "term": "Agentic SDLC"},
        ]
        out = filter_keyword_rows_for_insert(None, rows, active_rows=[])
        assert len(out) == 1

    def test_mock_supabase_never_raises(self, _clean_themes):
        """MagicMock supabase (as in test_eval_checkpoint) must not explode —
        the unusable fetch is treated as 'no DB info'."""
        from core.quality_schema import filter_keyword_rows_for_insert

        sb = MagicMock()
        sb.is_available.return_value = True
        sb.client.table.return_value.select.return_value.in_.return_value \
            .order.return_value.limit.return_value.execute.return_value = MagicMock()
        rows = self._rows()
        out = filter_keyword_rows_for_insert(sb, rows)
        # Only the never-raise contract and theme-keyword filtering apply.
        assert {r["term"] for r in out} == {"agentic sdLC", "fresh-term"}

    def test_empty_rows(self, _clean_themes):
        from core.quality_schema import filter_keyword_rows_for_insert

        assert filter_keyword_rows_for_insert(None, [], active_rows=[]) == []

    def test_auto_applied_rows_pass_through(self, _clean_themes):
        """The classifier's auto_applied audit rows record terms that are
        (by definition) already in THEMES — they must survive the filter."""
        from core.quality_schema import filter_keyword_rows_for_insert

        rows = [{"kind": "theme_keyword", "theme_name": THEME, "term": "MCP", "status": "auto_applied"}]
        out = filter_keyword_rows_for_insert(None, rows, active_rows=[])
        assert out == rows


class TestUpdateKeywordSuggestionStatusByTerms:
    def test_offline_returns_zero(self):
        from core.quality_schema import update_keyword_suggestion_status_by_terms

        assert update_keyword_suggestion_status_by_terms(None, ["x"], "applied") == 0

    def test_empty_terms_returns_zero(self):
        from core.quality_schema import update_keyword_suggestion_status_by_terms

        sb = MagicMock()
        assert update_keyword_suggestion_status_by_terms(sb, [], "applied") == 0
        sb.client.table.assert_not_called()

    def test_chains_kind_and_term_filters(self):
        from core.quality_schema import update_keyword_suggestion_status_by_terms

        sb = MagicMock()
        sb.is_available.return_value = True
        sb.client.table.return_value.update.return_value.eq.return_value \
            .in_.return_value.execute.return_value.data = [{"id": "1"}, {"id": "2"}]
        n = update_keyword_suggestion_status_by_terms(sb, ["a", "b"], "applied")
        assert n == 2
        table = sb.client.table.return_value
        table.update.assert_called_once_with({"status": "applied"})
        table.update.return_value.eq.assert_called_once_with("kind", "watchlist_term")
        table.update.return_value.eq.return_value.in_.assert_called_once_with("term", ["a", "b"])

    def test_failure_returns_zero(self):
        from core.quality_schema import update_keyword_suggestion_status_by_terms

        sb = MagicMock()
        sb.is_available.return_value = True
        sb.client.table.side_effect = RuntimeError("boom")
        assert update_keyword_suggestion_status_by_terms(sb, ["x"], "applied") == 0


class TestFetchKeywordSuggestionsByStatus:
    def test_offline_returns_empty(self):
        from core.quality_schema import fetch_keyword_suggestions_by_status

        assert fetch_keyword_suggestions_by_status(None, ["pending"]) == []

    def test_no_statuses_returns_empty(self):
        from core.quality_schema import fetch_keyword_suggestions_by_status

        sb = MagicMock()
        assert fetch_keyword_suggestions_by_status(sb, []) == []
        sb.client.table.assert_not_called()

    def test_query_failure_returns_empty(self):
        from core.quality_schema import fetch_keyword_suggestions_by_status

        sb = MagicMock()
        sb.is_available.return_value = True
        sb.client.table.side_effect = RuntimeError("boom")
        assert fetch_keyword_suggestions_by_status(sb, ["pending"]) == []
