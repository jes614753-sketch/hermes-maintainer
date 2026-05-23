"""Tests for repair module."""

from pathlib import Path

import pytest

from hermes_maintainer.config import MaintainerConfig
from hermes_maintainer.repair import (
    RepairAction,
    RepairReport,
    clean_pycache,
    clean_logs,
    clean_node_cache,
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
