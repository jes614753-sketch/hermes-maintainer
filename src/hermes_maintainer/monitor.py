"""Read-only health monitoring for Hermes Agent."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psutil

from .config import MaintainerConfig, discover_hermes_home

logger = logging.getLogger(__name__)


# ── Data models ────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    name: str
    status: str  # "ok", "warn", "error", "skip"
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class HealthReport:
    timestamp: str
    overall_score: int  # 0-100
    checks: list[CheckResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add(self, check: CheckResult) -> None:
        self.checks.append(check)
        if check.status == "warn":
            self.warnings.append(f"[{check.name}] {check.message}")
        elif check.status == "error":
            self.errors.append(f"[{check.name}] {check.message}")

    def compute_score(self) -> int:
        if not self.checks:
            return 0
        weights = {"ok": 100, "warn": 60, "error": 0, "skip": 80}
        total = sum(weights.get(c.status, 50) for c in self.checks)
        self.overall_score = total // len(self.checks)
        return self.overall_score

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "overall_score": self.overall_score,
            "checks": [
                {"name": c.name, "status": c.status, "message": c.message, "details": c.details}
                for c in self.checks
            ],
            "warnings": self.warnings,
            "errors": self.errors,
        }


# ── Individual checks ──────────────────────────────────────────────────

def check_process() -> CheckResult:
    """Check if Hermes processes are running."""
    hermes_pids = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "memory_info", "cpu_percent"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if "hermes" in cmdline.lower() and "maintainer" not in cmdline.lower():
                mem_mb = (proc.info.get("memory_info") or psutil.Process(proc.pid).memory_info()).rss / (1024 * 1024)
                hermes_pids.append({
                    "pid": proc.pid,
                    "memory_mb": round(mem_mb, 1),
                    "cpu_pct": proc.info.get("cpu_percent", 0),
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if not hermes_pids:
        return CheckResult("process", "warn", "No Hermes process found", {"pids": []})
    total_mem = sum(p["memory_mb"] for p in hermes_pids)
    return CheckResult(
        "process", "ok",
        f"{len(hermes_pids)} process(es), {total_mem:.0f} MB total",
        {"pids": hermes_pids, "total_memory_mb": round(total_mem, 1)},
    )


def check_sqlite(hermes_home: Path) -> CheckResult:
    """Read-only SQLite integrity and WAL check."""
    db_path = hermes_home / "state.db"
    if not db_path.exists():
        return CheckResult("sqlite", "skip", "state.db not found", {"path": str(db_path)})

    issues = []
    details: dict = {"path": str(db_path), "size_mb": round(db_path.stat().st_size / (1024 * 1024), 2)}

    # WAL file size
    wal_path = db_path.with_suffix(".db-wal")
    if wal_path.exists():
        wal_mb = wal_path.stat().st_size / (1024 * 1024)
        details["wal_size_mb"] = round(wal_mb, 2)
        if wal_mb > 100:
            issues.append(f"WAL file is {wal_mb:.0f} MB (needs checkpoint)")

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        conn.execute("PRAGMA busy_timeout=3000")
        # Integrity check (limited)
        try:
            result = conn.execute("PRAGMA integrity_check(10)").fetchall()
            bad = [r for r in result if r[0] != "ok"]
            if bad:
                issues.append(f"Integrity issues: {len(bad)}")
                details["integrity_errors"] = [r[0] for r in bad[:5]]
        except sqlite3.DatabaseError as e:
            issues.append(f"Integrity check failed: {e}")

        # Session count
        try:
            count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            details["session_count"] = count
            if count > 5000:
                issues.append(f"Large session count: {count}")
        except sqlite3.OperationalError:
            pass  # table may not exist

        # FTS5 check
        try:
            fts_tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%fts%'"
            ).fetchall()
            details["fts_tables"] = [t[0] for t in fts_tables]
        except sqlite3.OperationalError:
            pass

        conn.close()
    except sqlite3.DatabaseError as e:
        issues.append(f"Cannot open database: {e}")

    if issues:
        return CheckResult("sqlite", "warn", "; ".join(issues), details)
    return CheckResult("sqlite", "ok", f"Healthy, {details.get('session_count', '?')} sessions", details)


def check_api_connectivity(hermes_home: Path, timeout: int = 10) -> CheckResult:
    """Check if configured LLM API endpoints are reachable (lightweight ping)."""
    # Try to read provider config from .env
    env_path = hermes_home / ".env"
    if not env_path.exists():
        return CheckResult("api", "skip", "No .env file found", {})

    import httpx

    endpoints = {
        "openai": "https://api.openai.com/v1/models",
        "openrouter": "https://openrouter.ai/api/v1/models",
    }

    results = {}
    for name, url in endpoints.items():
        try:
            resp = httpx.get(url, timeout=timeout, follow_redirects=True)
            results[name] = {"status": resp.status_code, "latency_ms": int(resp.elapsed.total_seconds() * 1000)}
        except httpx.TimeoutException:
            results[name] = {"status": "timeout", "latency_ms": timeout * 1000}
        except httpx.ConnectError as e:
            results[name] = {"status": "connect_error", "error": str(e)[:100]}

    reachable = sum(1 for r in results.values() if isinstance(r.get("status"), int) and r["status"] < 500)
    total = len(results)

    if reachable == 0:
        return CheckResult("api", "error", "No API endpoints reachable", results)
    if reachable < total:
        return CheckResult("api", "warn", f"{reachable}/{total} endpoints reachable", results)
    return CheckResult("api", "ok", f"{reachable}/{total} endpoints reachable", results)


def check_disk(hermes_home: Path, warn_pct: float = 90.0) -> CheckResult:
    """Check disk space usage."""
    try:
        usage = psutil.disk_usage(str(hermes_home))
        pct = usage.percent
        details = {
            "total_gb": round(usage.total / (1024**3), 1),
            "used_gb": round(usage.used / (1024**3), 1),
            "free_gb": round(usage.free / (1024**3), 1),
            "percent": pct,
        }
        if pct >= warn_pct:
            return CheckResult("disk", "warn", f"Disk {pct}% used ({details['free_gb']} GB free)", details)
        return CheckResult("disk", "ok", f"Disk {pct}% used ({details['free_gb']} GB free)", details)
    except Exception as e:
        return CheckResult("disk", "error", str(e))


def check_logs(hermes_home: Path, max_lines: int = 100) -> CheckResult:
    """Scan recent log lines for errors."""
    log_paths = [
        hermes_home / "hermes.log",
        hermes_home / "logs" / "hermes.log",
    ]
    log_path = None
    for p in log_paths:
        if p.exists():
            log_path = p
            break
    if log_path is None:
        return CheckResult("logs", "skip", "No log file found", {})

    error_patterns = ["ERROR", "CRITICAL", "Traceback", "Exception", "FAILED"]
    errors_found = []
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        recent = lines[-max_lines:]
        for i, line in enumerate(recent):
            for pat in error_patterns:
                if pat in line:
                    errors_found.append(line.strip()[:200])
                    break
    except Exception as e:
        return CheckResult("logs", "warn", f"Cannot read log: {e}", {})

    details = {"log_path": str(log_path), "lines_scanned": min(max_lines, len(lines)), "errors_found": len(errors_found)}
    if errors_found:
        details["recent_errors"] = errors_found[-5:]
        return CheckResult("logs", "warn", f"{len(errors_found)} error(s) in last {max_lines} lines", details)
    return CheckResult("logs", "ok", f"No errors in last {max_lines} lines", details)


def check_config(hermes_home: Path) -> CheckResult:
    """Validate config files exist and have no obvious issues."""
    issues = []
    details: dict = {}

    env_path = hermes_home / ".env"
    if not env_path.exists():
        issues.append(".env file missing")
    else:
        content = env_path.read_text(encoding="utf-8", errors="replace")
        if "no-key-required" in content:
            issues.append("API key is placeholder 'no-key-required'")
        if "YOUR_KEY_HERE" in content:
            issues.append("API key contains 'YOUR_KEY_HERE' placeholder")
        details["env_lines"] = len(content.splitlines())

    config_path = hermes_home / "config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path, encoding="utf-8") as f:
                yaml.safe_load(f)
            details["config_valid"] = True
        except Exception as e:
            issues.append(f"config.yaml parse error: {e}")
            details["config_valid"] = False

    if issues:
        return CheckResult("config", "warn", "; ".join(issues), details)
    return CheckResult("config", "ok", "Config files valid", details)


# ── Main entry ─────────────────────────────────────────────────────────

def run_health_check(cfg: MaintainerConfig, quick: bool = False) -> HealthReport:
    """Run all health checks and return a report."""
    cfg.resolve_paths()
    hermes_home = cfg.hermes_home
    if hermes_home is None:
        report = HealthReport(timestamp=datetime.now(timezone.utc).isoformat(), overall_score=0)
        report.add(CheckResult("config", "error", "Hermes home directory not found"))
        return report

    report = HealthReport(timestamp=datetime.now(timezone.utc).isoformat(), overall_score=0)

    report.add(check_process())
    report.add(check_sqlite(hermes_home))
    report.add(check_config(hermes_home))
    report.add(check_disk(hermes_home, cfg.monitor.disk_warn_pct))

    if not quick:
        report.add(check_api_connectivity(hermes_home, cfg.monitor.api_timeout_sec))
        report.add(check_logs(hermes_home))

    report.compute_score()
    return report
