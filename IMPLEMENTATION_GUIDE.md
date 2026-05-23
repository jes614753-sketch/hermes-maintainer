# Hermes Maintainer — 实现指南

> 给 Kiro / 开发者的完整上下文文档。

## 项目背景

Hermes Agent (NousResearch, v0.13.0) 是一个 Python 自进化 AI Agent，安装在 `C:\Users\17551\AppData\Local\hermes\hermes-agent\`。用户遇到频繁崩溃和 API 超时，需要一个运维管家工具。

## 关键参考项目

| 项目 | 用途 | 地址 |
|------|------|------|
| samahn0601/hermes-doctor | 只读健康检查 CLI，**可直接集成** | `pip install hermes-doctor` |
| dongsheng123132/hermes-doctor | 16 条已知 issue 数据库（401、credential pool、provider switch 等） | GitHub |
| garrytan/gbrain | 健康评分系统（目标 90/100）、渐进式修复模式 | GitHub |
| Hermes 内置 `hermes doctor` | 基础诊断，我们要扩展它 | 已安装 |

## 已创建的文件

- `pyproject.toml` — 项目配置，依赖已定义
- `src/hermes_maintainer/config.py` — 配置管理模块（已完成）

## 待实现的模块

### 1. monitor.py — 只读健康监控

```python
# 核心检查项：
# 1. 进程检查：用 psutil 检测 hermes 进程是否存活、内存/CPU
# 2. SQLite 检查：只读打开 state.db，检查完整性（PRAGMA integrity_check）
#    - WAL 文件大小（超过 100MB 告警）
#    - session 链完整性（parent_session_id 引用是否存在）
#    - FTS5 索引健康
# 3. API 健康：httpx 轻量 ping LLM 端点（不发真实请求，只检查连通性）
# 4. 磁盘空间：检查 HERMES_HOME 所在分区使用率
# 5. 日志扫描：tail 最近 100 行日志，匹配 ERROR/CRITICAL/Traceback
# 6. 配置检查：.env 和 config.yaml 是否存在、API key 是否为占位符

# 输出格式：
@dataclass
class HealthReport:
    timestamp: str
    overall_score: int          # 0-100
    checks: list[CheckResult]   # 每项检查结果
    warnings: list[str]
    errors: list[str]

@dataclass
class CheckResult:
    name: str                   # "process", "sqlite", "api", "disk", "logs", "config"
    status: str                 # "ok", "warn", "error", "skip"
    message: str
    details: dict               # 额外数据
```

### 2. diagnose.py — 深度诊断

```python
# 集成 hermes-doctor：
#   from hermes_doctor.scanner import scan_all
#   results = scan_all(hermes_home=Path("..."))
#
# 自定义扩展检查：
# 1. 环境检查：Python 版本 >= 3.11、venv 完整性、pip list vs requirements.txt
# 2. 依赖审计：对比 pyproject.toml 的 exact pin 和实际安装版本
# 3. 网络诊断：DNS 解析、代理配置、SSL 证书
# 4. 已知 issue 匹配：从 dongsheng123132/hermes-doctor 的 known-issues.json 加载
#    16 条已知 issue，根据当前症状自动匹配
# 5. 性能分析：state.db 大小、session 数量、记忆文件总大小
#
# 输出：结构化诊断报告 + 每个问题的修复建议

# 已知 issue 数据库（从 dongsheng123132/hermes-doctor 复用）：
KNOWN_ISSUES = [
    {"id": 16394, "symptom": "hermes setup 静默跳过 API key 输入", "fix": "删 .env 该 KEY 行后重跑 setup"},
    {"id": 16677, "symptom": "DeepSeek V4 Pro via OpenRouter 崩溃", "fix": "改用 deepseek-chat"},
    {"id": 15914, "symptom": "fallback 链在 env 缺失时坍塌", "fix": ".env + auth.json 双写"},
    {"id": 3685,  "symptom": "/model 切换后 404(api_mode 残留)", "fix": "升级或手工删 api_mode"},
    {"id": 5561,  "symptom": "kimi-coding base_url 路由错", "fix": "重添加凭证"},
    # ... 共 16 条，从 known-issues.json 加载
]
```

### 3. repair.py — 安全修复

```python
# 核心原则：
# - 所有修复必须支持 --dry-run（只报告不执行）
# - 高风险操作（更新、DB 修改、配置重写）需人工确认
# - 修复前自动创建快照/备份
# - 单实例锁，防止两个 maintainer 同时修复

# 修复项（按风险分级）：
LOW_RISK = [
    "clean_pycache",        # 清理 __pycache__、*.pyc
    "clean_log_archive",    # 归档过大的日志文件
    "clean_node_cache",     # 清理 node_modules/.cache
    "validate_config",      # 检查配置文件语法
]

MEDIUM_RISK = [
    "sqlite_checkpoint",    # WAL checkpoint（只读→可写，但安全）
    "sqlite_vacuum",        # VACUUM（需要临时磁盘空间）
    "fts5_rebuild",         # 重建 FTS5 索引
    "verify_deps",          # 验证依赖一致性
]

HIGH_RISK = [
    "reinstall_venv",       # 重建 venv
    "reset_config",         # 重置配置文件
    "provider_rotate",      # 切换 API provider
]
```

### 4. updater.py — 版本管理

```python
# 功能：
# 1. 版本检查：读 pyproject.toml 的 version vs GitHub latest release
# 2. 更新前快照：备份 config.yaml、state.db、skills/、memory/ 到 snapshots/
# 3. 执行更新：调用 `hermes update` 并监控输出
# 4. 回滚：从快照恢复
# 5. 依赖锁定检查：验证 uv.lock 一致性

# 快照目录结构：
# <HERMES_HOME>/maintainer-snapshots/
#   └── 2026-05-24T120000/
#       ├── config.yaml
#       ├── state.db
#       ├── skills/
#       ├── memory/
#       └── metadata.json  # {"version": "0.13.0", "timestamp": "...", "reason": "pre-update"}
```

### 5. watchdog.py — OS 原生守护

```python
# Windows 实现：注册为 Task Scheduler 任务
# 关键设计（来自 Codex 建议）：
# - 崩溃循环保护：max_restart_attempts=3，超过后停止并告警
# - 冷却期：重启后等 restart_cooldown_sec=60s 再检查
# - 内存泄漏检测：超过 max_memory_mb 告警
# - 单实例锁：用文件锁防止多个 watchdog 同时运行

# Task Scheduler 注册命令（Windows）：
# schtasks /create /tn "HermesMaintainerWatchdog" /tr "python -m hermes_maintainer watchdog-run" /sc minute /mo 5 /f

# 也支持 NSSM（Non-Sucking Service Manager）作为 Windows Service：
# nssm install HermesWatchdog python -m hermes_maintainer watchdog-run
# nssm start HermesWatchdog
```

### 6. cli.py — 主入口

```python
# 使用 typer 框架
# 命令结构：
# hermes-maintainer status              # 快速状态概览（调 monitor --once）
# hermes-maintainer monitor             # 持续监控
# hermes-maintainer monitor --once      # 单次检查
# hermes-maintainer diagnose            # 全面诊断
# hermes-maintainer diagnose --focus api|db|env|net
# hermes-maintainer repair              # 自动修复
# hermes-maintainer repair --dry-run    # 只报告不修复
# hermes-maintainer repair --target venv|sqlite|config|cache
# hermes-maintainer update              # 检查并更新
# hermes-maintainer update --check      # 只检查
# hermes-maintainer rollback            # 回滚
# hermes-maintainer watchdog install    # 注册守护进程
# hermes-maintainer watchdog uninstall  # 卸载守护进程
# hermes-maintainer watchdog status     # 守护进程状态
# hermes-maintainer report              # 生成健康报告
# hermes-maintainer config show         # 显示配置
# hermes-maintainer config set <key> <value>
```

### 7. report.py — 健康报告

```python
# 生成 Markdown 格式的健康报告
# 保存到 <HERMES_HOME>/reports/health/YYYY-MM-DD.md
# 也支持 --json 输出
# 参考 gbrain 的评分系统：每项检查 0-100 分，加权平均得总分
```

## 技术约束

1. **SQLite 只读**：所有 state.db 操作用 PRAGMA 只读模式打开，不修改 Hermes 数据
2. **dry-run 优先**：repair 默认 dry-run，需显式 `--execute` 才真正执行
3. **人工确认边界**：高风险操作（venv 重建、配置重置、DB 修改）必须确认
4. **单实例锁**：用 `filelock` 或 `fcntl` 防止并发修复
5. **崩溃循环保护**：watchdog 重启 3 次失败后停止，发告警
6. **路径发现**：自动检测 HERMES_HOME（Windows: %LOCALAPPDATA%/hermes，Unix: ~/.hermes）

## 实现顺序

1. **config.py** ✅ 已完成
2. **monitor.py** — 最核心，先做只读健康检查
3. **diagnose.py** — 集成 hermes-doctor + 已知 issue
4. **cli.py** — 让 status/monitor/diagnose 能跑起来
5. **repair.py** — dry-run 优先
6. **updater.py** — 快照 + 回滚
7. **watchdog.py** — Task Scheduler 注册
8. **report.py** — 报告生成
9. **测试** — pytest 覆盖核心路径
