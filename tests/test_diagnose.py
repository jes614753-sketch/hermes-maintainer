"""Tests for diagnose module."""

from pathlib import Path

import pytest

from hermes_maintainer.config import MaintainerConfig
from hermes_maintainer.diagnose import (
    DiagnosticItem,
    DiagnosticReport,
    match_known_issues,
    check_environment,
    run_diagnostics,
)


def test_diagnostic_item_to_dict():
    item = DiagnosticItem("env", "warn", "Test", "Detail", "Fix it")
    d = item.to_dict()
    assert d["category"] == "env"
    assert d["severity"] == "warn"
    assert d["fix_hint"] == "Fix it"


def test_diagnostic_report_counts():
    report = DiagnosticReport()
    report.add(DiagnosticItem("a", "error", "err", "d"))
    report.add(DiagnosticItem("b", "warn", "warn", "d"))
    report.add(DiagnosticItem("c", "info", "info", "d"))
    assert report.error_count == 1
    assert report.warn_count == 1


def test_match_known_issues_401():
    matched = match_known_issues("401 authentication fails api key invalid")
    assert len(matched) > 0
    # Should match the no-key-required issue
    symptoms = [m["symptom"] for m in matched]
    assert any("401" in s or "key" in s.lower() for s in symptoms)


def test_match_known_issues_deepseek():
    matched = match_known_issues("deepseek v4 openrouter crash gateway")
    assert len(matched) > 0
    assert any("DeepSeek" in m["symptom"] for m in matched)


def test_match_known_issues_no_match():
    matched = match_known_issues("completely unrelated topic about cooking")
    assert len(matched) == 0


def test_check_environment(tmp_path):
    items = check_environment(tmp_path)
    assert len(items) > 0
    # Should at least check Python version
    python_items = [i for i in items if "Python" in i.title]
    assert len(python_items) >= 1


def test_run_diagnostics_no_hermes_home(tmp_path):
    cfg = MaintainerConfig()
    cfg.hermes_home = tmp_path / "nonexistent_hermes"
    report = run_diagnostics(cfg)
    # Should still produce some diagnostic items (env checks at minimum)
    assert len(report.items) >= 1


def test_run_diagnostics_with_mock_home(tmp_path):
    cfg = MaintainerConfig()
    cfg.hermes_home = tmp_path
    report = run_diagnostics(cfg)
    # Should have some items (env checks at minimum)
    assert len(report.items) >= 1
