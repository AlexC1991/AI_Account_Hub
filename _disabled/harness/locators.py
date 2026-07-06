"""Executable locators and small process utilities: Cursor agent / Antigravity CLI discovery, a version probe, and wait_until."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

_logger = logging.getLogger("native_harness")

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

def locate_cursor_agent() -> str:
    local_appdata = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    candidates = [
        shutil.which("cursor-agent.exe"),
        shutil.which("cursor-agent.cmd"),
        shutil.which("cursor-agent"),
        shutil.which("agent.exe"),
        shutil.which("agent.cmd"),
        shutil.which("agent"),
        str(local_appdata / "cursor-agent" / "cursor-agent.cmd"),
        str(local_appdata / "cursor-agent" / "agent.cmd"),
        str(local_appdata / "cursor-agent" / "cursor-agent.exe"),
        str(Path.home() / ".local" / "bin" / "cursor-agent.exe"),
        str(Path.home() / ".local" / "bin" / "cursor-agent"),
        str(Path.home() / ".local" / "bin" / "agent.exe"),
        str(Path.home() / ".local" / "bin" / "agent"),
        str(Path.home() / ".cursor" / "bin" / "cursor-agent.exe"),
        str(Path.home() / ".cursor" / "bin" / "agent.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    return ""


def locate_antigravity_cli() -> str:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe",
        Path.home() / "AppData" / "Local" / "agy" / "bin" / "agy.exe",
        Path(shutil.which("agy.exe") or shutil.which("agy") or ""),
    ]
    for candidate in candidates:
        if str(candidate) and candidate.is_file():
            return str(candidate)
    return ""


def executable_version(executable: str, timeout: float = 8) -> str:
    if not executable or not Path(executable).exists():
        return ""
    try:
        result = subprocess.run(
            [executable, "--version"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout.strip() or result.stderr.strip()).splitlines()[0]


def wait_until(predicate: Callable[[], bool], timeout: float = 5, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


__all__ = [
    "locate_cursor_agent",
    "locate_antigravity_cli",
    "executable_version",
    "wait_until",
]
