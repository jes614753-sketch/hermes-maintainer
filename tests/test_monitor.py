"""Tests for monitor module."""

from pathlib import Path

import pytest

from hermes_maintainer.config import MaintainerConfig
from hermes_maintainer.monitor import (
    HealthReport,
    CheckResult,
    check_process,
    check_disk,
    check_config,
    run_health_check,
)


def test_check_result_dataclass():
    r = CheckResult(name="test", status="ok", message="fine")
    assert r.name == "test"
    assert r.status == "ok"
    assert r.details == {}


def test_health_report_add_and_score():
    report = HealthReport(timestamp="2026-01-01", overall_score=0)
    report.add(CheckResult("a", "ok", "fine"))
    report.add(CheckResult("b", "warn", "issue"))
    report.add(CheckResult("c", "error", "bad"))

    assert len(report.checks) == 3
    assert len(report.warnings) == 1
    assert len(report.errors) == 1

    score = report.compute_score()
    # ok=100, warn=60, error=0 -> avg = 160/3 = 53
    assert 50 <= score <= 55


def test_health_report_to_dict():
    report = HealthReport(timestamp="2026-01-01", overall_score=80)
    report.add(CheckResult("test", "ok", "all good"))
    d = report.to_dict()
    assert d["overall_score"] == 80
    assert len(d["checks"]) == 1
    assert d["checks"][0]["name"] == "test"


def test_check_process_runs():
    result = check_process()
    assert result.name == "process"
    assert result.status in ("ok", "warn")


def test_check_disk_with_valid_path(tmp_path):
    result = check_disk(tmp_path, warn_pct=99.0)
    assert result.name == "disk"
    assert result.status in ("ok", "warn", "error")


def test_check_config_missing_env(tmp_path):
    result = check_config(tmp_path)
    assert result.name == "config"
    assert result.status == "warn"
    assert "missing" in result.message.lower() or ".env" in result.message


def test_run_health_check_no_hermes_home(tmp_path):
    cfg = MaintainerConfig()
    cfg.hermes_home = tmp_path / "nonexistent_hermes"
    report = run_health_check(cfg)
    # Should still run but with degraded results (no config, no sqlite, etc.)
    assert len(report.checks) >= 1


def test_run_health_check_with_mock_home(tmp_path):
    # Create minimal hermes home structure
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_KEY=sk-test123\n", encoding="utf-8")

    cfg = MaintainerConfig()
    cfg.hermes_home = tmp_path
    report = run_health_check(cfg, quick=True)

    # Should have at least process, config, disk checks
    assert len(report.checks) >= 3
    check_names = {c.name for c in report.checks}
    assert "config" in check_names
    assert "disk" in check_names
