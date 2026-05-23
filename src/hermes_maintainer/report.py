"""Health report generation for Hermes Agent."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import MaintainerConfig
from .diagnose import DiagnosticReport, run_diagnostics
from .monitor import HealthReport, run_health_check


def generate_markdown_report(
    health: HealthReport,
    diagnostics: Optional[DiagnosticReport] = None,
) -> str:
    """Generate a Markdown-formatted health report."""
    lines = []
    lines.append(f"# Hermes Health Report")
    lines.append(f"")
    lines.append(f"**Generated:** {health.timestamp}")
    lines.append(f"**Overall Score:** {health.overall_score}/100")
    lines.append(f"")

    # Score bar
    score = health.overall_score
    if score >= 80:
        grade = "Healthy"
    elif score >= 60:
        grade = "Warning"
    else:
        grade = "Critical"
    lines.append(f"**Status:** {grade}")
    lines.append(f"")

    # Checks table
    lines.append("## Health Checks")
    lines.append("")
    lines.append("| Check | Status | Details |")
    lines.append("|-------|--------|---------|")
    for c in health.checks:
        icon = {"ok": "OK", "warn": "WARN", "error": "ERR", "skip": "SKIP"}.get(c.status, "?")
        lines.append(f"| {c.name} | {icon} | {c.message} |")
    lines.append("")

    # Warnings
    if health.warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in health.warnings:
            lines.append(f"- {w}")
        lines.append("")

    # Errors
    if health.errors:
        lines.append("## Errors")
        lines.append("")
        for e in health.errors:
            lines.append(f"- {e}")
        lines.append("")

    # Diagnostics
    if diagnostics and diagnostics.items:
        lines.append("## Detailed Diagnostics")
        lines.append("")
        lines.append("| Category | Severity | Title | Detail | Fix |")
        lines.append("|----------|----------|-------|--------|-----|")
        for item in diagnostics.items:
            lines.append(f"| {item.category} | {item.severity} | {item.title} | {item.detail} | {item.fix_hint} |")
        lines.append("")

    return "\n".join(lines)


def save_report(
    cfg: MaintainerConfig,
    include_diagnostics: bool = True,
    output_path: Optional[Path] = None,
    as_json: bool = False,
) -> Path:
    """Generate and save a health report."""
    cfg.resolve_paths()
    hermes_home = cfg.hermes_home or Path(".")

    health = run_health_check(cfg)
    diagnostics = run_diagnostics(cfg) if include_diagnostics else None

    if as_json:
        data = health.to_dict()
        if diagnostics:
            data["diagnostics"] = diagnostics.to_dict()
        content = json.dumps(data, indent=2, ensure_ascii=False)
        suffix = ".json"
    else:
        content = generate_markdown_report(health, diagnostics)
        suffix = ".md"

    if output_path is None:
        reports_dir = hermes_home / "reports" / "health"
        reports_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        output_path = reports_dir / f"report_{ts}{suffix}"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return output_path
