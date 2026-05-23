"""OS-native watchdog for Hermes Agent — Task Scheduler / NSSM integration."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psutil

from .config import MaintainerConfig
from .monitor import run_health_check

logger = logging.getLogger(__name__)


_TASK_NAME = "HermesMaintainerWatchdog"


@dataclass
class WatchdogStatus:
    installed: bool = False
    running: bool = False
    last_check: str = ""
    restart_count: int = 0
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "installed": self.installed,
            "running": self.running,
            "last_check": self.last_check,
            "restart_count": self.restart_count,
            "message": self.message,
        }


# ── Task Scheduler integration (Windows) ───────────────────────────────

def _schtasks_installed() -> bool:
    """Check if the watchdog task is registered in Task Scheduler."""
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", _TASK_NAME],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def install_watchdog(cfg: MaintainerConfig, interval_minutes: int = 5) -> WatchdogStatus:
    """Register watchdog as a Task Scheduler task (Windows)."""
    cfg.resolve_paths()
    status = WatchdogStatus()

    python_exe = sys.executable
    module_cmd = f'"{python_exe}" -m hermes_maintainer watchdog-run'

    try:
        result = subprocess.run(
            [
                "schtasks", "/create",
                "/tn", _TASK_NAME,
                "/tr", module_cmd,
                "/sc", "minute",
                "/mo", str(interval_minutes),
                "/f",  # force overwrite
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            status.installed = True
            status.message = f"Watchdog installed (every {interval_minutes} min)"
        else:
            status.message = f"Install failed: {result.stderr[:200]}"
    except Exception as e:
        status.message = f"Install error: {e}"

    return status


def uninstall_watchdog() -> WatchdogStatus:
    """Remove the watchdog task from Task Scheduler."""
    status = WatchdogStatus()
    try:
        result = subprocess.run(
            ["schtasks", "/delete", "/tn", _TASK_NAME, "/f"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            status.installed = False
            status.message = "Watchdog uninstalled"
        else:
            status.message = f"Uninstall failed: {result.stderr[:200]}"
    except Exception as e:
        status.message = f"Uninstall error: {e}"
    return status


def get_watchdog_status() -> WatchdogStatus:
    """Check if watchdog is currently installed and running."""
    status = WatchdogStatus()
    status.installed = _schtasks_installed()
    if status.installed:
        try:
            result = subprocess.run(
                ["schtasks", "/query", "/tn", _TASK_NAME, "/fo", "LIST", "/v"],
                capture_output=True, text=True, timeout=10,
            )
            if "Running" in result.stdout:
                status.running = True
            status.message = "Task registered"
        except Exception:
            status.message = "Task registered but status unknown"
    else:
        status.message = "Not installed"
    return status


# ── Watchdog run loop ──────────────────────────────────────────────────

def _state_file(hermes_home: Path) -> Path:
    return hermes_home / "watchdog-state.json"


def _load_state(hermes_home: Path) -> dict:
    p = _state_file(hermes_home)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"restart_count": 0, "last_check": "", "last_restart": 0}


def _save_state(hermes_home: Path, state: dict) -> None:
    p = _state_file(hermes_home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def watchdog_run(cfg: MaintainerConfig) -> None:
    """Single watchdog tick — check health, restart if needed."""
    cfg.resolve_paths()
    hermes_home = cfg.hermes_home
    if hermes_home is None:
        logger.error("Hermes home not found, watchdog cannot run")
        return

    state = _load_state(hermes_home)
    state["last_check"] = datetime.now(timezone.utc).isoformat()

    # Check crash-loop protection
    max_attempts = cfg.watchdog.max_restart_attempts
    if state.get("restart_count", 0) >= max_attempts:
        logger.warning("Watchdog: max restart attempts (%d) reached. Stopping.", max_attempts)
        state["message"] = f"Max restart attempts ({max_attempts}) reached"
        _save_state(hermes_home, state)
        return

    # Run health check
    report = run_health_check(cfg, quick=True)

    if report.overall_score >= 80:
        # Healthy — reset restart counter
        state["restart_count"] = 0
        state["message"] = "Healthy"
        _save_state(hermes_home, state)
        return

    # Unhealthy — check if Hermes process exists
    hermes_running = any(
        c.name == "process" and c.status == "ok"
        for c in report.checks
    )

    if not hermes_running and cfg.watchdog.auto_restart:
        # Cooldown check
        last_restart = state.get("last_restart", 0)
        cooldown = cfg.watchdog.restart_cooldown_sec
        if time.time() - last_restart < cooldown:
            logger.info("Watchdog: in cooldown period, skipping restart")
            state["message"] = "In cooldown"
            _save_state(hermes_home, state)
            return

        # Attempt restart
        logger.warning("Watchdog: Hermes not running, attempting restart...")
        success = _try_restart_hermes(hermes_home)
        state["restart_count"] = state.get("restart_count", 0) + 1
        state["last_restart"] = time.time()
        if success:
            state["message"] = f"Restarted (attempt {state['restart_count']})"
        else:
            state["message"] = f"Restart failed (attempt {state['restart_count']})"
    else:
        state["message"] = f"Unhealthy (score {report.overall_score}), but process running"

    _save_state(hermes_home, state)


def _try_restart_hermes(hermes_home: Path) -> bool:
    """Try to restart Hermes via CLI."""
    hermes_cli = hermes_home / "hermes-agent" / "venv" / ("Scripts/hermes.exe" if sys.platform == "win32" else "bin/hermes")
    if not hermes_cli.exists():
        logger.error("Hermes CLI not found at %s", hermes_cli)
        return False
    try:
        # Detached start — don't wait for it
        subprocess.Popen(
            [str(hermes_cli)],
            cwd=str(hermes_home / "hermes-agent"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0,
        )
        return True
    except Exception as e:
        logger.error("Failed to start Hermes: %s", e)
        return False
