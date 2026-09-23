"""Tests for the cross-run Gate 3 classification cache
(core/classification_cache.py and its classifier integration)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import classification_cache
from core.classifier import classify_articles, get_latest_gate_stats


# ---------------------------------------------------------------------------
# classification_cache module
# ---------------------------------------------------------------------------

class TestClassificationCacheModule:
    def test_lookup_empty_hashes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(classification_cache, "LOCAL_CACHE_FILE", tmp_path / "cc.json")
        assert classification_cache.lookup_themes([]) == {}
        assert classification_cache.lookup_themes([None, ""]) == {}

    def test_mark_then_lookup_local(self, tmp_path, monkeypatch):
        f = tmp_path / "cc.json"
        monkeypatch.setattr(classification_cache, "LOCAL_CACHE_FILE", f)
        classification_cache.mark_classified({"h1": "Frontier Models & Benchmarks"})
        assert f.exists()
        found = classification_cache.lookup_themes(["h1", "h2"])
        assert found == {"h1": "Frontier Models & Benchmarks"}

    def test_mark_skips_empty_entries(self, tmp_path, monkeypatch):
        f = tmp_path / "cc.json"
        monkeypatch.setattr(classification_cache, "LOCAL_CACHE_FILE", f)
        classification_cache.mark_classified({"": "theme", None: "theme", "h": ""})
        assert classification_cache.lookup_themes(["", "h"]) == {}

    def test_lookup_prefers_supabase_on_conflict(self, tmp_path, monkeypatch):
        f = tmp_path / "cc.json"
        monkeypatch.setattr(classification_cache, "LOCAL_CACHE_FILE", f)
        classification_cache.mark_classified({"h1": "Local Theme"})

        mock_supabase = MagicMock()
        mock_supabase.is_available.return_value = True
        mock_supabase.get_classifications.return_value = {"h1": "Cloud Theme"}
        with patch("core.supabase_client.get_supabase_manager", return_value=mock_supabase):
            found = classification_cache.lookup_themes(["h1"])
        assert found == {"h1": "Cloud Theme"}

    def test_lookup_survives_supabase_failure(self, tmp_path, monkeypatch):
        f = tmp_path / "cc.json"
        monkeypatch.setattr(classification_cache, "LOCAL_CACHE_FILE", f)
        classification_cache.mark_classified({"h1": "Local Theme"})

        mock_supabase = MagicMock()
        mock_supabase.is_available.side_effect = RuntimeError("boom")
        with patch("core.supabase_client.get_supabase_manager", return_value=mock_supabase):
            found = classification_cache.lookup_themes(["h1"])
        assert found == {"h1": "Local Theme"}

    def test_corrupt_local_file_returns_empty(self, tmp_path, monkeypatch):
        f = tmp_path / "cc.json"
        f.write_text("not json {")
        monkeypatch.setattr(classification_cache, "LOCAL_CACHE_FILE", f)
        assert classification_cache.lookup_themes(["h1"]) == {}


# ---------------------------------------------------------------------------
# classifier integration
# ---------------------------------------------------------------------------

# Articles that miss Gate 1 (no theme keywords) AND Gate 2 (zero TF-IDF
# similarity), so they land in Gate 3
def _unmatched_article(title: str, content_hash: str) -> dict:
    return {
        "title": title,
        "summary": "Local community notes and observations.",
        "content_hash": content_hash,
        "link": f"https://example.com/{content_hash}",
    }


_TITLES = {
    "a": "Whiskers the tabby cat wins regional baking ribbon",
    "b": "Alpine wildflower bloom peaks earlier this decade",
    "c": "Historic lighthouse lens polished by volunteers",
}


class TestClassifierCacheIntegration:
    def test_cache_hit_skips_gateway(self, tmp_path, monkeypatch):
        """An article classified in a previous run is not re-sent to the LLM."""
        cache_file = tmp_path / "cc.json"
        monkeypatch.setattr(classification_cache, "LOCAL_CACHE_FILE", cache_file)
        classification_cache.mark_classified(
            {"hash-known": "Frontier Models & Benchmarks"}
        )

        mock_result = MagicMock()
        mock_result.is_success.return_value = True
        mock_result.result = '{"ID 0": "Hardware, Compute & LLMOps"}'
        mock_prov = MagicMock()
        mock_prov.to_dict.return_value = {}
        mock_result.provenance = mock_prov

        articles = [
            _unmatched_article(_TITLES["a"], "hash-known"),
            _unmatched_article(_TITLES["b"], "hash-new"),
        ]
        with patch("config.settings.get_classification_settings", return_value={
            "classification_mode": "hybrid", "gate3_auto_disable_threshold": 0.05,
            "gate_stats_history": [],
        }), patch("config.settings.update_classification_settings"), \
             patch("core.classifier.get_gateway") as mock_gw:
            mock_gw.return_value.execute = AsyncMock(return_value=mock_result)
            result = classify_articles(articles, skip_llm=False)

        # Gateway was still called — but only for the unknown article
        # (the batch prompt contains just its title).
        assert mock_gw.return_value.execute.called
        call_prompt = mock_gw.return_value.execute.call_args[0][0].input
        assert _TITLES["a"] not in call_prompt
        assert _TITLES["b"] in call_prompt

        # The cached article kept its cached theme, tagged as a cache hit
        cached_article = next(
            a for a in result["Frontier Models & Benchmarks"]
            if a["content_hash"] == "hash-known"
        )
        assert cached_article["gate"] == 3
        assert cached_article["gate_provenance"]["provider"] == "classification-cache"

        # Gate stats record the hit
        stats = get_latest_gate_stats()
        assert stats["gate_3_cache_hits"] == 1
        assert stats["gate_3_llm"] == 2  # 1 cache hit + 1 LLM result

    def test_all_cached_no_gateway_call(self, tmp_path, monkeypatch):
        """When every unmatched article is cached, the gateway is never called."""
        cache_file = tmp_path / "cc.json"
        monkeypatch.setattr(classification_cache, "LOCAL_CACHE_FILE", cache_file)
        classification_cache.mark_classified({
            "h1": "Frontier Models & Benchmarks",
            "h2": "Governance, Safety & Policy",
        })

        articles = [
            _unmatched_article(_TITLES["a"], "h1"),
            _unmatched_article(_TITLES["b"], "h2"),
        ]
        with patch("config.settings.get_classification_settings", return_value={
            "classification_mode": "hybrid", "gate3_auto_disable_threshold": 0.05,
            "gate_stats_history": [],
        }), patch("config.settings.update_classification_settings"), \
             patch("core.classifier.get_gateway") as mock_gw:
            result = classify_articles(articles, skip_llm=False)

        assert not mock_gw.return_value.execute.called
        assert sum(len(v) for v in result.values()) == 2
        stats = get_latest_gate_stats()
        assert stats["gate_3_cache_hits"] == 2

    def test_stale_theme_in_cache_is_ignored(self, tmp_path, monkeypatch):
        """A cached theme no longer in THEMES falls through to the LLM."""
        cache_file = tmp_path / "cc.json"
        monkeypatch.setattr(classification_cache, "LOCAL_CACHE_FILE", cache_file)
        classification_cache.mark_classified({"h1": "Old Retired Theme"})

        mock_result = MagicMock()
        mock_result.is_success.return_value = True
        mock_result.result = '{"ID 0": "Frontier Models & Benchmarks"}'
        mock_prov = MagicMock()
        mock_prov.to_dict.return_value = {}
        mock_result.provenance = mock_prov

        articles = [_unmatched_article(_TITLES["a"], "h1")]
        with patch("config.settings.get_classification_settings", return_value={
            "classification_mode": "hybrid", "gate3_auto_disable_threshold": 0.05,
            "gate_stats_history": [],
        }), patch("config.settings.update_classification_settings"), \
             patch("core.classifier.get_gateway") as mock_gw:
            mock_gw.return_value.execute = AsyncMock(return_value=mock_result)
            classify_articles(articles, skip_llm=False)

        assert mock_gw.return_value.execute.called
        stats = get_latest_gate_stats()
        assert stats["gate_3_cache_hits"] == 0

    def test_fresh_llm_result_is_marked_cached(self, tmp_path, monkeypatch):
        """A Gate 3 LLM result is persisted so the next run can reuse it."""
        cache_file = tmp_path / "cc.json"
        monkeypatch.setattr(classification_cache, "LOCAL_CACHE_FILE", cache_file)

        mock_result = MagicMock()
        mock_result.is_success.return_value = True
        mock_result.result = '{"ID 0": "Frontier Models & Benchmarks"}'
        mock_prov = MagicMock()
        mock_prov.to_dict.return_value = {}
        mock_result.provenance = mock_prov

        articles = [_unmatched_article(_TITLES["a"], "h1")]
        with patch("config.settings.get_classification_settings", return_value={
            "classification_mode": "hybrid", "gate3_auto_disable_threshold": 0.05,
            "gate_stats_history": [],
        }), patch("config.settings.update_classification_settings"), \
             patch("core.classifier.get_gateway") as mock_gw:
            mock_gw.return_value.execute = AsyncMock(return_value=mock_result)
            classify_articles(articles, skip_llm=False)

        stored = json.loads(cache_file.read_text())
        assert stored == {"h1": "Frontier Models & Benchmarks"}
