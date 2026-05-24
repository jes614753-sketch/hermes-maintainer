"""Tests for updater module — snapshots, rollback, version check."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_maintainer.updater import (
    create_snapshot,
    list_snapshots,
    rollback,
    get_current_version,
    check_for_update,
    run_update,
)


class TestVersionDetection:
    def test_get_current_version_no_pyproject(self, tmp_path):
        version = get_current_version(tmp_path)
        assert version == "unknown"

    def test_get_current_version_with_pyproject(self, tmp_path):
        agent_dir = tmp_path / "hermes-agent"
        agent_dir.mkdir()
        pyproject = agent_dir / "pyproject.toml"
        pyproject.write_text('version = "0.13.0"\nname = "hermes-agent"\n', encoding="utf-8")
        version = get_current_version(tmp_path)
        assert version == "0.13.0"


class TestSnapshots:
    def test_create_snapshot_creates_files(self, tmp_path):
        # Create minimal hermes home
        (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")
        (tmp_path / "config.yaml").write_text("key: value\n", encoding="utf-8")

        snap = create_snapshot(tmp_path, reason="test")
        assert snap.path.exists()
        assert (snap.path / ".env").exists()
        assert (snap.path / "config.yaml").exists()
        assert (snap.path / "metadata.json").exists()

        meta = json.loads((snap.path / "metadata.json").read_text(encoding="utf-8"))
        assert meta["reason"] == "test"

    def test_list_snapshots_empty(self, tmp_path):
        snaps = list_snapshots(tmp_path)
        assert snaps == []

    def test_list_snapshots_after_create(self, tmp_path):
        (tmp_path / ".env").write_text("key=val\n", encoding="utf-8")
        create_snapshot(tmp_path, reason="test1")
        import time
        time.sleep(1.1)  # Ensure different timestamp
        create_snapshot(tmp_path, reason="test2")
        snaps = list_snapshots(tmp_path)
        assert len(snaps) == 2
        # Should be sorted newest first
        assert snaps[0].reason == "test2"

    def test_rollback_with_no_snapshots(self, tmp_path):
        report = rollback(tmp_path)
        assert report.status == "failed"
        assert "No snapshots" in report.message

    def test_rollback_restores_files(self, tmp_path):
        # Create original state
        env_file = tmp_path / ".env"
        env_file.write_text("ORIGINAL_KEY=abc\n", encoding="utf-8")

        # Create snapshot
        snap = create_snapshot(tmp_path, reason="backup")

        # Modify original
        env_file.write_text("MODIFIED_KEY=xyz\n", encoding="utf-8")
        assert env_file.read_text(encoding="utf-8") == "MODIFIED_KEY=xyz\n"

        # Rollback
        report = rollback(tmp_path, snapshot=snap)
        assert report.status == "rolled-back"
        assert env_file.read_text(encoding="utf-8") == "ORIGINAL_KEY=abc\n"


class TestUpdateCheck:
    def test_check_for_update_returns_report(self, tmp_path):
        report = check_for_update(tmp_path)
        assert report.current_version in ("unknown", "0.13.0")
        assert report.status in ("unknown", "up-to-date", "update-available")

    def test_run_update_check_only(self, tmp_path):
        report = run_update(tmp_path, check_only=True)
        assert report.status in ("unknown", "up-to-date", "update-available")
