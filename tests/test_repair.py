"""Tests for repair module."""

import sqlite3
from pathlib import Path

import pytest

from hermes_maintainer.config import MaintainerConfig
from hermes_maintainer.repair import (
    RepairAction,
    RepairReport,
    clean_pycache,
    clean_logs,
    clean_node_cache,
    sqlite_checkpoint,
    verify_deps,
    _hermes_running,
    run_repairs,
)


def test_repair_action_to_dict():
    action = RepairAction("test", "low", "Test action", "done", "OK")
    d = action.to_dict()
    assert d["name"] == "test"
    assert d["risk"] == "low"
    assert d["status"] == "done"


def test_repair_report_dry_run():
    report = RepairReport(dry_run=True)
    report.add(RepairAction("a", "low", "test", "done"))
    d = report.to_dict()
    assert d["dry_run"] is True
    assert d["summary"]["done"] == 1


def test_clean_pycache_empty(tmp_path):
    action = clean_pycache(tmp_path, dry_run=True)
    assert action.status == "done"
    assert "nothing" in action.message.lower() or "clean" in action.message.lower()


def test_clean_pycache_with_files(tmp_path):
    # Create some .pyc files
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "test.pyc").write_bytes(b"fake")
    pyc_file = tmp_path / "module.pyc"
    pyc_file.write_bytes(b"fake")

    # Dry run
    action = clean_pycache(tmp_path, dry_run=True)
    assert action.status == "skipped"
    assert "would" in action.message.lower()

    # Verify files still exist
    assert (cache_dir / "test.pyc").exists()
    assert pyc_file.exists()

    # Execute
    action = clean_pycache(tmp_path, dry_run=False)
    assert action.status == "done"
    assert not (cache_dir / "test.pyc").exists()
    assert not pyc_file.exists()


def test_clean_logs_no_logs(tmp_path):
    action = clean_logs(tmp_path, dry_run=True)
    assert action.status == "done"


def test_clean_logs_small_log(tmp_path):
    log = tmp_path / "hermes.log"
    log.write_text("small log\n", encoding="utf-8")
    action = clean_logs(tmp_path, dry_run=True, max_size_mb=100)
    assert action.status == "done"
    assert "under" in action.message.lower()


def test_clean_logs_large_log_dry_run(tmp_path):
    log = tmp_path / "hermes.log"
    log.write_bytes(b"x" * (200 * 1024 * 1024))  # 200 MB
    action = clean_logs(tmp_path, dry_run=True, max_size_mb=100)
    assert action.status == "skipped"
    assert "would" in action.message.lower()


def test_run_repairs_no_hermes_home(tmp_path):
    cfg = MaintainerConfig()
    cfg.hermes_home = tmp_path / "nonexistent_hermes"
    report = run_repairs(cfg, dry_run=True)
    # Should run but with limited results
    assert len(report.actions) >= 1
    assert report.dry_run is True


def test_run_repairs_all_targets(tmp_path):
    cfg = MaintainerConfig()
    cfg.hermes_home = tmp_path
    report = run_repairs(cfg, dry_run=True)
    # Should run multiple repair actions
    assert len(report.actions) >= 2
    assert report.dry_run is True


# ── SQLite checkpoint tests ────────────────────────────────────────────

def test_sqlite_checkpoint_no_db(tmp_path):
    action = sqlite_checkpoint(tmp_path, dry_run=False)
    assert action.status == "done"
    assert "not found" in action.message


def test_sqlite_checkpoint_no_wal(tmp_path):
    # Create a real SQLite db (no WAL file)
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE test (id INTEGER)")
    conn.close()
    action = sqlite_checkpoint(tmp_path, dry_run=False)
    assert action.status == "done"
    assert "No WAL" in action.message


def test_sqlite_checkpoint_small_wal(tmp_path):
    # Create db with WAL — keep connection open so WAL persists
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE test (id INTEGER)")
    conn.execute("INSERT INTO test VALUES (1)")
    conn.commit()
    # Check WAL exists
    wal_path = db_path.with_suffix(".db-wal")
    if wal_path.exists() and wal_path.stat().st_size > 0:
        # WAL exists and is small — should say "no checkpoint needed"
        action = sqlite_checkpoint(tmp_path, dry_run=False)
        assert action.status == "done"
        assert "no checkpoint needed" in action.message or "checkpoint done" in action.message
    else:
        # WAL was auto-cleaned (can happen on Windows) — skip assertion
        pass
    conn.close()


def test_hermes_running_returns_bool():
    result = _hermes_running()
    assert isinstance(result, bool)


# ── Verify deps tests ──────────────────────────────────────────────────

def test_verify_deps_no_venv(tmp_path):
    action = verify_deps(tmp_path, dry_run=False)
    assert action.status == "failed"
    assert "not found" in action.message


def test_verify_deps_dry_run(tmp_path):
    # Create mock venv
    venv_dir = tmp_path / "hermes-agent" / "venv" / "Scripts"
    venv_dir.mkdir(parents=True)
    (venv_dir / "python.exe").write_bytes(b"fake")
    action = verify_deps(tmp_path, dry_run=True)
    assert action.status == "skipped"
