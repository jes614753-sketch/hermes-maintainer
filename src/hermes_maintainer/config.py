"""Configuration management for hermes-maintainer."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# ── Hermes path discovery ──────────────────────────────────────────────

def _hermes_home_candidates() -> list[Path]:
    """Return ordered list of possible HERMES_HOME paths."""
    env = os.environ.get("HERMES_HOME")
    if env:
        return [Path(env)]

    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes"
        return [local, Path.home() / ".hermes"]
    return [Path.home() / ".hermes"]


def discover_hermes_home() -> Optional[Path]:
    """Find the actual HERMES_HOME directory."""
    for p in _hermes_home_candidates():
        if p.is_dir():
            return p
    return None


def hermes_agent_root() -> Optional[Path]:
    """Find the hermes-agent source/install directory."""
    home = discover_hermes_home()
    if home is None:
        return None
    # Typical layout: <HERMES_HOME>/hermes-agent/
    candidate = home / "hermes-agent"
    if candidate.is_dir():
        return candidate
    # Fallback: HERMES_HOME itself
    return home


# ── Maintainer config ──────────────────────────────────────────────────

@dataclass
class WatchdogConfig:
    enabled: bool = True
    check_interval_sec: int = 300  # 5 min
    max_restart_attempts: int = 3
    restart_cooldown_sec: int = 60
    auto_restart: bool = True
    max_memory_mb: int = 2048


@dataclass
class MonitorConfig:
    api_timeout_sec: int = 10
    disk_warn_pct: float = 90.0
    memory_warn_pct: float = 85.0


@dataclass
class RepairConfig:
    dry_run_by_default: bool = True
    require_confirmation: bool = True
    max_log_size_mb: int = 100


@dataclass
class NotifyConfig:
    enabled: bool = False
    webhook_url: str = ""
    desktop_notify: bool = True


@dataclass
class MaintainerConfig:
    hermes_home: Optional[Path] = None
    hermes_agent_root: Optional[Path] = None
    watchdog: WatchdogConfig = field(default_factory=WatchdogConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    repair: RepairConfig = field(default_factory=RepairConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    verbose: bool = False

    def resolve_paths(self) -> None:
        """Auto-discover paths if not explicitly set."""
        if self.hermes_home is None:
            self.hermes_home = discover_hermes_home()
        if self.hermes_agent_root is None:
            self.hermes_agent_root = hermes_agent_root()


# ── Config file I/O ────────────────────────────────────────────────────

_CONFIG_FILENAME = "maintainer-config.yaml"


def config_path() -> Path:
    """Path to the maintainer config file."""
    home = discover_hermes_home()
    if home:
        return home / _CONFIG_FILENAME
    return Path.home() / ".hermes" / _CONFIG_FILENAME


def load_config(path: Optional[Path] = None) -> MaintainerConfig:
    """Load config from YAML, falling back to defaults."""
    p = path or config_path()
    cfg = MaintainerConfig()
    if not p.exists():
        cfg.resolve_paths()
        return cfg
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "watchdog" in data:
        for k, v in data["watchdog"].items():
            if hasattr(cfg.watchdog, k):
                setattr(cfg.watchdog, k, v)
    if "monitor" in data:
        for k, v in data["monitor"].items():
            if hasattr(cfg.monitor, k):
                setattr(cfg.monitor, k, v)
    if "repair" in data:
        for k, v in data["repair"].items():
            if hasattr(cfg.repair, k):
                setattr(cfg.repair, k, v)
    if "notify" in data:
        for k, v in data["notify"].items():
            if hasattr(cfg.notify, k):
                setattr(cfg.notify, k, v)
    if "verbose" in data:
        cfg.verbose = data["verbose"]
    cfg.resolve_paths()
    return cfg


def save_config(cfg: MaintainerConfig, path: Optional[Path] = None) -> Path:
    """Save config to YAML."""
    p = path or config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "watchdog": {
            "enabled": cfg.watchdog.enabled,
            "check_interval_sec": cfg.watchdog.check_interval_sec,
            "max_restart_attempts": cfg.watchdog.max_restart_attempts,
            "restart_cooldown_sec": cfg.watchdog.restart_cooldown_sec,
            "auto_restart": cfg.watchdog.auto_restart,
            "max_memory_mb": cfg.watchdog.max_memory_mb,
        },
        "monitor": {
            "api_timeout_sec": cfg.monitor.api_timeout_sec,
            "disk_warn_pct": cfg.monitor.disk_warn_pct,
            "memory_warn_pct": cfg.monitor.memory_warn_pct,
        },
        "repair": {
            "dry_run_by_default": cfg.repair.dry_run_by_default,
            "require_confirmation": cfg.repair.require_confirmation,
            "max_log_size_mb": cfg.repair.max_log_size_mb,
        },
        "notify": {
            "enabled": cfg.notify.enabled,
            "webhook_url": cfg.notify.webhook_url,
            "desktop_notify": cfg.notify.desktop_notify,
        },
        "verbose": cfg.verbose,
    }
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    return p
