"""Tests for updater module — snapshots, rollback, version check."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from hermes_maintainer.updater import (
    create_snapshot,
    list_snapshots,
    rollback,
    get_current_version,
    check_for_update,
    run_update,
    _backup_sqlite,
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

    def test_rollback_creates_pre_rollback_snapshot(self, tmp_path):
        """Rollback should snapshot current state before overwriting."""
        env_file = tmp_path / ".env"
        env_file.write_text("V1\n", encoding="utf-8")
        snap1 = create_snapshot(tmp_path, reason="v1")

        env_file.write_text("V2\n", encoding="utf-8")

        # Rollback to V1 — should create a pre-rollback snapshot
        rollback(tmp_path, snapshot=snap1)

        snaps = list_snapshots(tmp_path)
        reasons = [s.reason for s in snaps]
        assert "pre-rollback" in reasons

    def test_create_snapshot_uses_backup_api(self, tmp_path):
        """state.db should be backed up via SQLite backup API."""
        import sqlite3
        db_path = tmp_path / "state.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.close()

        snap = create_snapshot(tmp_path, reason="test")
        # Verify the backup is a valid SQLite db
        backup_conn = sqlite3.connect(str(snap.path / "state.db"))
        row = backup_conn.execute("SELECT id FROM t").fetchone()
        assert row == (1,)
        backup_conn.close()


class TestBackupSqlite:
    def test_backup_api_copies_data(self, tmp_path):
        import sqlite3
        src = tmp_path / "src.db"
        dst = tmp_path / "dst.db"
        conn = sqlite3.connect(str(src))
        conn.execute("CREATE TABLE x (val TEXT)")
        conn.execute("INSERT INTO x VALUES ('hello')")
        conn.commit()
        conn.close()

        _backup_sqlite(src, dst)
        conn2 = sqlite3.connect(str(dst))
        row = conn2.execute("SELECT val FROM x").fetchone()
        assert row == ("hello",)
        conn2.close()


class TestUpdateCheck:
    @patch("hermes_maintainer.updater.get_latest_version_from_github", return_value="0.13.0")
    def test_check_for_update_up_to_date(self, mock_gh, tmp_path):
        agent_dir = tmp_path / "hermes-agent"
        agent_dir.mkdir()
        (agent_dir / "pyproject.toml").write_text('version = "0.13.0"\n', encoding="utf-8")
        report = check_for_update(tmp_path)
        assert report.status == "up-to-date"
        assert report.update_available is False

    @patch("hermes_maintainer.updater.get_latest_version_from_github", return_value="0.14.0")
    def test_check_for_update_available(self, mock_gh, tmp_path):
        agent_dir = tmp_path / "hermes-agent"
        agent_dir.mkdir()
        (agent_dir / "pyproject.toml").write_text('version = "0.13.0"\n', encoding="utf-8")
        report = check_for_update(tmp_path)
        assert report.status == "update-available"
        assert report.update_available is True
        assert report.latest_version == "0.14.0"

    @patch("hermes_maintainer.updater.get_latest_version_from_github", return_value=None)
    def test_check_for_update_unknown(self, mock_gh, tmp_path):
        report = check_for_update(tmp_path)
        assert report.status == "unknown"

    @patch("hermes_maintainer.updater.get_latest_version_from_github", return_value="0.13.0")
    def test_run_update_check_only(self, mock_gh, tmp_path):
        agent_dir = tmp_path / "hermes-agent"
        agent_dir.mkdir()
        (agent_dir / "pyproject.toml").write_text('version = "0.13.0"\n', encoding="utf-8")
        report = run_update(tmp_path, check_only=True)
        assert report.status == "up-to-date"
        mock_gh.assert_called()

    @patch("hermes_maintainer.updater.get_latest_version_from_github", return_value=None)
    def test_run_update_no_update(self, mock_gh, tmp_path):
        report = run_update(tmp_path, check_only=False)
        assert report.status == "unknown"
