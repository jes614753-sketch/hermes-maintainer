#!/usr/bin/env python3
"""
Hermes Health Check & Auto-Fix Script
Based on real-world troubleshooting experience (7+ incidents)

Usage:
    python hermes-healthcheck.py           # Basic check
    python hermes-healthcheck.py --deep    # Deep check (API validation)
    python hermes-healthcheck.py --fix     # Check and auto-fix

Author: Ayo (based on battle-tested experience)
Date: 2026-05-24
"""

import argparse
import json
import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime

# ─── Constants ───────────────────────────────────────────────

HERMES_HOME = Path(os.getenv("HERMES_HOME", Path.home() / ".hermes"))
HERMES_ENV = HERMES_HOME / ".env"
HERMES_CONFIG = HERMES_HOME / "config.yaml"
LOCK_DIR = Path(os.getenv("HERMES_GATEWAY_LOCK_DIR", 
                           Path.home() / ".local" / "state" / "hermes" / "gateway-locks"))
PID_FILE = HERMES_HOME / "gateway.pid"
STATE_FILE = HERMES_HOME / "gateway_state.json"

CRITICAL_KEYS = [
    "OPENAI_API_KEY", "KIMI_API_KEY", "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY", "GLM_API_KEY", "FIRECRAWL_API_KEY",
    "FEISHU_APP_ID", "FEISHU_APP_SECRET",
]

PROVIDER_KEY_MAP = {
    "custom": "OPENAI_API_KEY",  # The hidden truth: custom provider only reads this
    "openrouter": "OPENROUTER_API_KEY",
    "zai": "GLM_API_KEY",
    "z.ai": "GLM_API_KEY",
}

# Colors
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

# ─── Helpers ─────────────────────────────────────────────────

def ok(msg, detail=""):
    print(f"  {GREEN}✓{RESET} {msg}" + (f" {DIM}{detail}{RESET}" if detail else ""))

def warn(msg, detail=""):
    print(f"  {YELLOW}⚠{RESET} {msg}" + (f" {DIM}{detail}{RESET}" if detail else ""))

def fail(msg, detail=""):
    print(f"  {RED}✗{RESET} {msg}" + (f" {DIM}{detail}{RESET}" if detail else ""))

def info(msg):
    print(f"    {CYAN}→{RESET} {msg}")

def section(title):
    print(f"\n{BOLD}{CYAN}◆ {title}{RESET}")

def mask_key(key):
    if not key or len(key) < 12:
        return "(empty or too short)"
    return f"{key[:8]}...{key[-4:]}"


# ─── Check Functions ─────────────────────────────────────────

def check_env_file():
    """Check .env file existence and validity."""
    section("Environment File")
    
    if not HERMES_ENV.exists():
        fail(f".env not found", f"Expected: {HERMES_ENV}")
        return False
    
    ok(f".env exists", str(HERMES_ENV))
    
    # Check encoding
    try:
        content = HERMES_ENV.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = HERMES_ENV.read_text(encoding="utf-8-sig")
            warn(".env has BOM marker", "Re-save without BOM")
        except UnicodeDecodeError:
            fail(".env encoding error", "Must be UTF-8 without BOM")
            return False
    
    # Check for non-ASCII in credential values
    issues = []
    for line_no, line in enumerate(content.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if any(key.endswith(s) for s in ("_API_KEY", "_TOKEN", "_SECRET", "_KEY")):
            try:
                value.encode("ascii")
            except UnicodeEncodeError:
                issues.append((line_no, key))
    
    if issues:
        fail(f".env has non-ASCII in credential values")
        for line_no, key in issues:
            info(f"Line {line_no}: {key} contains non-ASCII characters")
        return False
    
    ok("No encoding issues")
    
    # Check for Chinese comments (common source of encoding problems on Windows)
    chinese_lines = []
    for line_no, line in enumerate(content.splitlines(), 1):
        if line.strip().startswith("#"):
            try:
                line.encode("ascii")
            except UnicodeEncodeError:
                chinese_lines.append(line_no)
    
    if chinese_lines:
        warn(f"Chinese comments detected on lines: {chinese_lines}", 
             "May cause encoding issues on Windows; prefer English comments")
    
    return True


def check_env_vars():
    """Check environment variable consistency between .env and os.environ."""
    section("Environment Variables")
    
    if not HERMES_ENV.exists():
        return
    
    # Parse .env values
    env_values = {}
    try:
        content = HERMES_ENV.read_text(encoding="utf-8-sig", errors="replace")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            # Remove surrounding quotes
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            env_values[key] = value
    except Exception as e:
        fail(f"Cannot parse .env: {e}")
        return
    
    # Check critical keys
    for key in CRITICAL_KEYS:
        in_env = key in env_values and env_values[key]
        in_os = bool(os.getenv(key))
        
        if in_env and in_os:
            env_val = env_values[key]
            os_val = os.getenv(key, "")
            if env_val != os_val:
                fail(f"{key} conflict!",
                     f".env={mask_key(env_val)}, os={mask_key(os_val)}")
                info(f"Fix: [Environment]::SetEnvironmentVariable('{key}', $null, 'User')")
                info(f"  Then restart terminal")
            else:
                ok(f"{key} consistent", mask_key(env_val))
        elif in_env and not in_os:
            ok(f"{key} in .env", mask_key(env_values[key]))
        elif not in_env and in_os:
            warn(f"{key} only in os.environ", mask_key(os.getenv(key)))
            info(f"Source may be Windows User env var or shell profile")
        else:
            # Not set anywhere - only warn for key keys
            if key in ("OPENAI_API_KEY", "KIMI_API_KEY", "FEISHU_APP_ID"):
                warn(f"{key} not set")
    
    # Check the CRITICAL custom provider key mapping
    check_custom_provider_key(env_values)


def check_custom_provider_key(env_values):
    """Check if custom provider has the right key mapped."""
    section("Custom Provider Key Mapping")
    
    if not HERMES_CONFIG.exists():
        warn("config.yaml not found")
        return
    
    try:
        content = HERMES_CONFIG.read_text(encoding="utf-8")
    except Exception:
        return
    
    # Simple YAML parsing (avoid pyyaml dependency)
    provider = None
    base_url = None
    model = None
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("provider:"):
            provider = line.split(":", 1)[1].strip().strip('"').strip("'")
        elif line.startswith("base_url:"):
            base_url = line.split(":", 1)[1].strip().strip('"').strip("'")
        elif line.startswith("default:"):
            model = line.split(":", 1)[1].strip().strip('"').strip("'")
    
    if provider == "custom":
        info(f"Provider: custom, base_url: {base_url}, model: {model}")
        
        # The KEY insight: custom provider only reads OPENAI_API_KEY
        openai_key = os.getenv("OPENAI_API_KEY", "") or env_values.get("OPENAI_API_KEY", "")
        
        if not openai_key:
            fail("custom provider requires OPENAI_API_KEY", 
                 "But OPENAI_API_KEY is not set!")
            info("Add to .env: OPENAI_API_KEY=<your-api-key>")
            info("This is the #1 most common Hermes misconfiguration")
        else:
            ok(f"OPENAI_API_KEY is set", mask_key(openai_key))
        
        # Check if user set KIMI_API_KEY but forgot OPENAI_API_KEY
        if "KIMI_API_KEY" in env_values and env_values["KIMI_API_KEY"]:
            if not openai_key:
                fail("KIMI_API_KEY is set but OPENAI_API_KEY is not",
                     "Hermes custom provider ONLY reads OPENAI_API_KEY!")
            elif openai_key != env_values["KIMI_API_KEY"]:
                warn("KIMI_API_KEY != OPENAI_API_KEY",
                     "They should be the same for Kimi provider")
                info(f"KIMI_API_KEY = {mask_key(env_values['KIMI_API_KEY'])}")
                info(f"OPENAI_API_KEY = {mask_key(openai_key)}")
        
        # Check if user set DEEPSEEK_API_KEY but forgot OPENAI_API_KEY
        if "DEEPSEEK_API_KEY" in env_values and env_values["DEEPSEEK_API_KEY"]:
            if not openai_key:
                fail("DEEPSEEK_API_KEY is set but OPENAI_API_KEY is not",
                     "Hermes custom provider ONLY reads OPENAI_API_KEY!")
            elif openai_key != env_values["DEEPSEEK_API_KEY"]:
                warn("DEEPSEEK_API_KEY != OPENAI_API_KEY",
                     "They should be the same for DeepSeek provider")
    
    elif provider:
        ok(f"Provider: {provider}")


def check_windows_env_vars():
    """Check Windows user-level environment variables that might conflict."""
    if sys.platform != "win32":
        return
    
    section("Windows User Environment Variables")
    
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-ChildItem Env: | Where-Object { $_.Name -match '_API_KEY|_TOKEN|_SECRET|_KEY' } | "
             "ForEach-Object { \"$($_.Name)=$($_.Value)\" }"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().splitlines()
            for line in lines:
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    if value and key in CRITICAL_KEYS:
                        warn(f"System env: {key} = {mask_key(value)}")
                        info("This may override .env! Remove with:")
                        info(f'  [Environment]::SetEnvironmentVariable("{key}", $null, "User")')
    except Exception:
        pass
    
    # Also check specifically for User-level vars (persisted across reboots)
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Environment]::GetEnvironmentVariable('OPENAI_API_KEY', 'User')"],
            capture_output=True, text=True, timeout=10
        )
        user_val = result.stdout.strip() if result.returncode == 0 else ""
        if user_val:
            warn(f"Windows User env OPENAI_API_KEY = {mask_key(user_val)}")
            info("This persists across reboots and overrides .env!")
            info('Remove: [Environment]::SetEnvironmentVariable("OPENAI_API_KEY", $null, "User")')
    except Exception:
        pass


def check_lock_files():
    """Check for stale lock files."""
    section("Gateway Lock Files")
    
    if not LOCK_DIR.exists():
        ok("Lock directory does not exist", "(no locks ever created)")
        return
    
    lock_files = list(LOCK_DIR.glob("*.lock"))
    if not lock_files:
        ok("No lock files", "(clean)")
        return
    
    stale_count = 0
    active_count = 0
    
    for lock_file in lock_files:
        try:
            data = json.loads(lock_file.read_text(encoding="utf-8"))
            pid = data.get("pid", 0)
            
            # Check if process exists
            try:
                import psutil
                alive = psutil.pid_exists(pid)
                if alive:
                    try:
                        proc = psutil.Process(pid)
                        cmdline = " ".join(proc.cmdline())
                        if any(p in cmdline for p in ["hermes", "gateway"]):
                            ok(f"Lock: {lock_file.name} (PID {pid}, active gateway)")
                            active_count += 1
                        else:
                            warn(f"Lock: {lock_file.name} (PID {pid}, NOT gateway: {cmdline[:50]})")
                            stale_count += 1
                    except (psutil.AccessDenied, psutil.NoSuchProcess):
                        stale_count += 1
                else:
                    warn(f"Lock: {lock_file.name} (PID {pid}, process dead)")
                    stale_count += 1
            except ImportError:
                warn(f"Lock: {lock_file.name} (PID {pid}, cannot verify - psutil missing)")
        except (json.JSONDecodeError, OSError):
            warn(f"Lock: {lock_file.name} (invalid JSON)")
            stale_count += 1
    
    if stale_count > 0:
        info(f"Found {stale_count} stale lock file(s)")
        info(f"Fix: Remove {LOCK_DIR}")
        if args.fix:
            import shutil
            shutil.rmtree(LOCK_DIR, ignore_errors=True)
            ok("Lock directory cleaned")


def check_gateway_status():
    """Check if gateway is running."""
    section("Gateway Process")
    
    # Check PID file
    if PID_FILE.exists():
        try:
            data = json.loads(PID_FILE.read_text(encoding="utf-8"))
            pid = data.get("pid", 0)
            ok(f"PID file exists", f"PID {pid}")
            
            try:
                import psutil
                if psutil.pid_exists(pid):
                    try:
                        proc = psutil.Process(pid)
                        cmdline = " ".join(proc.cmdline())
                        if any(p in cmdline for p in ["hermes", "gateway"]):
                            ok(f"Gateway process running", f"PID {pid}")
                        else:
                            warn(f"PID {pid} is not a gateway process", cmdline[:60])
                    except (psutil.AccessDenied, psutil.NoSuchProcess):
                        pass
                else:
                    warn(f"PID {pid} not running", "Gateway may have crashed")
            except ImportError:
                pass
        except (json.JSONDecodeError, OSError):
            warn("PID file invalid")
    else:
        info("No PID file (gateway not running or crashed without cleanup)")
    
    # Check state file
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            state = data.get("gateway_state", "unknown")
            platforms = data.get("platforms", {})
            updated = data.get("updated_at", "unknown")
            
            state_icon = GREEN + "✓" if state == "running" else YELLOW + "⚠"
            print(f"  {state_icon}{RESET} State: {state}, updated: {updated}")
            
            for plat_name, plat_data in platforms.items():
                plat_state = plat_data.get("state", "unknown")
                error = plat_data.get("error_message", "")
                if error:
                    fail(f"Platform {plat_name}: {plat_state}", error)
                else:
                    ok(f"Platform {plat_name}: {plat_state}")
        except (json.JSONDecodeError, OSError):
            pass


def check_proxy_config():
    """Check proxy configuration that might block Feishu connection."""
    section("Proxy Configuration")
    
    http_proxy = os.getenv("HTTP_PROXY", "") or os.getenv("http_proxy", "")
    https_proxy = os.getenv("HTTPS_PROXY", "") or os.getenv("https_proxy", "")
    no_proxy = os.getenv("no_proxy", "") or os.getenv("NO_PROXY", "")
    
    if http_proxy:
        warn(f"HTTP_PROXY is set", http_proxy)
        if "open.feishu.cn" not in no_proxy:
            fail("open.feishu.cn not in no_proxy!", "Feishu connection will fail through proxy")
        else:
            ok("open.feishu.cn in no_proxy")
    else:
        ok("HTTP_PROXY not set", "(good for Feishu)")
    
    if https_proxy:
        warn(f"HTTPS_PROXY is set", https_proxy)
    else:
        ok("HTTPS_PROXY not set")
    
    if no_proxy:
        ok(f"no_proxy set", no_proxy)


def check_deep_api():
    """Deep check: validate API keys by making test requests."""
    section("API Key Validation (Deep Check)")
    
    # Check Kimi/Moonshot
    kimi_key = os.getenv("KIMI_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
    if kimi_key:
        try:
            import urllib.request
            import urllib.error
            
            req = urllib.request.Request(
                "https://api.moonshot.cn/v1/models",
                headers={"Authorization": f"Bearer {kimi_key}"}
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        ok("Kimi API key valid", mask_key(kimi_key))
                    else:
                        warn(f"Kimi API returned {resp.status}")
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    fail("Kimi API key INVALID (401)", mask_key(kimi_key))
                else:
                    warn(f"Kimi API returned {e.code}")
            except Exception as e:
                warn(f"Cannot reach Kimi API", str(e)[:60])
        except ImportError:
            pass
    else:
        info("No Kimi key to validate")
    
    # Check Feishu
    app_id = os.getenv("FEISHU_APP_ID", "")
    app_secret = os.getenv("FEISHU_APP_SECRET", "")
    if app_id and app_secret:
        try:
            import urllib.request
            import json as _json
            
            data = _json.dumps({
                "app_id": app_id,
                "app_secret": app_secret
            }).encode("utf-8")
            
            req = urllib.request.Request(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                data=data,
                headers={"Content-Type": "application/json; charset=utf-8"}
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = _json.loads(resp.read())
                    if result.get("code") == 0:
                        ok("Feishu App ID/Secret valid")
                    else:
                        fail(f"Feishu auth failed", result.get("msg", "unknown"))
            except urllib.error.HTTPError as e:
                fail(f"Feishu API error", f"HTTP {e.code}")
            except Exception as e:
                warn(f"Cannot reach Feishu API", str(e)[:60])
        except ImportError:
            pass


def check_lark_oapi_version():
    """Check if lark-oapi is the correct version."""
    section("Dependencies")
    
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", "lark-oapi"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("Version:"):
                    version = line.split(":", 1)[1].strip()
                    if version == "1.4.5":
                        ok(f"lark-oapi version: {version}", "(compatible)")
                    else:
                        warn(f"lark-oapi version: {version}", 
                             "Only 1.4.5 is confirmed compatible; other versions may fail")
                    break
        else:
            warn("lark-oapi not found in current Python")
    except Exception:
        pass
    
    # Check psutil
    try:
        import psutil
        ok(f"psutil available", f"version {psutil.__version__}")
    except ImportError:
        warn("psutil not installed", "Recommended for Windows process detection")


def generate_report():
    """Generate a summary report."""
    section("Summary")
    
    print(f"""
  Hermes Home:   {HERMES_HOME}
  Config:        {HERMES_CONFIG}
  Env File:      {HERMES_ENV}
  Lock Dir:      {LOCK_DIR}
  PID File:      {PID_FILE}
  State File:    {STATE_FILE}
  Platform:      {sys.platform}
  Python:        {sys.version.split()[0]}
  
  Quick Fixes:
    • Clean stale locks:     rm -rf {LOCK_DIR}
    • Restart gateway:       hermes gateway run --replace
    • Full diagnostics:      hermes doctor
    • Check Windows env:     [Environment]::GetEnvironmentVariable("OPENAI_API_KEY", "User")
    • Remove Windows env:    [Environment]::SetEnvironmentVariable("OPENAI_API_KEY", $null, "User")
""")


# ─── Main ────────────────────────────────────────────────────

args = None

def main():
    global args
    parser = argparse.ArgumentParser(description="Hermes Health Check & Auto-Fix")
    parser.add_argument("--deep", action="store_true", help="Deep check (API validation)")
    parser.add_argument("--fix", action="store_true", help="Auto-fix issues")
    args = parser.parse_args()
    
    print(f"\n{BOLD}Hermes Health Check{RESET}")
    print(f"{DIM}{'=' * 50}{RESET}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if args.fix:
        print(f"{YELLOW}Mode: CHECK + AUTO-FIX{RESET}")
    elif args.deep:
        print(f"Mode: DEEP CHECK")
    else:
        print(f"Mode: BASIC CHECK")
    
    check_env_file()
    check_env_vars()
    check_windows_env_vars()
    check_lock_files()
    check_gateway_status()
    check_proxy_config()
    check_lark_oapi_version()
    
    if args.deep:
        check_deep_api()
    
    generate_report()


if __name__ == "__main__":
    main()
