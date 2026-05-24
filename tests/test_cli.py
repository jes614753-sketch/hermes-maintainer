"""Tests for CLI safety integration — confirmation prompts, --force, Hermes detection."""

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
                with patch("hermes_maintainer.cli.run_update") as mock_update:
                    mock_update.return_value = MagicMock(
                        status="up-to-date", message="ok", snapshot_path=""
                    )
                    result = runner.invoke(app, ["update", "--check"])
        assert result.exit_code == 0
        mock_confirm.assert_not_called()

    @patch("hermes_maintainer.cli.confirm_action", return_value=True)
    def test_update_hermes_running_refused(self, mock_confirm, tmp_path):
        """update should be refused when Hermes is running (no --force)."""
        with patch("hermes_maintainer.cli.load_config") as mock_load:
            cfg = MagicMock()
            cfg.hermes_home = tmp_path
            cfg.verbose = False
            cfg.resolve_paths = MagicMock()
            mock_load.return_value = cfg
            with patch("hermes_maintainer.cli.run_update") as mock_update:
                from hermes_maintainer.updater import UpdateReport
                mock_update.return_value = UpdateReport(
                    status="failed", message="Hermes is running — stop it first, or use --force to override"
                )
                result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        # Should pass force=False
        mock_update.assert_called_once_with(tmp_path, check_only=False, force=False)

    @patch("hermes_maintainer.cli.confirm_action", return_value=True)
    def test_update_force_overrides(self, mock_confirm, tmp_path):
        """update --force should pass force=True to run_update."""
        with patch("hermes_maintainer.cli.load_config") as mock_load:
            cfg = MagicMock()
            cfg.hermes_home = tmp_path
            cfg.verbose = False
            cfg.resolve_paths = MagicMock()
            mock_load.return_value = cfg
            with patch("hermes_maintainer.cli.run_update") as mock_update:
                mock_update.return_value = MagicMock(
                    status="updated", message="ok", snapshot_path=""
                )
                result = runner.invoke(app, ["update", "--force"])
        assert result.exit_code == 0
        mock_update.assert_called_once_with(tmp_path, check_only=False, force=True)


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
            with patch("hermes_maintainer.cli.rollback") as mock_rb:
                mock_rb.return_value = MagicMock(status="failed", message="No snapshots", snapshot_path="")
                result = runner.invoke(app, ["rollback"])
        assert result.exit_code == 0
        mock_rb.assert_called_once_with(tmp_path, force=False)

    @patch("hermes_maintainer.cli.confirm_action", return_value=True)
    def test_rollback_hermes_running_refused(self, mock_confirm, tmp_path):
        """rollback should be refused when Hermes is running (no --force)."""
        with patch("hermes_maintainer.cli.load_config") as mock_load:
            cfg = MagicMock()
            cfg.hermes_home = tmp_path
            cfg.verbose = False
            cfg.resolve_paths = MagicMock()
            mock_load.return_value = cfg
            with patch("hermes_maintainer.cli.rollback") as mock_rb:
                from hermes_maintainer.updater import UpdateReport
                mock_rb.return_value = UpdateReport(
                    status="failed", message="Hermes is running — stop it first, or use --force to override"
                )
                result = runner.invoke(app, ["rollback"])
        assert result.exit_code == 0
        mock_rb.assert_called_once_with(tmp_path, force=False)

    @patch("hermes_maintainer.cli.confirm_action", return_value=True)
    def test_rollback_force_overrides(self, mock_confirm, tmp_path):
        """rollback --force should pass force=True."""
        with patch("hermes_maintainer.cli.load_config") as mock_load:
            cfg = MagicMock()
            cfg.hermes_home = tmp_path
            cfg.verbose = False
            cfg.resolve_paths = MagicMock()
            mock_load.return_value = cfg
            with patch("hermes_maintainer.cli.rollback") as mock_rb:
                mock_rb.return_value = MagicMock(status="rolled-back", message="ok", snapshot_path="")
                result = runner.invoke(app, ["rollback", "--force"])
        assert result.exit_code == 0
        mock_rb.assert_called_once_with(tmp_path, force=True)
