"""Deep diagnostics for Hermes Agent — extends hermes-doctor."""

from __future__ import annotations

import json
import logging
import platform
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import MaintainerConfig

logger = logging.getLogger(__name__)


# ── Data models ────────────────────────────────────────────────────────

@dataclass
class DiagnosticItem:
    category: str       # "env", "config", "api", "db", "net", "deps", "known_issue"
    severity: str       # "info", "warn", "error"
    title: str
    detail: str
    fix_hint: str = ""  # suggested fix

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "fix_hint": self.fix_hint,
        }


@dataclass
class DiagnosticReport:
    items: list[DiagnosticItem] = field(default_factory=list)

    def add(self, item: DiagnosticItem) -> None:
        self.items.append(item)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.items if i.severity == "error")

    @property
    def warn_count(self) -> int:
        return sum(1 for i in self.items if i.severity == "warn")

    def to_dict(self) -> dict:
        return {
            "summary": {"errors": self.error_count, "warnings": self.warn_count, "total": len(self.items)},
            "items": [i.to_dict() for i in self.items],
        }


# ── Known issues database ──────────────────────────────────────────────

KNOWN_ISSUES: list[dict] = [
    {
        "id": 16394,
        "symptom": "hermes setup 静默跳过 API key 输入",
        "match": ["setup", "skip", "api key", "green check"],
        "fix": "删 .env 该 KEY 行后重跑 setup",
    },
    {
        "id": 16677,
        "symptom": "DeepSeek V4 Pro via OpenRouter 崩溃",
        "match": ["deepseek", "v4", "openrouter", "crash", "gateway"],
        "fix": "改用 deepseek-chat 或 deepseek-coder",
    },
    {
        "id": 15914,
        "symptom": "fallback 链在 env 缺失时坍塌",
        "match": ["fallback", "env", "missing", "401"],
        "fix": ".env + auth.json 双写 API key",
    },
    {
        "id": 3685,
        "symptom": "/model 切换后 404 (api_mode 残留)",
        "match": ["404", "model", "switch", "api_mode"],
        "fix": "升级或手工删 api_mode 配置项",
    },
    {
        "id": 5561,
        "symptom": "kimi-coding base_url 路由错",
        "match": ["kimi", "base_url", "route", "404"],
        "fix": "删除并重新添加 kimi 凭证",
    },
    {
        "id": 5908,
        "symptom": "kimi-coding base_url 路由错 (续)",
        "match": ["kimi", "moonshot", "base_url"],
        "fix": "重添加凭证",
    },
    {
        "id": 0,
        "symptom": "no-key-required 占位符被当成真实 key",
        "match": ["no-key-required", "401", "auth"],
        "fix": "检查 .env 和 auth.json，替换为真实 API key",
    },
    {
        "id": 0,
        "symptom": "WAL 文件过大导致性能下降",
        "match": ["wal", "slow", "sqlite", "checkpoint"],
        "fix": "执行 WAL checkpoint: PRAGMA wal_checkpoint(TRUNCATE)",
    },
    {
        "id": 0,
        "symptom": "gateway already running 启动失败",
        "match": ["gateway", "already running", "port", "lock"],
        "fix": "杀死残留进程或删除 PID 锁文件",
    },
    {
        "id": 0,
        "symptom": "Windows UTF-8 编码问题",
        "match": ["utf-8", "encoding", "unicode", "charmap"],
        "fix": "确保 hermes_bootstrap.py 正确加载（venv 可能损坏）",
    },
    {
        "id": 0,
        "symptom": "venv 损坏导致 import 失败",
        "match": ["modulenotfounderror", "import", "venv"],
        "fix": "重建 venv: python -m venv venv && pip install -e '.[all]'",
    },
    {
        "id": 0,
        "symptom": "SSH 终端后端连接失败",
        "match": ["ssh", "terminal", "connect", "timeout"],
        "fix": "检查 SSH key 和 known_hosts 配置",
    },
    # ── WorkBuddy repair experience (2026-05) ──
    {
        "id": 0,
        "symptom": "WSL2 terminal 无网络，web 工具不可用",
        "match": ["wsl2", "network", "terminal", "web", "unreachable"],
        "fix": "配置 Firecrawl 云端 web toolset（web_search/web_extract/web_crawl），绕过 WSL2 网络限制",
    },
    {
        "id": 0,
        "symptom": "搜索引擎被反爬拦截，web_search 返回空结果",
        "match": ["search", "anti-bot", "blocked", "captcha", "empty", "browserbase"],
        "fix": "改用 Firecrawl 云端搜索，或用 Seznam.cz（唯一不拦截云浏览器的搜索引擎）",
    },
    {
        "id": 0,
        "symptom": "飞书/QQ 渠道 connected 但不回复消息",
        "match": ["feishu", "lark", "qq", "connected", "no reply", "不回复", "gateway"],
        "fix": "排查 4 项：1) gateway 进程是否运行 2) 凭证/App Secret 是否过期 3) 隧道(ngrok)是否断开 4) 事件订阅是否掉了",
    },
    {
        "id": 0,
        "symptom": "飞书群聊 @bot 不回复（私聊正常）",
        "match": ["feishu", "group", "群聊", "at", "@", "不回复"],
        "fix": "飞书开放平台 → 应用 → 事件订阅 → 开启 im.message.receive_v1 群聊事件开关",
    },
    {
        "id": 0,
        "symptom": "Browserbase 云浏览器主流搜索引擎全部反爬",
        "match": ["browserbase", "google", "bing", "search", "反爬", "blank"],
        "fix": "BBC/Guardian 等新闻站可直接访问；搜索改用 Firecrawl 或 Seznam.cz",
    },
    {
        "id": 0,
        "symptom": "Hermes 更新后 venv 部分损坏，hermes_bootstrap 加载失败",
        "match": ["update", "bootstrap", "venv", "partial", "broken"],
        "fix": "hermes_bootstrap 缺失不影响核心功能（仅跳过 Windows UTF-8 设置），但建议重建 venv",
    },
    {
        "id": 0,
        "symptom": "credential pool 耗尽，所有 API key 被限流",
        "match": ["credential", "pool", "rate limit", "429", "exhausted", "所有 key"],
        "fix": "hermes auth 查看池状态，添加新 key 或等待冷却。双写 .env + auth.json 避免单点故障",
    },
]


def match_known_issues(symptoms: str) -> list[dict]:
    """Match symptom text against known issues database."""
    symptoms_lower = symptoms.lower()
    matched = []
    for issue in KNOWN_ISSUES:
        score = sum(1 for keyword in issue["match"] if keyword in symptoms_lower)
        if score >= 2:
            matched.append({**issue, "match_score": score})
    return sorted(matched, key=lambda x: x["match_score"], reverse=True)


# ── Diagnostic checks ──────────────────────────────────────────────────

def check_environment(hermes_home: Path) -> list[DiagnosticItem]:
    """Check Python, venv, and system environment."""
    items = []

    # Python version
    ver = sys.version_info
    if ver < (3, 11):
        items.append(DiagnosticItem(
            "env", "error", "Python version too old",
            f"Python {ver.major}.{ver.minor}.{ver.micro}, need >= 3.11",
            "Install Python 3.11+ from python.org",
        ))
    else:
        items.append(DiagnosticItem(
            "env", "info", "Python version OK",
            f"Python {ver.major}.{ver.minor}.{ver.micro}",
        ))

    # venv check
    venv_path = hermes_home / "venv"
    if not venv_path.exists():
        items.append(DiagnosticItem(
            "env", "error", "venv directory missing",
            f"Expected at {venv_path}",
            "Run: python -m venv venv && pip install -e '.[all]'",
        ))
    else:
        python_exe = venv_path / "Scripts" / "python.exe" if sys.platform == "win32" else venv_path / "bin" / "python"
        if not python_exe.exists():
            items.append(DiagnosticItem(
                "env", "error", "venv python executable missing",
                f"Expected at {python_exe}",
                "Rebuild venv: python -m venv venv",
            ))
        else:
            items.append(DiagnosticItem("env", "info", "venv OK", str(venv_path)))

    # Disk space
    try:
        usage = shutil.disk_usage(str(hermes_home))
        free_gb = usage.free / (1024**3)
        if free_gb < 1:
            items.append(DiagnosticItem(
                "env", "error", "Disk space critically low",
                f"Only {free_gb:.1f} GB free",
                "Free up disk space",
            ))
        elif free_gb < 5:
            items.append(DiagnosticItem(
                "env", "warn", "Disk space low",
                f"{free_gb:.1f} GB free",
            ))
    except Exception:
        pass

    return items


def check_hermes_version(hermes_home: Path) -> list[DiagnosticItem]:
    """Check installed Hermes version."""
    items = []
    agent_root = hermes_home / "hermes-agent"
    pyproject = agent_root / "pyproject.toml"
    if not pyproject.exists():
        items.append(DiagnosticItem(
            "env", "warn", "Cannot find hermes-agent pyproject.toml",
            f"Expected at {pyproject}",
        ))
        return items

    try:
        content = pyproject.read_text(encoding="utf-8")
        for line in content.splitlines():
            if line.strip().startswith("version"):
                version = line.split("=")[1].strip().strip('"').strip("'")
                items.append(DiagnosticItem("env", "info", f"Hermes version: {version}", version))
                break
    except Exception as e:
        items.append(DiagnosticItem("env", "warn", "Cannot read Hermes version", str(e)))

    return items


def check_database_deep(hermes_home: Path) -> list[DiagnosticItem]:
    """Deep database diagnostics."""
    items = []
    db_path = hermes_home / "state.db"
    if not db_path.exists():
        items.append(DiagnosticItem("db", "info", "state.db not found (first run?)", str(db_path)))
        return items

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        conn.execute("PRAGMA busy_timeout=3000")

        # Schema version
        try:
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
            items.append(DiagnosticItem("db", "info", f"Schema version: {ver}", f"user_version={ver}"))
        except sqlite3.OperationalError:
            pass

        # Session chain integrity
        try:
            orphans = conn.execute("""
                SELECT COUNT(*) FROM sessions
                WHERE parent_session_id IS NOT NULL
                AND parent_session_id NOT IN (SELECT id FROM sessions)
            """).fetchone()[0]
            if orphans > 0:
                items.append(DiagnosticItem(
                    "db", "warn", f"{orphans} orphaned session chains",
                    "Some sessions reference non-existent parent sessions",
                ))
        except sqlite3.OperationalError:
            pass

        # Largest sessions
        try:
            large = conn.execute("""
                SELECT id, LENGTH(messages) as msg_size
                FROM sessions ORDER BY msg_size DESC LIMIT 3
            """).fetchall()
            if large:
                details = [{"id": r[0][:16], "size_kb": r[1] // 1024} for r in large]
                items.append(DiagnosticItem(
                    "db", "info", f"Top 3 largest sessions",
                    json.dumps(details),
                ))
        except sqlite3.OperationalError:
            pass

        conn.close()
    except sqlite3.DatabaseError as e:
        items.append(DiagnosticItem("db", "error", f"Database error: {e}", str(e)))

    return items


def check_network(hermes_home: Path) -> list[DiagnosticItem]:
    """Network connectivity diagnostics."""
    items = []
    import httpx

    endpoints = {
        "OpenAI API": "https://api.openai.com/v1/models",
        "OpenRouter API": "https://openrouter.ai/api/v1/models",
        "GitHub": "https://api.github.com",
    }

    for name, url in endpoints.items():
        try:
            resp = httpx.get(url, timeout=10, follow_redirects=True)
            latency = int(resp.elapsed.total_seconds() * 1000)
            if resp.status_code < 500:
                items.append(DiagnosticItem("net", "info", f"{name} reachable", f"{resp.status_code} in {latency}ms"))
            else:
                items.append(DiagnosticItem("net", "warn", f"{name} server error", f"HTTP {resp.status_code}"))
        except httpx.TimeoutException:
            items.append(DiagnosticItem("net", "warn", f"{name} timeout", "10s timeout"))
        except httpx.ConnectError as e:
            items.append(DiagnosticItem("net", "error", f"{name} unreachable", str(e)[:100]))

    # Proxy check
    proxy_vars = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"]
    proxies = {k: v for k in proxy_vars if (v := __import__("os").environ.get(k))}
    if proxies:
        items.append(DiagnosticItem("net", "info", "Proxy configured", json.dumps(proxies)))

    return items


def check_dependencies(hermes_home: Path) -> list[DiagnosticItem]:
    """Check dependency consistency."""
    items = []
    agent_root = hermes_home / "hermes-agent"
    venv_path = agent_root / "venv"

    if not venv_path.exists():
        return items

    # Check if uv.lock is consistent
    uv_lock = agent_root / "uv.lock"
    if uv_lock.exists():
        items.append(DiagnosticItem("deps", "info", "uv.lock present", "Dependencies are locked"))
    else:
        items.append(DiagnosticItem("deps", "warn", "uv.lock missing", "Dependencies may drift"))

    return items


# ── Main entry ─────────────────────────────────────────────────────────

def run_diagnostics(
    cfg: MaintainerConfig,
    focus: Optional[str] = None,
    symptoms: str = "",
) -> DiagnosticReport:
    """Run deep diagnostics and return a report."""
    cfg.resolve_paths()
    report = DiagnosticReport()

    hermes_home = cfg.hermes_home
    if hermes_home is None:
        report.add(DiagnosticItem("env", "error", "Hermes home not found", "Set HERMES_HOME or install Hermes"))
        return report

    checks = {
        "env": lambda: check_environment(hermes_home) + check_hermes_version(hermes_home),
        "db": lambda: check_database_deep(hermes_home),
        "net": lambda: check_network(hermes_home),
        "api": lambda: check_network(hermes_home),
        "deps": lambda: check_dependencies(hermes_home),
    }

    if focus:
        if focus in checks:
            for item in checks[focus]():
                report.add(item)
        else:
            report.add(DiagnosticItem("env", "warn", f"Unknown focus: {focus}", f"Available: {', '.join(checks.keys())}"))
    else:
        for check_fn in checks.values():
            for item in check_fn():
                report.add(item)

    # Match known issues from symptoms
    if symptoms:
        matched = match_known_issues(symptoms)
        for issue in matched[:3]:
            report.add(DiagnosticItem(
                "known_issue", "warn",
                f"Known issue #{issue['id']}: {issue['symptom']}",
                f"Match score: {issue['match_score']}",
                issue["fix"],
            ))

    return report
