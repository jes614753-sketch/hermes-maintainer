"""Tests for safety module — locks, confirmation, process detection."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hermes_maintainer.safety import (
    acquire_lock,
    release_lock,
    maintainer_lock,
    hermes_running,
    confirm_action,
)


class TestSingleInstanceLock:
    def test_acquire_and_release(self, tmp_path):
        assert acquire_lock(tmp_path) is True
        lock_file = tmp_path / ".maintainer.lock"
        assert lock_file.exists()
        data = json.loads(lock_file.read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid()
        release_lock(tmp_path)
        assert not lock_file.exists()

    def test_reacquire_after_release(self, tmp_path):
        assert acquire_lock(tmp_path) is True
        release_lock(tmp_path)
        assert acquire_lock(tmp_path) is True
        release_lock(tmp_path)

    def test_context_manager(self, tmp_path):
        with maintainer_lock(tmp_path):
            lock_file = tmp_path / ".maintainer.lock"
            assert lock_file.exists()
        # Lock should be released after context
        assert not lock_file.exists()

    def test_context_manager_releases_on_exception(self, tmp_path):
        lock_file = tmp_path / ".maintainer.lock"
        try:
            with maintainer_lock(tmp_path):
                assert lock_file.exists()
                raise ValueError("test error")
        except ValueError:
            pass
        assert not lock_file.exists()


class TestProcessDetection:
    def test_hermes_running_returns_bool(self):
        result = hermes_running()
        assert isinstance(result, bool)


class TestConfirmation:
    def test_low_risk_auto_confirms(self):
        assert confirm_action("test", risk="low") is True
