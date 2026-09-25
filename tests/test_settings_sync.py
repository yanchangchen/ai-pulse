"""Tests for the Supabase app_settings persistence of evaluation-driven
config (config/custom_settings.json and data/auto_remediation_state.json).

The sync is remote-first on load (Supabase row wins, local file refreshed
as offline cache) and write-through on save (local file + upsert).  All
Supabase interaction is mocked; the conftest autouse fixture already pins
the real manager offline.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

import config.settings as settings
import core.auto_remediation as ar
from core.supabase_client import SupabaseManager


@pytest.fixture(autouse=True)
def isolate_files(tmp_path, monkeypatch):
    """Point the settings/state files at tmp storage and reset the remote
    cache between tests (the cache is module state)."""
    monkeypatch.setattr(settings, "CUSTOM_SETTINGS_FILE", tmp_path / "custom_settings.json")
    monkeypatch.setattr(ar, "STATE_FILE", tmp_path / "state.json")
    settings._reset_custom_settings_remote_cache()
    yield
    settings._reset_custom_settings_remote_cache()


def _fake_manager(remote_value=None, available=True):
    m = MagicMock()
    m.is_available.return_value = available
    m.get_app_setting.return_value = remote_value
    m.upsert_app_setting.return_value = True
    return m


# ---------------------------------------------------------------------------
# SupabaseManager.get_app_setting / upsert_app_setting
# ---------------------------------------------------------------------------


class TestManagerAppSetting:
    def _manager(self, data):
        mgr = SupabaseManager.__new__(SupabaseManager)
        mgr.client = MagicMock()
        mgr.available = True
        mgr.client.table.return_value.select.return_value.eq.return_value \
            .limit.return_value.execute.return_value = MagicMock(data=data)
        return mgr

    def test_get_returns_value(self):
        mgr = self._manager([{"value": {"temperature": 0.1}}])
        assert mgr.get_app_setting("custom_settings") == {"temperature": 0.1}

    def test_get_returns_none_when_no_row(self):
        mgr = self._manager([])
        assert mgr.get_app_setting("custom_settings") is None

    def test_get_swallow_errors(self):
        mgr = self._manager([])
        mgr.client.table.side_effect = RuntimeError("schema missing")
        assert mgr.get_app_setting("custom_settings") is None

    def test_get_unavailable(self):
        mgr = self._manager([{"value": {}}])
        mgr.available = False
        assert mgr.get_app_setting("custom_settings") is None

    def test_upsert_writes_row(self):
        mgr = self._manager([])
        assert mgr.upsert_app_setting("custom_settings", {"a": 1}) is True
        table = mgr.client.table
        table.assert_called_once_with("app_settings")
        kwargs = table.return_value.upsert.call_args
        row = kwargs.args[0]
        assert row["key"] == "custom_settings"
        assert row["value"] == {"a": 1}
        assert kwargs.kwargs.get("on_conflict") == "key"

    def test_upsert_unavailable_returns_false(self):
        mgr = self._manager([])
        mgr.available = False
        assert mgr.upsert_app_setting("custom_settings", {}) is False


# ---------------------------------------------------------------------------
# config.settings load/save with remote-first semantics
# ---------------------------------------------------------------------------


class TestCustomSettingsSync:
    def test_remote_hit_wins_and_refreshes_local(self):
        settings._reset_custom_settings_remote_cache()
        m = _fake_manager(remote_value={"temperature": 0.2, "max_tokens": 900})
        with patch("core.supabase_client.get_supabase_manager", return_value=m):
            data = settings.load_custom_settings()
        assert data == {"temperature": 0.2, "max_tokens": 900}
        # The local file was refreshed as an offline cache.
        assert json.loads(settings.CUSTOM_SETTINGS_FILE.read_text(encoding="utf-8")) == data

    def test_remote_miss_falls_back_to_local(self):
        # Prime a local file first.
        settings.CUSTOM_SETTINGS_FILE.write_text(
            json.dumps({"temperature": 0.3}), encoding="utf-8"
        )
        settings._reset_custom_settings_remote_cache()
        m = _fake_manager(remote_value=None)
        with patch("core.supabase_client.get_supabase_manager", return_value=m):
            data = settings.load_custom_settings()
        assert data == {"temperature": 0.3}

    def test_save_writes_local_and_upserts_remote(self):
        m = _fake_manager(remote_value=None)
        with patch("core.supabase_client.get_supabase_manager", return_value=m):
            settings.save_custom_settings({"temperature": 0.15})
            # The cache is refreshed, so an immediate load returns the new
            # value without re-fetching.
            assert settings.load_custom_settings() == {"temperature": 0.15}
        assert json.loads(settings.CUSTOM_SETTINGS_FILE.read_text(encoding="utf-8")) == {
            "temperature": 0.15
        }
        m.upsert_app_setting.assert_called_once_with(
            "custom_settings", {"temperature": 0.15}
        )

    def test_remote_fetch_is_ttl_cached(self):
        settings._reset_custom_settings_remote_cache()
        m = _fake_manager(remote_value={"temperature": 0.2})
        with patch("core.supabase_client.get_supabase_manager", return_value=m):
            for _ in range(3):
                assert settings.load_custom_settings() == {"temperature": 0.2}
        # One remote fetch served all three loads (page reruns don't hammer
        # the database).
        assert m.get_app_setting.call_count == 1

    def test_local_read_after_save_within_ttl(self):
        # Write-through updates the cache, so a local hand-edit right after
        # a save is NOT masked on the next load.
        m = _fake_manager(remote_value=None)
        with patch("core.supabase_client.get_supabase_manager", return_value=m):
            settings.save_custom_settings({"temperature": 0.15})
        # Externally edit the local file (simulating another process /
        # offline fallback usage).
        settings.CUSTOM_SETTINGS_FILE.write_text(
            json.dumps({"temperature": 0.4}), encoding="utf-8"
        )
        settings._reset_custom_settings_remote_cache()
        m2 = _fake_manager(remote_value=None)
        with patch("core.supabase_client.get_supabase_manager", return_value=m2):
            assert settings.load_custom_settings() == {"temperature": 0.4}

    def test_update_summariser_settings_roundtrip(self):
        m = _fake_manager(remote_value=None)
        with patch("core.supabase_client.get_supabase_manager", return_value=m):
            settings.update_summariser_settings(0.25, 1200, True)
            active = settings.get_summariser_settings()
        assert active["temperature"] == 0.25
        assert active["max_tokens"] == 1200
        assert active["strict_faithfulness_mode"] is True
        m.upsert_app_setting.assert_called_once()


# ---------------------------------------------------------------------------
# core.auto_remediation state sync
# ---------------------------------------------------------------------------


class TestAutoRemediationStateSync:
    def test_remote_state_wins_and_refreshes_local(self):
        remote = {"pending_keyword_applies": {
            "Agentic Systems & DevTools": {
                "baseline_score": 0.8, "terms": {"mcp": 2},
            }
        }}
        m = _fake_manager(remote_value=remote)
        with patch("core.supabase_client.get_supabase_manager", return_value=m):
            state = ar._load_state()
        assert state == remote
        assert json.loads(ar.STATE_FILE.read_text(encoding="utf-8")) == remote

    def test_remote_miss_falls_back_to_local(self):
        local = {"pending_keyword_applies": {"T": {"baseline_score": 0.5, "terms": {}}}}
        ar.STATE_FILE.write_text(json.dumps(local), encoding="utf-8")
        m = _fake_manager(remote_value=None)
        with patch("core.supabase_client.get_supabase_manager", return_value=m):
            assert ar._load_state() == local

    def test_save_state_writes_local_and_remote(self):
        m = _fake_manager(remote_value=None)
        state = {"pending_keyword_applies": {"T": {"baseline_score": 0.9, "terms": {"ai": 1}}}}
        with patch("core.supabase_client.get_supabase_manager", return_value=m):
            ar._save_state(state)
        assert json.loads(ar.STATE_FILE.read_text(encoding="utf-8")) == state
        m.upsert_app_setting.assert_called_once_with("auto_remediation_state", state)

    def test_save_state_survives_remote_failure(self):
        m = _fake_manager(remote_value=None)
        m.upsert_app_setting.return_value = False
        state = {"pending_keyword_applies": {}}
        with patch("core.supabase_client.get_supabase_manager", return_value=m):
            ar._save_state(state)  # must not raise
        assert json.loads(ar.STATE_FILE.read_text(encoding="utf-8")) == state
