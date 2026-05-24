"""Safety utilities — single-instance lock, confirmation prompts, snapshots."""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psutil


# ── Single-instance lock ───────────────────────────────────────────────

_LOCK_FILENAME = ".maintainer.lock"


def _lock_path(hermes_home: Path) -> Path:
    return hermes_home / _LOCK_FILENAME


def acquire_lock(hermes_home: Path) -> bool:
    """Try to acquire a single-instance lock. Returns True if acquired."""
    lock = _lock_path(hermes_home)
    if lock.exists():
        try:
            data = json.loads(lock.read_text(encoding="utf-8"))
            pid = data.get("pid")
            if pid and psutil.pid_exists(pid):
                return False  # Another instance is running
        except (json.JSONDecodeError, KeyError):
            pass  # Stale lock, overwrite
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "time": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    return True


def release_lock(hermes_home: Path) -> None:
    """Release the single-instance lock."""
    lock = _lock_path(hermes_home)
    if lock.exists():
        try:
            lock.unlink()
        except OSError:
            pass


@contextmanager
def maintainer_lock(hermes_home: Path):
    """Context manager for single-instance lock."""
    if not acquire_lock(hermes_home):
        raise RuntimeError(
            f"Another hermes-maintainer instance is running (lock: {_lock_path(hermes_home)})"
        )
    try:
        yield
    finally:
        release_lock(hermes_home)


# ── Confirmation prompt ────────────────────────────────────────────────

def confirm_action(action: str, risk: str = "medium") -> bool:
    """Ask user for confirmation before a destructive action."""
    if risk == "low":
        return True
    prompt = f"[{risk.upper()}] {action}. Continue? [y/N] "
    try:
        answer = input(prompt).strip().lower()
        return answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


# ── Process check ──────────────────────────────────────────────────────

def hermes_running() -> bool:
    """Check if any Hermes process is currently running."""
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if "hermes" in cmdline.lower() and "maintainer" not in cmdline.lower():
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def warn_if_hermes_running() -> bool:
    """Warn user if Hermes is running. Returns True if running."""
    if hermes_running():
        print("WARNING: Hermes is currently running.")
        print("  SQLite and config modifications may cause data corruption.")
        print("  Consider stopping Hermes first: close the TUI/gateway process.")
        return True
    return False
