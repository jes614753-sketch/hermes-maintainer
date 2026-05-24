"""CLI entry point for hermes-maintainer."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .config import MaintainerConfig, load_config, save_config, discover_hermes_home
from .diagnose import run_diagnostics
from .safety import confirm_action, maintainer_lock, warn_if_hermes_running
from .monitor import run_health_check
from .report import save_report, generate_markdown_report
from .repair import run_repairs
from .updater import check_for_update, run_update, rollback, list_snapshots
from .watchdog import install_watchdog, uninstall_watchdog, get_watchdog_status, watchdog_run

app = typer.Typer(
    name="hermes-maintainer",
    help="Hermes Agent lifecycle manager — monitor, diagnose, repair, update",
    no_args_is_help=True,
)
console = Console()


def _load_cfg(verbose: bool = False) -> MaintainerConfig:
    cfg = load_config()
    cfg.verbose = verbose
    cfg.resolve_paths()
    return cfg


# ── status ─────────────────────────────────────────────────────────────

@app.command()
def status(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Quick health overview."""
    cfg = _load_cfg(verbose)
    report = run_health_check(cfg, quick=True)

    console.print(f"\n[bold]Hermes Health: {report.overall_score}/100[/bold]\n")

    table = Table(show_header=True)
    table.add_column("Check", style="cyan")
    table.add_column("Status")
    table.add_column("Details")

    for c in report.checks:
        style = {"ok": "green", "warn": "yellow", "error": "red", "skip": "dim"}.get(c.status, "")
        table.add_row(c.name, f"[{style}]{c.status}[/{style}]", c.message)
    console.print(table)

    if report.warnings:
        console.print("\n[yellow]Warnings:[/yellow]")
        for w in report.warnings:
            console.print(f"  ! {w}")
    if report.errors:
        console.print("\n[red]Errors:[/red]")
        for e in report.errors:
            console.print(f"  x {e}")
    console.print()


# ── monitor ────────────────────────────────────────────────────────────

@app.command()
def monitor(
    once: bool = typer.Option(False, "--once", help="Single check then exit"),
    interval: int = typer.Option(300, "--interval", "-i", help="Check interval in seconds"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Health monitoring (continuous or one-shot)."""
    cfg = _load_cfg(verbose)

    if once:
        report = run_health_check(cfg)
        console.print_json(json.dumps(report.to_dict(), ensure_ascii=False))
        raise typer.Exit(code=1 if report.overall_score < 60 else 0)

    console.print(f"[dim]Monitoring every {interval}s. Ctrl+C to stop.[/dim]")
    try:
        while True:
            report = run_health_check(cfg)
            icon = "OK" if report.overall_score >= 80 else "WARN" if report.overall_score >= 60 else "ERR"
            console.print(f"[{icon}] Score: {report.overall_score}/100 | Warnings: {len(report.warnings)} | Errors: {len(report.errors)}")
            import time
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")


# ── diagnose ───────────────────────────────────────────────────────────

@app.command()
def diagnose(
    focus: Optional[str] = typer.Option(None, help="Focus area: env, db, net, api, deps"),
    symptoms: str = typer.Option("", help="Symptom description for known-issue matching"),
    json_output: bool = typer.Option(False, "--json", "-j"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Deep diagnostics."""
    cfg = _load_cfg(verbose)
    report = run_diagnostics(cfg, focus=focus, symptoms=symptoms)

    if json_output:
        console.print_json(json.dumps(report.to_dict(), ensure_ascii=False))
        return

    console.print(f"\n[bold]Diagnostics: {report.error_count} errors, {report.warn_count} warnings[/bold]\n")

    table = Table(show_header=True)
    table.add_column("Category", style="cyan")
    table.add_column("Severity")
    table.add_column("Title")
    table.add_column("Detail", max_width=50)
    table.add_column("Fix", max_width=40)

    for item in report.items:
        style = {"info": "dim", "warn": "yellow", "error": "red"}.get(item.severity, "")
        table.add_row(
            item.category,
            f"[{style}]{item.severity}[/{style}]",
            item.title,
            item.detail[:50],
            item.fix_hint[:40],
        )
    console.print(table)
    console.print()


# ── repair ─────────────────────────────────────────────────────────────

@app.command()
def repair(
    target: Optional[str] = typer.Option(None, help="Target: cache, pycache, logs, sqlite, deps"),
    dry_run: bool = typer.Option(True, "--dry-run/--execute", help="Dry-run (default) or execute"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Auto-repair common issues (dry-run by default)."""
    cfg = _load_cfg(verbose)

    report = run_repairs(cfg, target=target, dry_run=dry_run, confirm_fn=confirm_action)

    label = "DRY RUN" if report.dry_run else "EXECUTED"
    console.print(f"\n[bold]Repair Report ({label})[/bold]\n")

    table = Table(show_header=True)
    table.add_column("Action", style="cyan")
    table.add_column("Risk")
    table.add_column("Status")
    table.add_column("Message")

    for a in report.actions:
        risk_style = {"low": "green", "medium": "yellow", "high": "red"}.get(a.risk, "")
        status_style = {"done": "green", "skipped": "dim", "failed": "red"}.get(a.status, "")
        table.add_row(
            a.name,
            f"[{risk_style}]{a.risk}[/{risk_style}]",
            f"[{status_style}]{a.status}[/{status_style}]",
            a.message,
        )
    console.print(table)
    console.print()


# ── update ─────────────────────────────────────────────────────────────

@app.command("update")
def update_cmd(
    check: bool = typer.Option(False, "--check", help="Only check, don't update"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Check for updates and optionally update Hermes."""
    cfg = _load_cfg(verbose)
    if cfg.hermes_home is None:
        console.print("[red]Hermes home not found[/red]")
        raise typer.Exit(1)

    if not check:
        # Safety: confirm + lock for actual update
        if not confirm_action("Update Hermes (creates snapshot first)", risk="high"):
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(0)
        warn_if_hermes_running()
        try:
            with maintainer_lock(cfg.hermes_home):
                report = run_update(cfg.hermes_home, check_only=False)
        except RuntimeError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
    else:
        report = run_update(cfg.hermes_home, check_only=True)
    console.print(f"\n[bold]{report.message}[/bold]")
    if report.snapshot_path:
        console.print(f"[dim]Snapshot: {report.snapshot_path}[/dim]")
    console.print()


@app.command("rollback")
def rollback_cmd(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Rollback to last snapshot."""
    cfg = _load_cfg(verbose)
    if cfg.hermes_home is None:
        console.print("[red]Hermes home not found[/red]")
        raise typer.Exit(1)

    # Safety: confirm + lock
    if not confirm_action("Rollback to last snapshot (overwrites current config/db/skills)", risk="high"):
        console.print("[yellow]Cancelled.[/yellow]")
        raise typer.Exit(0)
    warn_if_hermes_running()
    try:
        with maintainer_lock(cfg.hermes_home):
            report = rollback(cfg.hermes_home)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"\n[bold]{report.message}[/bold]")
    console.print()


@app.command()
def snapshots(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """List available snapshots."""
    cfg = _load_cfg(verbose)
    if cfg.hermes_home is None:
        console.print("[red]Hermes home not found[/red]")
        raise typer.Exit(1)

    snaps = list_snapshots(cfg.hermes_home)
    if not snaps:
        console.print("[dim]No snapshots found.[/dim]")
        return

    table = Table(show_header=True)
    table.add_column("Timestamp", style="cyan")
    table.add_column("Version")
    table.add_column("Reason")
    table.add_column("Path", style="dim")
    for s in snaps:
        table.add_row(s.timestamp, s.version, s.reason, str(s.path))
    console.print(table)
    console.print()


# ── watchdog ───────────────────────────────────────────────────────────

watchdog_app = typer.Typer(help="Watchdog daemon management")
app.add_typer(watchdog_app, name="watchdog")


@watchdog_app.command("install")
def watchdog_install(
    interval: int = typer.Option(5, "--interval", "-i", help="Check interval in minutes"),
):
    """Register watchdog as a scheduled task."""
    if not confirm_action("Install watchdog (Windows Task Scheduler)", risk="medium"):
        console.print("[yellow]Cancelled.[/yellow]")
        raise typer.Exit(0)
    cfg = _load_cfg()
    result = install_watchdog(cfg, interval_minutes=interval)
    console.print(f"[bold]{result.message}[/bold]")


@watchdog_app.command("uninstall")
def watchdog_uninstall_cmd():
    """Remove the watchdog task."""
    if not confirm_action("Uninstall watchdog (remove scheduled task)", risk="medium"):
        console.print("[yellow]Cancelled.[/yellow]")
        raise typer.Exit(0)
    result = uninstall_watchdog()
    console.print(f"[bold]{result.message}[/bold]")


@watchdog_app.command("status")
def watchdog_status_cmd():
    """Show watchdog status."""
    result = get_watchdog_status()
    console.print(f"[bold]Installed:[/bold] {result.installed}")
    console.print(f"[bold]Running:[/bold] {result.running}")
    console.print(f"[bold]Message:[/bold] {result.message}")


@app.command("watchdog-run", hidden=True)
def watchdog_run_cmd():
    """Internal: single watchdog tick (called by Task Scheduler)."""
    cfg = _load_cfg()
    watchdog_run(cfg)


# ── report ─────────────────────────────────────────────────────────────

@app.command()
def report(
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output path"),
    json_output: bool = typer.Option(False, "--json", "-j"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Generate a full health report."""
    cfg = _load_cfg(verbose)
    path = save_report(cfg, output_path=output, as_json=json_output)
    console.print(f"[green]Report saved to:[/green] {path}")


# ── config ─────────────────────────────────────────────────────────────

config_app = typer.Typer(help="Configuration management")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show():
    """Show current configuration."""
    cfg = load_config()
    from dataclasses import asdict
    console.print_json(json.dumps(asdict(cfg), default=str, ensure_ascii=False))


@config_app.command("init")
def config_init():
    """Create default configuration file."""
    cfg = MaintainerConfig()
    cfg.resolve_paths()
    path = save_config(cfg)
    console.print(f"[green]Config saved to:[/green] {path}")


@config_app.command("set")
def config_set(
    key: str = typer.Argument(help="Config key (e.g. watchdog.max_restart_attempts)"),
    value: str = typer.Argument(help="New value"),
):
    """Set a configuration value."""
    cfg = load_config()
    parts = key.split(".", 1)
    if len(parts) == 2:
        section, attr = parts
        section_obj = getattr(cfg, section, None)
        if section_obj is None or not hasattr(section_obj, attr):
            console.print(f"[red]Unknown key: {key}[/red]")
            raise typer.Exit(1)
        # Type coercion
        current = getattr(section_obj, attr)
        if isinstance(current, bool):
            new_val = value.lower() in ("true", "1", "yes")
        elif isinstance(current, int):
            new_val = int(value)
        elif isinstance(current, float):
            new_val = float(value)
        else:
            new_val = value
        setattr(section_obj, attr, new_val)
    elif hasattr(cfg, key):
        current = getattr(cfg, key)
        if isinstance(current, bool):
            new_val = value.lower() in ("true", "1", "yes")
        elif isinstance(current, int):
            new_val = int(value)
        elif isinstance(current, float):
            new_val = float(value)
        else:
            new_val = value
        setattr(cfg, key, new_val)
    else:
        console.print(f"[red]Unknown key: {key}[/red]")
        raise typer.Exit(1)
    path = save_config(cfg)
    console.print(f"[green]Set[/green] {key} = {new_val}")
    console.print(f"[dim]Saved to {path}[/dim]")


# ── issue (GitHub) ─────────────────────────────────────────────────────

@app.command()
def issue(
    title: str = typer.Option("", "--title", "-t", help="Issue title"),
    body: str = typer.Option("", "--body", "-b", help="Issue body (or auto-generate from diagnostics)"),
    auto: bool = typer.Option(False, "--auto", help="Auto-generate issue from latest diagnostics"),
    repo: str = typer.Option("NousResearch/hermes-agent", "--repo", help="Target GitHub repo"),
    dry_run: bool = typer.Option(True, "--dry-run/--create", help="Preview or create"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Create a GitHub issue from diagnostic results."""
    cfg = _load_cfg(verbose)

    if auto or not body:
        # Run diagnostics and generate issue body
        diag = run_diagnostics(cfg)
        if not title:
            title = f"[hermes-maintainer] Auto-diagnosed: {diag.error_count} errors, {diag.warn_count} warnings"

        # Build issue body
        lines = []
        lines.append("## Diagnostic Report (auto-generated by hermes-maintainer)\n")
        lines.append(f"**Errors:** {diag.error_count}")
        lines.append(f"**Warnings:** {diag.warn_count}\n")

        if diag.items:
            lines.append("### Findings\n")
            lines.append("| Category | Severity | Title | Detail |")
            lines.append("|----------|----------|-------|--------|")
            for item in diag.items:
                lines.append(f"| {item.category} | {item.severity} | {item.title} | {item.detail} |")
            lines.append("")

        lines.append("### Suggested Fixes\n")
        for item in diag.items:
            if item.fix_hint:
                lines.append(f"- **{item.title}**: {item.fix_hint}")

        lines.append("\n---")
        lines.append("*Auto-generated by [hermes-maintainer](https://github.com/Jes614753-sketch/hermes-maintainer)*")

        body = "\n".join(lines)

    if dry_run:
        console.print(f"\n[bold]Preview (dry-run):[/bold]")
        console.print(f"[cyan]Repo:[/cyan] {repo}")
        console.print(f"[cyan]Title:[/cyan] {title}\n")
        console.print(body)
        console.print(f"\n[dim]Use --create to actually create the issue.[/dim]")
        return

    # Create issue via gh CLI
    import subprocess
    try:
        result = subprocess.run(
            ["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            console.print(f"[green]Issue created:[/green] {result.stdout.strip()}")
        else:
            console.print(f"[red]Failed:[/red] {result.stderr[:500]}")
    except FileNotFoundError:
        console.print("[red]gh CLI not found. Install from https://cli.github.com[/red]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


# ── main ───────────────────────────────────────────────────────────────

def main():
    app()


if __name__ == "__main__":
    main()
