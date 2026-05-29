"""Shared debug logging helpers for local LLM/pipeline diagnosis."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

from .config import get_settings


def debug_enabled() -> bool:
    return bool(get_settings().llm_debug_log)


def debug_log_path() -> Path:
    return Path(get_settings().llm_debug_log_file)


def write_debug_log(text: str) -> None:
    if not debug_enabled():
        return
    path = debug_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(f"\n[{stamp}]\n{text.rstrip()}\n")


def announce_debug_log_path() -> None:
    if not debug_enabled():
        return
    print(f"LLM debug log file: {debug_log_path()}", file=sys.stderr, flush=True)
