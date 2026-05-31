"""Build a sanitized RoamMind release package.

The release package is intentionally runtime-configured: it contains the
backend app, a prebuilt frontend, helper scripts, and an env template, but no
real API keys or local .env files.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path


VERSION = os.environ.get("ROAMMIND_VERSION", "0.1.0")
ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
BACKEND = ROOT / "backend"
DOCS = ROOT / "docs"
RELEASE_ROOT = ROOT / "dist_release"
PACKAGE_DIR = RELEASE_ROOT / f"RoamMind-{VERSION}"


ENV_EXAMPLE = """# RoamMind runtime config
# Copy this file to config/roammind.env and fill in your own keys.
# Do not put real keys into the release package or source control.

# --- LLM (OpenAI-compatible; backend only) ---------------------------------
LLM_PROVIDER=deepseek
LLM_API_KEY=
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat

# Legacy DeepSeek names are still supported.
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

# --- AMap Web Service API (backend only) -----------------------------------
# Used for POI search, routes, geocoding, and reverse geocoding.
AMAP_WEB_SERVICE_KEY=

# --- AMap JS API (public browser-side config) ------------------------------
# Used only to render the live browser map. Leave empty to use the SVG fallback.
VITE_AMAP_JS_KEY=
VITE_AMAP_JS_SECURITY_CODE=

# --- Runtime knobs ----------------------------------------------------------
DEFAULT_CITY=北京
CORS_ORIGINS=*
LLM_DEBUG_LOG=false
"""


INSTALL_WINDOWS = r"""param()

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$VenvDir = Join-Path $PSScriptRoot ".venv"
if (!(Test-Path $VenvDir)) {
  python -m venv $VenvDir
}

$Python = Join-Path $VenvDir "Scripts\python.exe"
& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $PSScriptRoot "backend\requirements.txt")

Write-Host ""
Write-Host "RoamMind dependencies installed."
Write-Host "Next: copy config\roammind.env.example to config\roammind.env, fill keys, then run .\start_roammind.ps1"
"""


START_WINDOWS = r"""param(
  [string]$ConfigPath = "",
  [int]$Port = 8000,
  [string]$HostAddress = "0.0.0.0"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
  $ConfigPath = Join-Path $PSScriptRoot "config\roammind.env"
}

if (!(Test-Path $ConfigPath)) {
  $Example = Join-Path $PSScriptRoot "config\roammind.env.example"
  Copy-Item $Example $ConfigPath
  Write-Host "Created config file: $ConfigPath"
  Write-Host "Please edit it with your LLM/AMap keys, then run this script again."
  exit 1
}

$ResolvedConfig = (Resolve-Path $ConfigPath).Path
$env:ROAMMIND_ENV_FILE = $ResolvedConfig
$env:PYTHONPATH = $PSScriptRoot

$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (!(Test-Path $Python)) {
  $Python = "python"
}

Write-Host "Using config: $ResolvedConfig"
Write-Host "Open http://localhost:$Port after the server starts."
& $Python -m uvicorn backend.app.main:app --host $HostAddress --port $Port
"""


INSTALL_UNIX = """#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r backend/requirements.txt

echo
echo "RoamMind dependencies installed."
echo "Next: cp config/roammind.env.example config/roammind.env, fill keys, then run ./start_roammind.sh"
"""


START_UNIX = """#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CONFIG_PATH="${ROAMMIND_ENV_FILE:-$SCRIPT_DIR/config/roammind.env}"
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"

if [[ ! -f "$CONFIG_PATH" ]]; then
  cp "$SCRIPT_DIR/config/roammind.env.example" "$CONFIG_PATH"
  echo "Created config file: $CONFIG_PATH"
  echo "Please edit it with your LLM/AMap keys, then run this script again."
  exit 1
fi

export ROAMMIND_ENV_FILE="$CONFIG_PATH"
export PYTHONPATH="$SCRIPT_DIR"

PYTHON="$SCRIPT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="python3"
fi

echo "Using config: $CONFIG_PATH"
echo "Open http://localhost:$PORT after the server starts."
exec "$PYTHON" -m uvicorn backend.app.main:app --host "$HOST" --port "$PORT"
"""


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    print(f"$ {' '.join(cmd)}  (cwd={cwd})")
    use_shell = os.name == "nt" and cmd[0].lower().endswith(".cmd")
    args: list[str] | str = subprocess.list2cmdline(cmd) if use_shell else cmd
    subprocess.run(args, cwd=cwd, env=env, check=True, shell=use_shell)


def command(name: str) -> str:
    found = None
    if os.name == "nt" and name == "npm":
        for candidate in (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "npm.cmd",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "nodejs" / "npm.cmd",
        ):
            if candidate.exists():
                found = str(candidate)
                break
    if os.name == "nt" and name == "node":
        for candidate in (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "node.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "nodejs" / "node.exe",
        ):
            if candidate.exists():
                found = str(candidate)
                break
    if not found:
        found = shutil.which(name)
    if not found and os.name == "nt":
        found = shutil.which(f"{name}.cmd")
    if not found:
        raise SystemExit(f"Missing required command: {name}")
    return found


def copytree(src: Path, dst: Path) -> None:
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "*.log"),
    )


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def build_frontend() -> None:
    env = os.environ.copy()
    env["VITE_API_BASE_URL"] = ""
    env["VITE_AMAP_JS_KEY"] = ""
    env["VITE_AMAP_JS_SECURITY_CODE"] = ""

    if not (FRONTEND / "node_modules").is_dir():
        run([command("npm"), "install"], cwd=FRONTEND)
    node = command("node")
    run([node, "node_modules/typescript/bin/tsc", "--noEmit"], cwd=FRONTEND, env=env)
    run([node, "node_modules/vite/bin/vite.js", "build"], cwd=FRONTEND, env=env)


def make_package() -> Path:
    if RELEASE_ROOT.exists():
        shutil.rmtree(RELEASE_ROOT)
    PACKAGE_DIR.mkdir(parents=True)

    copytree(BACKEND / "app", PACKAGE_DIR / "backend" / "app")
    shutil.copy2(BACKEND / "requirements.txt", PACKAGE_DIR / "backend" / "requirements.txt")
    (PACKAGE_DIR / "backend" / "__init__.py").write_text("", encoding="utf-8")

    copytree(FRONTEND / "dist", PACKAGE_DIR / "frontend" / "dist")

    config_dir = PACKAGE_DIR / "config"
    config_dir.mkdir()
    (config_dir / "roammind.env.example").write_text(ENV_EXAMPLE, encoding="utf-8", newline="\n")

    shutil.copy2(DOCS / "RELEASE_RUN_GUIDE.md", PACKAGE_DIR / "README_RUN.md")
    (PACKAGE_DIR / "VERSION").write_text(f"{VERSION}\n", encoding="utf-8")

    (PACKAGE_DIR / "install_windows.ps1").write_text(INSTALL_WINDOWS, encoding="utf-8", newline="\r\n")
    (PACKAGE_DIR / "start_roammind.ps1").write_text(START_WINDOWS, encoding="utf-8", newline="\r\n")
    write_executable(PACKAGE_DIR / "install_unix.sh", INSTALL_UNIX)
    write_executable(PACKAGE_DIR / "start_roammind.sh", START_UNIX)

    archive_base = RELEASE_ROOT / PACKAGE_DIR.name
    archive_path = Path(shutil.make_archive(str(archive_base), "zip", RELEASE_ROOT, PACKAGE_DIR.name))
    return archive_path


def main() -> None:
    build_frontend()
    archive_path = make_package()
    print()
    print(f"Release directory: {PACKAGE_DIR}")
    print(f"Release archive:   {archive_path}")


if __name__ == "__main__":
    main()
