"""Tests for updater module — snapshots, rollback, version check."""

from __future__ import annotations

import json
import sqlite3
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
        time.sleep(1.1)
        create_snapshot(tmp_path, reason="test2")
        snaps = list_snapshots(tmp_path)
        assert len(snaps) == 2
        assert snaps[0].reason == "test2"

    @patch("hermes_maintainer.updater.hermes_running", return_value=False)
    def test_rollback_with_no_snapshots(self, mock_hr, tmp_path):
        report = rollback(tmp_path)
        assert report.status == "failed"
        assert "No snapshots" in report.message

    @patch("hermes_maintainer.updater.hermes_running", return_value=False)
    def test_rollback_restores_files(self, mock_hr, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("ORIGINAL_KEY=abc\n", encoding="utf-8")

        snap = create_snapshot(tmp_path, reason="backup")

        env_file.write_text("MODIFIED_KEY=xyz\n", encoding="utf-8")
        assert env_file.read_text(encoding="utf-8") == "MODIFIED_KEY=xyz\n"

        report = rollback(tmp_path, snapshot=snap)
        assert report.status == "rolled-back"
        assert env_file.read_text(encoding="utf-8") == "ORIGINAL_KEY=abc\n"

    @patch("hermes_maintainer.updater.hermes_running", return_value=False)
    def test_rollback_creates_pre_rollback_snapshot(self, mock_hr, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("V1\n", encoding="utf-8")
        snap1 = create_snapshot(tmp_path, reason="v1")

        env_file.write_text("V2\n", encoding="utf-8")

        rollback(tmp_path, snapshot=snap1)

        snaps = list_snapshots(tmp_path)
        reasons = [s.reason for s in snaps]
        assert "pre-rollback" in reasons

    def test_create_snapshot_uses_backup_api(self, tmp_path):
        db_path = tmp_path / "state.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.close()

        snap = create_snapshot(tmp_path, reason="test")
        backup_conn = sqlite3.connect(str(snap.path / "state.db"))
        row = backup_conn.execute("SELECT id FROM t").fetchone()
        assert row == (1,)
        backup_conn.close()


class TestBackupSqlite:
    def test_backup_api_copies_data(self, tmp_path):
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

    def test_backup_api_raises_on_corrupt_db(self, tmp_path):
        """A corrupt .db file should cause RuntimeError, not raw copy."""
        src = tmp_path / "corrupt.db"
        src.write_bytes(b"not a valid sqlite database")
        dst = tmp_path / "dst.db"
        with pytest.raises(RuntimeError, match="SQLite backup failed"):
            _backup_sqlite(src, dst)


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

    @patch("hermes_maintainer.updater.get_latest_version_from_github", return_value="0.14.0")
    @patch("hermes_maintainer.updater.hermes_running", return_value=False)
    def test_run_update_acquires_lock(self, mock_hr, mock_gh, tmp_path):
        """run_update should acquire maintainer_lock when updating."""
        agent_dir = tmp_path / "hermes-agent"
        agent_dir.mkdir()
        (agent_dir / "pyproject.toml").write_text('version = "0.13.0"\n', encoding="utf-8")
        with patch("hermes_maintainer.updater.maintainer_lock") as mock_lock:
            mock_lock.return_value.__enter__ = MagicMock()
            mock_lock.return_value.__exit__ = MagicMock(return_value=False)
            with patch("hermes_maintainer.updater._run_update_inner") as mock_inner:
                from hermes_maintainer.updater import UpdateReport
                expected = UpdateReport(status="updated", message="ok")
                mock_inner.return_value = expected
                report = run_update(tmp_path, check_only=False)
        mock_lock.assert_called_once_with(tmp_path)

    @patch("hermes_maintainer.updater.get_latest_version_from_github", return_value="0.14.0")
    @patch("hermes_maintainer.updater.hermes_running", return_value=True)
    def test_run_update_hermes_running_refused(self, mock_hr, mock_gh, tmp_path):
        """run_update should refuse when Hermes is running without --force."""
        agent_dir = tmp_path / "hermes-agent"
        agent_dir.mkdir()
        (agent_dir / "pyproject.toml").write_text('version = "0.13.0"\n', encoding="utf-8")
        report = run_update(tmp_path, check_only=False)
        assert report.status == "failed"
        assert "Hermes is running" in report.message

    @patch("hermes_maintainer.updater.get_latest_version_from_github", return_value="0.14.0")
    @patch("hermes_maintainer.updater.hermes_running", return_value=True)
    def test_run_update_force_overrides(self, mock_hr, mock_gh, tmp_path):
        """run_update(force=True) should proceed even when Hermes is running."""
        agent_dir = tmp_path / "hermes-agent"
        agent_dir.mkdir()
        (agent_dir / "pyproject.toml").write_text('version = "0.13.0"\n', encoding="utf-8")
        with patch("hermes_maintainer.updater.maintainer_lock") as mock_lock:
            mock_lock.return_value.__enter__ = MagicMock()
            mock_lock.return_value.__exit__ = MagicMock(return_value=False)
            with patch("hermes_maintainer.updater._run_update_inner") as mock_inner:
                from hermes_maintainer.updater import UpdateReport
                mock_inner.return_value = UpdateReport(status="updated", message="ok")
                report = run_update(tmp_path, check_only=False, force=True)
        assert report.status == "updated"
        mock_lock.assert_called_once()


class TestRollbackLock:
    @patch("hermes_maintainer.updater.hermes_running", return_value=False)
    def test_rollback_acquires_lock(self, mock_hr, tmp_path):
        """rollback should acquire maintainer_lock."""
        with patch("hermes_maintainer.updater.maintainer_lock") as mock_lock:
            mock_lock.return_value.__enter__ = MagicMock()
            mock_lock.return_value.__exit__ = MagicMock(return_value=False)
            with patch("hermes_maintainer.updater._rollback_inner") as mock_inner:
                from hermes_maintainer.updater import UpdateReport
                expected = UpdateReport(status="rolled-back", message="ok")
                mock_inner.return_value = expected
                report = rollback(tmp_path)
        mock_lock.assert_called_once_with(tmp_path)
        assert report.status == "rolled-back"

    @patch("hermes_maintainer.updater.hermes_running", return_value=False)
    def test_rollback_lock_failure(self, mock_hr, tmp_path):
        """rollback should report failure if lock cannot be acquired."""
        with patch("hermes_maintainer.updater.maintainer_lock") as mock_lock:
            mock_lock.return_value.__enter__ = MagicMock(side_effect=RuntimeError("Lock held"))
            mock_lock.return_value.__exit__ = MagicMock(return_value=False)
            report = rollback(tmp_path)
        assert report.status == "failed"
        assert "Lock held" in report.message

    @patch("hermes_maintainer.updater.hermes_running", return_value=True)
    def test_rollback_hermes_running_refused(self, mock_hr, tmp_path):
        """rollback should refuse when Hermes is running without --force."""
        report = rollback(tmp_path)
        assert report.status == "failed"
        assert "Hermes is running" in report.message

    @patch("hermes_maintainer.updater.hermes_running", return_value=True)
    def test_rollback_force_overrides(self, mock_hr, tmp_path):
        """rollback(force=True) should proceed even when Hermes is running."""
        with patch("hermes_maintainer.updater.maintainer_lock") as mock_lock:
            mock_lock.return_value.__enter__ = MagicMock()
            mock_lock.return_value.__exit__ = MagicMock(return_value=False)
            with patch("hermes_maintainer.updater._rollback_inner") as mock_inner:
                from hermes_maintainer.updater import UpdateReport
                mock_inner.return_value = UpdateReport(status="rolled-back", message="ok")
                report = rollback(tmp_path, force=True)
        assert report.status == "rolled-back"
        mock_lock.assert_called_once()

    def test_rollback_snapshot_failure_aborts(self, tmp_path):
        """rollback should abort if pre-rollback snapshot creation fails."""
        env_file = tmp_path / ".env"
        env_file.write_text("ORIGINAL\n", encoding="utf-8")
        snap = create_snapshot(tmp_path, reason="backup")
        env_file.write_text("MODIFIED\n", encoding="utf-8")

        # Patch create_snapshot to fail inside _rollback_inner
        with patch("hermes_maintainer.updater.create_snapshot", side_effect=RuntimeError("disk full")):
            with patch("hermes_maintainer.updater.hermes_running", return_value=False):
                report = rollback(tmp_path, snapshot=snap)

        assert report.status == "failed"
        assert "Pre-rollback snapshot failed" in report.message
        # Original file must NOT be overwritten
        assert env_file.read_text(encoding="utf-8") == "MODIFIED\n"
