"""Tests for CLI safety integration — confirmation prompts at CLI boundary."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner

import pytest

from hermes_maintainer.cli import app

runner = CliRunner()


class TestRepairSafety:
    @patch("hermes_maintainer.cli.confirm_action", return_value=False)
    def test_repair_execute_cancelled(self, mock_confirm, tmp_path):
        """repair --execute should be cancellable via confirmation prompt."""
        with patch("hermes_maintainer.cli.load_config") as mock_load:
            cfg = MagicMock()
            cfg.hermes_home = tmp_path
            cfg.verbose = False
            cfg.resolve_paths = MagicMock()
            mock_load.return_value = cfg
            result = runner.invoke(app, ["repair", "--execute"])
        assert result.exit_code == 0
        mock_confirm.assert_called()

    @patch("hermes_maintainer.cli.confirm_action", return_value=True)
    def test_repair_execute_proceeds(self, mock_confirm, tmp_path):
        """repair --execute should proceed when confirmed."""
        with patch("hermes_maintainer.cli.load_config") as mock_load:
            cfg = MagicMock()
            cfg.hermes_home = tmp_path
            cfg.verbose = False
            cfg.resolve_paths = MagicMock()
            mock_load.return_value = cfg
            result = runner.invoke(app, ["repair", "--execute"])
        assert result.exit_code == 0
        mock_confirm.assert_called()

    def test_repair_dry_run_no_confirm(self, tmp_path):
        """repair (dry-run) should NOT ask for confirmation."""
        with patch("hermes_maintainer.cli.load_config") as mock_load:
            cfg = MagicMock()
            cfg.hermes_home = tmp_path
            cfg.verbose = False
            cfg.resolve_paths = MagicMock()
            mock_load.return_value = cfg
            with patch("hermes_maintainer.cli.confirm_action") as mock_confirm:
                result = runner.invoke(app, ["repair"])
        assert result.exit_code == 0
        mock_confirm.assert_not_called()


class TestWatchdogSafety:
    @patch("hermes_maintainer.cli.confirm_action", return_value=False)
    def test_watchdog_install_cancelled(self, mock_confirm):
        """watchdog install should be cancellable."""
        result = runner.invoke(app, ["watchdog", "install"])
        assert result.exit_code == 0

    @patch("hermes_maintainer.cli.confirm_action", return_value=False)
    def test_watchdog_uninstall_cancelled(self, mock_confirm):
        """watchdog uninstall should be cancellable."""
        result = runner.invoke(app, ["watchdog", "uninstall"])
        assert result.exit_code == 0


class TestUpdateSafety:
    @patch("hermes_maintainer.cli.confirm_action", return_value=False)
    def test_update_cancelled(self, mock_confirm, tmp_path):
        """update should be cancellable via confirmation prompt."""
        with patch("hermes_maintainer.cli.load_config") as mock_load:
            cfg = MagicMock()
            cfg.hermes_home = tmp_path
            cfg.verbose = False
            cfg.resolve_paths = MagicMock()
            mock_load.return_value = cfg
            result = runner.invoke(app, ["update"])
        assert result.exit_code == 0

    def test_update_check_no_confirm(self, tmp_path):
        """update --check should NOT ask for confirmation."""
        with patch("hermes_maintainer.cli.load_config") as mock_load:
            cfg = MagicMock()
            cfg.hermes_home = tmp_path
            cfg.verbose = False
            cfg.resolve_paths = MagicMock()
            mock_load.return_value = cfg
            with patch("hermes_maintainer.cli.confirm_action") as mock_confirm:
                with patch("hermes_maintainer.updater.check_for_update") as mock_check:
                    mock_check.return_value = MagicMock(
                        status="up-to-date", message="ok", update_available=False
                    )
                    result = runner.invoke(app, ["update", "--check"])
        assert result.exit_code == 0
        mock_confirm.assert_not_called()


class TestRollbackSafety:
    @patch("hermes_maintainer.cli.confirm_action", return_value=False)
    def test_rollback_cancelled(self, mock_confirm, tmp_path):
        """rollback should be cancellable via confirmation prompt."""
        with patch("hermes_maintainer.cli.load_config") as mock_load:
            cfg = MagicMock()
            cfg.hermes_home = tmp_path
            cfg.verbose = False
            cfg.resolve_paths = MagicMock()
            mock_load.return_value = cfg
            result = runner.invoke(app, ["rollback"])
        assert result.exit_code == 0

    @patch("hermes_maintainer.cli.confirm_action", return_value=True)
    def test_rollback_proceeds(self, mock_confirm, tmp_path):
        """rollback should proceed when confirmed."""
        with patch("hermes_maintainer.cli.load_config") as mock_load:
            cfg = MagicMock()
            cfg.hermes_home = tmp_path
            cfg.verbose = False
            cfg.resolve_paths = MagicMock()
            mock_load.return_value = cfg
            with patch("hermes_maintainer.updater.rollback") as mock_rb:
                mock_rb.return_value = MagicMock(status="failed", message="No snapshots", snapshot_path="")
                result = runner.invoke(app, ["rollback"])
        assert result.exit_code == 0
