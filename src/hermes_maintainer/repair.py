"""Safe auto-repair for Hermes Agent — dry-run first, confirm for high-risk."""

from __future__ import annotations

import logging
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import MaintainerConfig

logger = logging.getLogger(__name__)


# ── Data models ────────────────────────────────────────────────────────

@dataclass
class RepairAction:
    name: str
    risk: str  # "low", "medium", "high"
    description: str
    status: str = "pending"  # "pending", "done", "skipped", "failed"
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "risk": self.risk,
            "description": self.description,
            "status": self.status,
            "message": self.message,
        }


@dataclass
class RepairReport:
    dry_run: bool
    actions: list[RepairAction] = field(default_factory=list)

    def add(self, action: RepairAction) -> None:
        self.actions.append(action)

    def to_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "summary": {
                "total": len(self.actions),
                "done": sum(1 for a in self.actions if a.status == "done"),
                "skipped": sum(1 for a in self.actions if a.status == "skipped"),
                "failed": sum(1 for a in self.actions if a.status == "failed"),
            },
            "actions": [a.to_dict() for a in self.actions],
        }


# ── Repair functions ───────────────────────────────────────────────────

def clean_pycache(hermes_home: Path, dry_run: bool) -> RepairAction:
    """Clean __pycache__ and .pyc files."""
    action = RepairAction("clean_pycache", "low", "Remove __pycache__ and .pyc files")
    pycache_dirs = list(hermes_home.rglob("__pycache__"))
    pyc_files = list(hermes_home.rglob("*.pyc"))
    total = len(pycache_dirs) + len(pyc_files)
    if total == 0:
        action.status = "done"
        action.message = "Nothing to clean"
        return action
    if dry_run:
        action.status = "skipped"
        action.message = f"Would remove {len(pycache_dirs)} __pycache__ dirs, {len(pyc_files)} .pyc files"
        return action
    for d in pycache_dirs:
        shutil.rmtree(d, ignore_errors=True)
    for f in pyc_files:
        f.unlink(missing_ok=True)
    action.status = "done"
    action.message = f"Removed {len(pycache_dirs)} dirs, {len(pyc_files)} files"
    return action


def clean_logs(hermes_home: Path, dry_run: bool, max_size_mb: float = 100) -> RepairAction:
    """Archive oversized log files."""
    action = RepairAction("clean_logs", "low", "Archive log files larger than limit")
    log_paths = [
        hermes_home / "hermes.log",
        hermes_home / "logs" / "hermes.log",
    ]
    for log_path in log_paths:
        if not log_path.exists():
            continue
        size_mb = log_path.stat().st_size / (1024 * 1024)
        if size_mb <= max_size_mb:
            action.status = "done"
            action.message = f"{log_path.name} is {size_mb:.1f} MB (under {max_size_mb} MB limit)"
            return action
        if dry_run:
            action.status = "skipped"
            action.message = f"Would archive {log_path.name} ({size_mb:.1f} MB)"
            return action
        archive = log_path.with_suffix(f".{int(time.time())}.log.bak")
        log_path.rename(archive)
        action.status = "done"
        action.message = f"Archived {log_path.name} to {archive.name}"
        return action
    action.status = "done"
    action.message = "No log files found"
    return action


def sqlite_checkpoint(hermes_home: Path, dry_run: bool) -> RepairAction:
    """Run WAL checkpoint to reduce WAL file size."""
    action = RepairAction("sqlite_checkpoint", "medium", "WAL checkpoint to reduce WAL size")
    db_path = hermes_home / "state.db"
    if not db_path.exists():
        action.status = "done"
        action.message = "state.db not found"
        return action
    wal_path = db_path.with_suffix(".db-wal")
    if not wal_path.exists():
        action.status = "done"
        action.message = "No WAL file to checkpoint"
        return action
    wal_mb = wal_path.stat().st_size / (1024 * 1024)
    if wal_mb < 10:
        action.status = "done"
        action.message = f"WAL is {wal_mb:.1f} MB, no checkpoint needed"
        return action
    if dry_run:
        action.status = "skipped"
        action.message = f"Would checkpoint WAL ({wal_mb:.1f} MB)"
        return action
    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        new_size = wal_path.stat().st_size / (1024 * 1024) if wal_path.exists() else 0
        action.status = "done"
        action.message = f"WAL checkpoint done: {wal_mb:.1f} MB -> {new_size:.1f} MB"
    except sqlite3.DatabaseError as e:
        action.status = "failed"
        action.message = f"Checkpoint failed: {e}"
    return action


def verify_deps(hermes_home: Path, dry_run: bool) -> RepairAction:
    """Verify dependency consistency."""
    action = RepairAction("verify_deps", "medium", "Check dependency consistency")
    agent_root = hermes_home / "hermes-agent"
    venv_python = agent_root / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not venv_python.exists():
        action.status = "failed"
        action.message = "venv python not found"
        return action
    if dry_run:
        action.status = "skipped"
        action.message = "Would run pip check"
        return action
    try:
        result = subprocess.run(
            [str(venv_python), "-m", "pip", "check"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            action.status = "done"
            action.message = "All dependencies OK"
        else:
            action.status = "failed"
            action.message = result.stdout[:500]
    except Exception as e:
        action.status = "failed"
        action.message = str(e)
    return action


def clean_node_cache(hermes_home: Path, dry_run: bool) -> RepairAction:
    """Clean node_modules/.cache directories."""
    action = RepairAction("clean_node_cache", "low", "Remove node_modules/.cache")
    cache_dirs = list(hermes_home.rglob("node_modules/.cache"))
    if not cache_dirs:
        action.status = "done"
        action.message = "No .cache directories found"
        return action
    total_size = sum(d.stat().st_size for d in cache_dirs if d.is_dir())
    if dry_run:
        action.status = "skipped"
        action.message = f"Would remove {len(cache_dirs)} .cache dirs ({total_size // 1024} KB)"
        return action
    for d in cache_dirs:
        shutil.rmtree(d, ignore_errors=True)
    action.status = "done"
    action.message = f"Removed {len(cache_dirs)} .cache dirs"
    return action


# ── Main entry ─────────────────────────────────────────────────────────

# Registry of all repairs by target
REPAIR_REGISTRY: dict[str, tuple[str, str, callable]] = {
    # target -> (risk, description, function)
    "cache": ("low", "Clean caches", None),  # composite
    "pycache": ("low", "Clean __pycache__", clean_pycache),
    "node_cache": ("low", "Clean node_modules/.cache", clean_node_cache),
    "logs": ("low", "Archive large logs", clean_logs),
    "sqlite": ("medium", "SQLite WAL checkpoint", sqlite_checkpoint),
    "deps": ("medium", "Verify dependencies", verify_deps),
}


def run_repairs(
    cfg: MaintainerConfig,
    target: Optional[str] = None,
    dry_run: bool = True,
) -> RepairReport:
    """Run repairs. Default is dry-run."""
    cfg.resolve_paths()
    report = RepairReport(dry_run=dry_run)
    hermes_home = cfg.hermes_home
    if hermes_home is None:
        report.add(RepairAction("all", "high", "Hermes home not found", "failed"))
        return report

    if target == "cache":
        targets = ["pycache", "node_cache"]
    elif target:
        targets = [target]
    else:
        targets = ["pycache", "node_cache", "logs", "sqlite", "deps"]

    for t in targets:
        entry = REPAIR_REGISTRY.get(t)
        if entry is None:
            report.add(RepairAction(t, "low", f"Unknown repair target: {t}", "skipped"))
            continue
        risk, desc, fn = entry
        if fn is None:
            continue
        action = fn(hermes_home, dry_run)
        report.add(action)

    return report
