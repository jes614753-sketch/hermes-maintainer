"""Version management and safe updates for Hermes Agent."""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import MaintainerConfig
from .safety import hermes_running, maintainer_lock

logger = logging.getLogger(__name__)


@dataclass
class SnapshotInfo:
    path: Path
    version: str
    timestamp: str
    reason: str


@dataclass
class UpdateReport:
    current_version: str = ""
    latest_version: str = ""
    update_available: bool = False
    snapshot_path: str = ""
    status: str = "unknown"  # "up-to-date", "updated", "failed", "rolled-back"
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "update_available": self.update_available,
            "snapshot_path": self.snapshot_path,
            "status": self.status,
            "message": self.message,
        }


# ── Version detection ──────────────────────────────────────────────────

def get_current_version(hermes_home: Path) -> str:
    """Read current Hermes version from pyproject.toml."""
    pyproject = hermes_home / "hermes-agent" / "pyproject.toml"
    if not pyproject.exists():
        return "unknown"
    try:
        content = pyproject.read_text(encoding="utf-8")
        for line in content.splitlines():
            if line.strip().startswith("version"):
                return line.split("=")[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "unknown"


def get_latest_version_from_github() -> Optional[str]:
    """Fetch latest release version from GitHub API."""
    import httpx
    try:
        resp = httpx.get(
            "https://api.github.com/repos/NousResearch/hermes-agent/releases/latest",
            timeout=15,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            tag = resp.json().get("tag_name", "")
            return tag.lstrip("v")
    except Exception as e:
        logger.warning("Failed to fetch latest version: %s", e)
    return None


def check_for_update(hermes_home: Path) -> UpdateReport:
    """Check if an update is available."""
    report = UpdateReport()
    report.current_version = get_current_version(hermes_home)
    latest = get_latest_version_from_github()
    if latest is None:
        report.status = "unknown"
        report.message = "Could not fetch latest version from GitHub"
        return report
    report.latest_version = latest
    if report.current_version == latest:
        report.update_available = False
        report.status = "up-to-date"
        report.message = f"Already on latest version {latest}"
    else:
        report.update_available = True
        report.status = "update-available"
        report.message = f"Update available: {report.current_version} -> {latest}"
    return report


# ── Snapshot management ────────────────────────────────────────────────

_SNAPSHOT_DIR = "maintainer-snapshots"


def snapshots_dir(hermes_home: Path) -> Path:
    return hermes_home / _SNAPSHOT_DIR


def create_snapshot(hermes_home: Path, reason: str = "pre-update") -> SnapshotInfo:
    """Create a backup of key Hermes files before update.

    Uses the SQLite backup API for ``state.db`` so the copy is consistent
    even when Hermes is running.  Raises ``RuntimeError`` if Hermes is
    running and ``state.db`` cannot be safely backed up.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    version = get_current_version(hermes_home)
    snap_dir = snapshots_dir(hermes_home) / ts
    snap_dir.mkdir(parents=True, exist_ok=True)

    # Backup key files
    files_to_backup = [
        ("config.yaml", hermes_home / "config.yaml"),
        (".env", hermes_home / ".env"),
        ("auth.json", hermes_home / "auth.json"),
    ]
    for name, src in files_to_backup:
        if src.exists():
            shutil.copy2(src, snap_dir / name)

    # Backup state.db using SQLite backup API for consistency
    db_path = hermes_home / "state.db"
    if db_path.exists():
        _backup_sqlite(db_path, snap_dir / "state.db")

    # Backup skills directory
    skills_dir = hermes_home / "skills"
    if skills_dir.is_dir():
        shutil.copytree(skills_dir, snap_dir / "skills", dirs_exist_ok=True)

    # Write metadata
    meta = {"version": version, "timestamp": ts, "reason": reason, "hermes_home": str(hermes_home)}
    (snap_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return SnapshotInfo(path=snap_dir, version=version, timestamp=ts, reason=reason)


def _backup_sqlite(src: Path, dst: Path) -> None:
    """Copy a SQLite database safely using the backup API.

    Raises ``RuntimeError`` if the backup API fails, rather than falling
    back to an inconsistent raw file copy.
    """
    src_conn = sqlite3.connect(str(src), timeout=5)
    dst_conn = sqlite3.connect(str(dst), timeout=5)
    try:
        src_conn.backup(dst_conn)
    except sqlite3.DatabaseError as e:
        raise RuntimeError(f"SQLite backup failed for {src}: {e}") from e
    finally:
        dst_conn.close()
        src_conn.close()


def list_snapshots(hermes_home: Path) -> list[SnapshotInfo]:
    """List available snapshots."""
    snap_dir = snapshots_dir(hermes_home)
    if not snap_dir.is_dir():
        return []
    snapshots = []
    for d in sorted(snap_dir.iterdir(), reverse=True):
        meta_file = d / "metadata.json"
        if meta_file.exists():
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            snapshots.append(SnapshotInfo(
                path=d,
                version=meta.get("version", "unknown"),
                timestamp=meta.get("timestamp", d.name),
                reason=meta.get("reason", ""),
            ))
    return snapshots


def rollback(hermes_home: Path, snapshot: Optional[SnapshotInfo] = None, *, force: bool = False) -> UpdateReport:
    """Restore from a snapshot.

    Acquires ``maintainer_lock``, refuses if Hermes is running (unless
    ``force=True``), and creates a ``pre-rollback`` snapshot before
    overwriting anything.  Aborts if the pre-rollback snapshot fails.
    """
    report = UpdateReport()
    if hermes_running() and not force:
        report.status = "failed"
        report.message = "Hermes is running — stop it first, or use --force to override"
        return report
    try:
        with maintainer_lock(hermes_home):
            return _rollback_inner(hermes_home, snapshot, report)
    except RuntimeError as e:
        report.status = "failed"
        report.message = str(e)
        return report


def _rollback_inner(
    hermes_home: Path,
    snapshot: Optional[SnapshotInfo],
    report: UpdateReport,
) -> UpdateReport:
    """Actual rollback execution (called inside maintainer_lock)."""
    if snapshot is None:
        snapshots = list_snapshots(hermes_home)
        if not snapshots:
            report.status = "failed"
            report.message = "No snapshots available"
            return report
        snapshot = snapshots[0]

    report.snapshot_path = str(snapshot.path)
    report.current_version = get_current_version(hermes_home)
    report.latest_version = snapshot.version

    # Safety: snapshot current state before overwriting — MUST succeed
    try:
        create_snapshot(hermes_home, reason="pre-rollback")
    except Exception as e:
        report.status = "failed"
        report.message = f"Pre-rollback snapshot failed, aborting to prevent data loss: {e}"
        return report

    # Restore files
    files_to_restore = ["config.yaml", ".env", "auth.json"]
    for name in files_to_restore:
        src = snapshot.path / name
        if src.exists():
            shutil.copy2(src, hermes_home / name)

    # Restore state.db via backup API for safety
    src_db = snapshot.path / "state.db"
    if src_db.exists():
        _backup_sqlite(src_db, hermes_home / "state.db")

    # Restore skills
    src_skills = snapshot.path / "skills"
    if src_skills.is_dir():
        dst_skills = hermes_home / "skills"
        if dst_skills.exists():
            shutil.rmtree(dst_skills)
        shutil.copytree(src_skills, dst_skills)

    report.status = "rolled-back"
    report.message = f"Rolled back to version {snapshot.version} from {snapshot.timestamp}"
    return report


# ── Update execution ───────────────────────────────────────────────────

def run_update(hermes_home: Path, check_only: bool = False, *, force: bool = False) -> UpdateReport:
    """Check for updates and optionally execute.

    When ``check_only=False``, acquires ``maintainer_lock`` and refuses
    if Hermes is running (unless ``force=True``).
    """
    report = check_for_update(hermes_home)

    if check_only or not report.update_available:
        return report

    # Safety: refuse if Hermes running, then lock
    if hermes_running() and not force:
        report.status = "failed"
        report.message = "Hermes is running — stop it first, or use --force to override"
        return report
    try:
        with maintainer_lock(hermes_home):
            return _run_update_inner(hermes_home, report)
    except RuntimeError as e:
        report.status = "failed"
        report.message = str(e)
        return report


def _run_update_inner(hermes_home: Path, report: UpdateReport) -> UpdateReport:
    """Actual update execution (called inside maintainer_lock)."""
    # Create snapshot before updating
    try:
        snapshot = create_snapshot(hermes_home, reason="pre-update")
        report.snapshot_path = str(snapshot.path)
    except Exception as e:
        report.status = "failed"
        report.message = f"Failed to create snapshot: {e}"
        return report

    # Run hermes update
    venv_python = hermes_home / "hermes-agent" / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    hermes_cli = hermes_home / "hermes-agent" / "venv" / ("Scripts/hermes.exe" if sys.platform == "win32" else "bin/hermes")

    if not hermes_cli.exists():
        report.status = "failed"
        report.message = f"hermes CLI not found at {hermes_cli}"
        return report

    try:
        result = subprocess.run(
            [str(hermes_cli), "update"],
            capture_output=True, text=True, timeout=300,
            cwd=str(hermes_home / "hermes-agent"),
        )
        if result.returncode == 0:
            report.status = "updated"
            report.current_version = report.latest_version
            report.message = f"Updated to {report.latest_version}"
        else:
            report.status = "failed"
            report.message = f"Update failed (exit {result.returncode}): {result.stderr[:500]}"
    except subprocess.TimeoutExpired:
        report.status = "failed"
        report.message = "Update timed out after 5 minutes"
    except Exception as e:
        report.status = "failed"
        report.message = f"Update error: {e}"

    return report
