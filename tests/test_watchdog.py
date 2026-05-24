"""Tests for watchdog module — status, install/uninstall (mocked)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from hermes_maintainer.config import MaintainerConfig
from hermes_maintainer.watchdog import (
    WatchdogStatus,
    get_watchdog_status,
    _load_state,
    _save_state,
    watchdog_run,
)


class TestWatchdogStatus:
    def test_defaults(self):
        s = WatchdogStatus()
        assert s.installed is False
        assert s.running is False
        assert s.restart_count == 0

    def test_to_dict(self):
        s = WatchdogStatus(installed=True, running=True, message="ok")
        d = s.to_dict()
        assert d["installed"] is True
        assert d["running"] is True
        assert d["message"] == "ok"


class TestWatchdogState:
    def test_load_state_creates_defaults(self, tmp_path):
        state = _load_state(tmp_path)
        assert state["restart_count"] == 0
        assert state["last_check"] == ""

    def test_save_and_load_state(self, tmp_path):
        _save_state(tmp_path, {"restart_count": 2, "last_check": "2026-01-01"})
        state = _load_state(tmp_path)
        assert state["restart_count"] == 2
        assert state["last_check"] == "2026-01-01"


class TestWatchdogRun:
    def test_run_no_hermes_home(self, tmp_path):
        cfg = MaintainerConfig()
        cfg.hermes_home = tmp_path / "nonexistent"
        # Should not raise
        watchdog_run(cfg)

    def test_run_with_healthy_hermes(self, tmp_path):
        # Create minimal hermes home with .env
        (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")
        cfg = MaintainerConfig()
        cfg.hermes_home = tmp_path
        cfg.watchdog.auto_restart = False  # Don't try to actually restart
        # Should not raise
        watchdog_run(cfg)
        state = _load_state(tmp_path)
        assert "last_check" in state
