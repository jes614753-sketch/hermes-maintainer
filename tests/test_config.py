"""Tests for config module."""

import tempfile
from pathlib import Path

import pytest
import yaml

from hermes_maintainer.config import (
    MaintainerConfig,
    load_config,
    save_config,
    discover_hermes_home,
)


def test_default_config():
    cfg = MaintainerConfig()
    assert cfg.watchdog.max_restart_attempts == 3
    assert cfg.monitor.api_timeout_sec == 10
    assert cfg.repair.dry_run_by_default is True


def test_save_and_load_config(tmp_path):
    cfg = MaintainerConfig()
    cfg.watchdog.max_restart_attempts = 5
    cfg.monitor.api_timeout_sec = 20

    path = tmp_path / "test-config.yaml"
    save_config(cfg, path=path)

    loaded = load_config(path=path)
    assert loaded.watchdog.max_restart_attempts == 5
    assert loaded.monitor.api_timeout_sec == 20


def test_load_nonexistent_config(tmp_path):
    path = tmp_path / "nonexistent.yaml"
    cfg = load_config(path=path)
    # Should return defaults
    assert cfg.watchdog.max_restart_attempts == 3


def test_config_yaml_structure(tmp_path):
    cfg = MaintainerConfig()
    path = tmp_path / "test.yaml"
    save_config(cfg, path=path)

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "watchdog" in data
    assert "monitor" in data
    assert "repair" in data
    assert "notify" in data
