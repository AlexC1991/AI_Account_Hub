from __future__ import annotations

import base64
import calendar
import ctypes
import datetime as dt
import hashlib
import http.server
import json
import logging
import math
import os
import queue
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import tomllib
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk
from tkinter import ttk
from urllib.parse import urlparse

MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))
CLAUDE_PERMISSION_BRIDGE_PATH = MODULE_DIR / "claude_permission_bridge.py"

_logger = logging.getLogger(__name__)

from native_harness import (  # noqa: E402
    AntigravityTransport,
    CodexTransport,
    NativeTransportError,
    StreamJsonTransport,
    claude_content_image_refs,
    claude_tool_activity_fields,
    claude_tool_result_fields,
    claude_tool_result_text,
    clean_windows_path_text,
    compact_history_text,
    codex_thread_messages,
    discover_codex_file_threads,
    discover_antigravity_threads,
    discover_claude_threads,
    discover_cursor_threads,
    extract_message_text,
    load_codex_saved_workspaces,
    load_thread_refs,
    locate_cursor_agent,
    normalized_path_key,
    read_antigravity_thread,
    read_claude_thread,
    read_codex_session_file,
    read_cursor_thread,
    strip_ansi,
    summarize_codex_item,
    thread_ref,
    upsert_thread_ref,
)


BG = "#edf2ef"
PANEL = "#ffffff"
PANEL_ALT = "#f8faf8"
INK = "#17211c"
MUTED = "#647269"
LINE = "#d8e0da"
LINE_STRONG = "#b8c7bf"
GREEN = "#2b7c4b"
GREEN_SOFT = "#e0f3e7"
RED = "#b42318"
RED_SOFT = "#ffe5e3"
AMBER = "#9a5d00"
AMBER_SOFT = "#fff0cd"
BLUE = "#236f95"
BLUE_SOFT = "#e2f1f7"
DARK = "#1c2922"

SCRIPT_DIR = MODULE_DIR
OUTPUTS_DIR = SCRIPT_DIR.parent
HELPER_PATH = OUTPUTS_DIR / "codex-account-limits-helper.mjs"
LAUNCHER_ROOT = Path.home() / ".codex-account-launcher"
PROFILES_FILE = LAUNCHER_ROOT / "profiles.json"
DESKTOP_BACKUP_ROOT = LAUNCHER_ROOT / "desktop-default-backup"
DESKTOP_ACTIVE_PROFILE_PATH = LAUNCHER_ROOT / "desktop-active-profile.json"
SETTINGS_FILE = LAUNCHER_ROOT / "ai-hub-calendar-settings.json"
HISTORY_DB_FILE = LAUNCHER_ROOT / "ai-hub-history.sqlite3"
NATIVE_THREADS_FILE = LAUNCHER_ROOT / "native-threads.json"
BROWSER_PROFILES_ROOT = LAUNCHER_ROOT / "browser-profiles"
DEFAULT_ACCOUNTS_ROOT = Path.home() / ".codex-accounts"
HUB_ACCOUNTS_ROOT = Path.home() / ".ai-account-hub"
DEFAULT_CODEX_HOME = Path.home() / ".codex"
DEFAULT_WORKSPACE = Path.home() / "Documents" / "Codex"
CLAUDE_CLI_HOME = Path.home() / ".claude"
CLAUDE_PROJECTS_ROOT = CLAUDE_CLI_HOME / "projects"
APPDATA_ROOT = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
LOCALAPPDATA_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
PROGRAMFILES_ROOT = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
PROGRAMFILES_X86_ROOT = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
CLAUDE_ROAMING_HOME = APPDATA_ROOT / "Claude"
CURSOR_ROAMING_HOME = APPDATA_ROOT / "Cursor"
CURSOR_HOME = Path.home() / ".cursor"
ANTIGRAVITY_ROAMING_HOME = APPDATA_ROOT / "Antigravity"
ANTIGRAVITY_HOME = Path.home() / ".antigravity"
ANTIGRAVITY_PROGRAM_DIR = LOCALAPPDATA_ROOT / "Programs" / "Antigravity"

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
STATE_CACHE_SECONDS = 20

_CLAUDE_STATUS_CACHE: dict[str, object] = {"at": None, "value": None}

PROVIDER_COLORS = {
    "codex": "#254c37",
    "claude": "#8a4e2e",
    "cursor": "#242a32",
    "antigravity": "#1f6feb",
    "api": "#19706b",
}

PROVIDER_INITIALS = {
    "codex": "CX",
    "claude": "CL",
    "cursor": "CR",
    "antigravity": "AG",
    "api": "API",
}

PROVIDER_CHOICES = [
    ("Codex", "codex"),
    ("Claude Code", "claude"),
    ("Antigravity", "antigravity"),
    ("Cursor", "cursor"),
]

SORT_CHOICES = ["Manual", "Name", "Provider", "State", "Session left", "Weekly left", "Last refresh"]
CARD_TEMPLATE_CHOICES = ["Balanced", "Compact", "Plan Chips", "Usage First", "Identity"]

ONLINE_LINKS = {
    "codex": [
        {"key": "chat", "label": "ChatGPT", "url": "https://chatgpt.com/"},
        {"key": "billing", "label": "ChatGPT Billing", "url": "https://chatgpt.com/"},
        {"key": "workspace-billing", "label": "Workspace Billing", "url": "https://chatgpt.com/admin/billing"},
        {"key": "api-billing", "label": "API Billing", "url": "https://platform.openai.com/account/billing/overview"},
        {"key": "api-usage", "label": "API Usage", "url": "https://platform.openai.com/usage"},
        {"key": "api-keys", "label": "API Keys", "url": "https://platform.openai.com/api-keys"},
        {"key": "support", "label": "OpenAI Help", "url": "https://help.openai.com/"},
    ],
    "claude": [
        {"key": "chat", "label": "Claude Chat", "url": "https://claude.ai/"},
        {"key": "billing", "label": "Billing", "url": "https://claude.ai/settings/billing"},
        {"key": "usage", "label": "Usage", "url": "https://claude.ai/settings/usage"},
        {"key": "console-billing", "label": "Console Billing", "url": "https://platform.claude.com/settings/billing"},
        {"key": "console-usage", "label": "Console Usage", "url": "https://platform.claude.com/usage"},
        {"key": "console-limits", "label": "Console Limits", "url": "https://platform.claude.com/settings/limits"},
        {"key": "support", "label": "Claude Support", "url": "https://support.claude.com/"},
        {"key": "code-docs", "label": "Code Costs", "url": "https://code.claude.com/docs/en/costs"},
    ],
    "cursor": [
        {"key": "dashboard", "label": "Dashboard", "url": "https://cursor.com/dashboard"},
        {"key": "usage", "label": "Usage Limits", "url": "https://cursor.com/help/models-and-usage/usage-limits"},
        {"key": "pricing", "label": "Models/Pricing", "url": "https://cursor.com/docs/models-and-pricing"},
        {"key": "spend", "label": "Spend Limits", "url": "https://cursor.com/help/account-and-billing/spend-limits"},
        {"key": "docs", "label": "Cursor Docs", "url": "https://cursor.com/docs"},
    ],
    "antigravity": [
        {"key": "home", "label": "Antigravity", "url": "https://antigravity.google/"},
        {"key": "pricing", "label": "Pricing", "url": "https://antigravity.google/pricing"},
        {"key": "plans", "label": "Plans", "url": "https://antigravity.google/docs/plans"},
        {"key": "credits", "label": "AI Credits", "url": "https://antigravity.google/docs/cli/credits"},
        {"key": "cli", "label": "CLI Docs", "url": "https://antigravity.google/docs/cli-overview"},
    ],
}

PROFILE_DEFAULTS = {
    "name": "Account",
    "provider": "codex",
    "codexHome": "",
    "claudeConfigDir": "",
    "workspace": str(DEFAULT_WORKSPACE),
    "browserCommand": "",
    "browserProfileMode": "isolated",
    "browserProfileDir": "",
    "onlineLinks": [],
    "cooldownUntilUtc": "",
    "shortLimitUsedPercent": "",
    "shortLimitResetUtc": "",
    "shortLimitLabel": "5h",
    "weeklyLimitUsedPercent": "",
    "weeklyLimitResetUtc": "",
    "weeklyResetEstimateUtc": "",
    "weeklyResetEstimateSource": "",
    "weeklyLimitLabel": "Weekly",
    "limitReachedType": "",
    "resetCreditsAvailable": "",
    "lastResetOutcome": "",
    "lastResetUtc": "",
    "lastLimitsRefreshUtc": "",
    "lastLimitsError": "",
    "lastUsageError": "",
    "accountName": "",
    "accountEmail": "",
    "accountType": "",
    "accountPlan": "",
    "accountPlanStatus": "",
    "providerVersion": "",
    "usageSummary": {},
    "usageDailyBuckets": [],
}

LIGHT_THEME = {
    "BG": "#edf2ef",
    "PANEL": "#ffffff",
    "PANEL_ALT": "#f8faf8",
    "INK": "#17211c",
    "MUTED": "#647269",
    "LINE": "#d8e0da",
    "LINE_STRONG": "#b8c7bf",
    "GREEN": "#2b7c4b",
    "GREEN_SOFT": "#e0f3e7",
    "RED": "#b42318",
    "RED_SOFT": "#ffe5e3",
    "AMBER": "#9a5d00",
    "AMBER_SOFT": "#fff0cd",
    "BLUE": "#236f95",
    "BLUE_SOFT": "#e2f1f7",
    "DARK": "#1c2922",
}

DARK_THEME = {
    "BG": "#151a1f",
    "PANEL": "#1f272d",
    "PANEL_ALT": "#1a2026",
    "INK": "#e8f0ea",
    "MUTED": "#9cadb4",
    "LINE": "#303b43",
    "LINE_STRONG": "#4b5a63",
    "GREEN": "#69d18f",
    "GREEN_SOFT": "#163322",
    "RED": "#ff8a80",
    "RED_SOFT": "#3b1d1d",
    "AMBER": "#ffd166",
    "AMBER_SOFT": "#352a12",
    "BLUE": "#79c4e8",
    "BLUE_SOFT": "#173140",
    "DARK": "#0f1418",
}

PRIMARY = "#1f6b49"
PRIMARY_HOVER = "#255f45"
CARD_SELECTED = "#f4fbf7"
CARD_SELECTED_DARK = "#20382c"
CARD_HAIRLINE = "#e2e9e4"
CARD_HAIRLINE_DARK = "#334047"
CALENDAR_OUTSIDE = "#f7faf8"
CALENDAR_OUTSIDE_DARK = "#171c21"
CALENDAR_HEADER = "#f8faf8"
CALENDAR_HEADER_DARK = "#192127"
METER_BG = "#e8eee9"
METER_BG_DARK = "#2b353b"


def coding_palette(theme_name: str) -> dict[str, str]:
    if theme_name == "dark":
        return {
            "bg": "#181818",
            "rail": "#202020",
            "panel": "#242424",
            "panel_alt": "#1c1c1c",
            "active": "#2b2b2b",
            "field": "#222222",
            "composer": "#232323",
            "ink": "#f2f2f2",
            "muted": "#a0a0a0",
            "faint": "#747474",
            "line": "#343434",
            "line_strong": "#484848",
        }
    return {
        "bg": "#f6f8f7",
        "rail": "#eef2ef",
        "panel": "#ffffff",
        "panel_alt": "#f8faf8",
        "active": "#e7ede9",
        "field": "#ffffff",
        "composer": "#ffffff",
        "ink": "#17211c",
        "muted": "#647269",
        "faint": "#8a9690",
        "line": "#d8e0da",
        "line_strong": "#b8c7bf",
    }


def apply_theme(theme_name: str) -> None:
    theme = DARK_THEME if theme_name == "dark" else LIGHT_THEME
    globals().update(theme)


def configure_windows_titlebar(window: tk.Tk, theme_name: str) -> None:
    if sys.platform != "win32":
        return
    try:
        window.update_idletasks()
        raw_hwnd = int(window.winfo_id())
        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
        handles = [raw_hwnd]
        for candidate in (user32.GetParent(raw_hwnd), user32.GetAncestor(raw_hwnd, 2)):
            candidate_int = int(candidate or 0)
            if candidate_int and candidate_int not in handles:
                handles.append(candidate_int)
        enabled = ctypes.c_int(1 if theme_name == "dark" else 0)
        if theme_name == "dark":
            caption = ctypes.c_int(0x001F1A15)
            text = ctypes.c_int(0x00F3F5F2)
        else:
            caption = ctypes.c_int(0x00EFF2ED)
            text = ctypes.c_int(0x001C2117)
        for handle in handles:
            hwnd = ctypes.c_void_p(handle)
            for attr in (20, 19):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    ctypes.c_int(attr),
                    ctypes.byref(enabled),
                    ctypes.sizeof(enabled),
                )
            for attr, value in ((35, caption), (36, text)):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    ctypes.c_int(attr),
                    ctypes.byref(value),
                    ctypes.sizeof(value),
                )
    except Exception:
        return


def load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {"theme": "light", "autoRefreshEnabled": True, "autoRefreshMinutes": 10, "sortMode": "Manual", "cardTemplate": "Balanced"}
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"theme": "light", "autoRefreshEnabled": True, "autoRefreshMinutes": 10, "sortMode": "Manual", "cardTemplate": "Balanced"}
    if not isinstance(raw, dict):
        return {"theme": "light", "autoRefreshEnabled": True, "autoRefreshMinutes": 10, "sortMode": "Manual", "cardTemplate": "Balanced"}
    raw.setdefault("theme", "light")
    raw.setdefault("autoRefreshEnabled", True)
    raw.setdefault("autoRefreshMinutes", 10)
    raw.setdefault("sortMode", "Manual")
    raw.setdefault("cardTemplate", "Balanced")
    if raw.get("sortMode") == "5h left":
        raw["sortMode"] = "Session left"
    if raw.get("cardTemplate") not in CARD_TEMPLATE_CHOICES:
        raw["cardTemplate"] = "Balanced"
    return raw


def save_settings(settings: dict) -> None:
    LAUNCHER_ROOT.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def compact_number(value: int | float | None) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "-" if number < 0 else ""
    number = abs(number)
    if number >= 1_000_000:
        amount = number / 1_000_000
        text = f"{amount:.0f}M" if amount >= 10 else f"{amount:.1f}M"
        return f"{sign}{text}"
    if number >= 1_000:
        return f"{sign}{number / 1_000:.0f}K"
    return f"{sign}{int(number)}"


def native_token_usage_label(usage: object) -> str:
    if not isinstance(usage, dict):
        return "-"

    def find_total(value: object) -> int | None:
        if not isinstance(value, dict):
            return None
        for key in ("totalTokens", "total_tokens"):
            number = sanitize_float(value.get(key))
            if number is not None:
                return int(number)
        for key in ("total", "last"):
            nested = find_total(value.get(key))
            if nested is not None:
                return nested
        input_tokens = sanitize_float(value.get("inputTokens") or value.get("input_tokens"))
        output_tokens = sanitize_float(value.get("outputTokens") or value.get("output_tokens"))
        if input_tokens is not None or output_tokens is not None:
            return int((input_tokens or 0) + (output_tokens or 0))
        return None

    total = find_total(usage)
    return compact_number(total) if total is not None else "-"


def format_codex_plan_update(plan: object, explanation: object = "") -> str:
    lines: list[str] = []
    if str(explanation or "").strip():
        lines.append(str(explanation).strip())
    entries = plan if isinstance(plan, list) else []
    status_labels = {
        "completed": "done",
        "complete": "done",
        "inProgress": "active",
        "in_progress": "active",
        "pending": "todo",
    }
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        step = str(entry.get("step") or entry.get("text") or "").strip()
        if not step:
            continue
        status = status_labels.get(str(entry.get("status") or "pending"), str(entry.get("status") or "todo"))
        lines.append(f"[{status}] {step}")
    if not lines and isinstance(plan, dict):
        for key, value in plan.items():
            lines.append(f"{key}: {value}")
    if not lines and str(plan or "").strip():
        lines.append(str(plan).strip())
    return "Plan\n" + "\n".join(lines) if lines else ""


def format_minutes(minutes: int | float | None) -> str:
    if minutes is None:
        return "-"
    try:
        total = int(round(float(minutes)))
    except (TypeError, ValueError):
        return "-"
    if total <= 0:
        return "0m"
    hours = total // 60
    mins = total % 60
    if hours:
        return f"{hours}h {mins:02d}m"
    return f"{mins}m"


def month_days(year: int, month: int) -> list[dt.date]:
    first = dt.date(year, month, 1)
    start = first - dt.timedelta(days=(first.weekday() + 1) % 7)
    return [start + dt.timedelta(days=i) for i in range(42)]


def sanitize_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def percent_left(used_value: object) -> float | None:
    used = sanitize_float(used_value)
    if used is None:
        return None
    return max(0.0, min(100.0, 100.0 - used))


def format_percent(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.0f}%" if value == round(value) else f"{value:.1f}%"


def is_limit_exhausted(value: object) -> bool:
    used = sanitize_float(value)
    return used is not None and used >= 100.0


def is_revoked_token_message(text: object) -> bool:
    haystack = str(text or "").lower()
    return "refresh token was revoked" in haystack or "access token could not be refreshed" in haystack


def is_not_logged_in_message(text: object) -> bool:
    haystack = str(text or "").lower()
    return "not logged in" in haystack or "not authenticated" in haystack


def redact_auth_output(text: object) -> str:
    redacted = str(text or "")
    redacted = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[email redacted]", redacted)
    redacted = re.sub(
        r"(?i)\b(access[_-]?token|refresh[_-]?token|api[_-]?key|authorization|bearer|sessionkey|secret)([\"'\s:=]+)([^\s\"',}]+)",
        r"\1\2[redacted]",
        redacted,
    )
    return redacted


def mark_auth_error(profile: dict, message: str) -> None:
    profile["lastLimitsError"] = message
    profile["limitReachedType"] = "auth_error"


def parse_iso_datetime(raw: object) -> dt.datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+([+-]\d\d:\d\d)$", r"\1\2", text)
    text = re.sub(r"(\.\d{6})\d+$", r"\1", text)
    try:
        value = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def iso_utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def iso_from_value(value: object) -> str:
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return "" if value is None else str(value)
    return parsed.isoformat()


def format_countdown(raw: object) -> str:
    reset = parse_iso_datetime(raw)
    if reset is None:
        return "-"
    remaining = reset - dt.datetime.now(dt.timezone.utc)
    seconds = remaining.total_seconds()
    if seconds <= 0:
        return "now"
    if remaining.days >= 1:
        return f"{remaining.days}d {remaining.seconds // 3600:02d}h"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if hours >= 1:
        return f"{hours}h {minutes:02d}m"
    if minutes >= 1:
        return f"{minutes}m"
    return "<1m"


def local_datetime_label(raw: object) -> str:
    parsed = parse_iso_datetime(raw)
    if parsed is None:
        return "-"
    local = parsed.astimezone()
    return local.strftime("%Y-%m-%d %H:%M")


def profile_id(profile: dict) -> str:
    explicit = str(profile.get("id") or "").strip()
    if explicit:
        return explicit
    home = str(profile.get("codexHome", "")).strip()
    return home or str(profile.get("name", "Account"))


def provider_key(profile: dict) -> str:
    raw = str(profile.get("provider") or "codex").strip().lower().replace("_", "-")
    aliases = {
        "openai": "codex",
        "openai-codex": "codex",
        "codex-cli": "codex",
        "claude-code": "claude",
        "anthropic": "claude",
        "google-antigravity": "antigravity",
        "antigravity-2": "antigravity",
        "antigravity-2.0": "antigravity",
        "gemini": "antigravity",
        "google": "antigravity",
        "google-gemini": "antigravity",
        "cursor-ai": "cursor",
        "curser": "cursor",
    }
    return aliases.get(raw, raw)


def same_local_path(first: object, second: object) -> bool:
    try:
        left = os.path.normcase(os.path.abspath(os.path.expanduser(str(first))))
        right = os.path.normcase(os.path.abspath(os.path.expanduser(str(second))))
    except (OSError, TypeError, ValueError):
        return False
    return left == right


def claude_profile_home(profile: dict) -> Path:
    explicit = str(profile.get("claudeConfigDir") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    legacy_home = str(profile.get("codexHome") or "").strip()
    if legacy_home and not same_local_path(legacy_home, CLAUDE_ROAMING_HOME):
        return Path(legacy_home).expanduser()
    return CLAUDE_CLI_HOME


def provider_label(profile: dict) -> str:
    provider = provider_key(profile)
    return {
        "codex": "Codex",
        "claude": "Claude Code",
        "antigravity": "Antigravity",
        "cursor": "Cursor",
    }.get(provider, provider.title() or "Account")


def provider_capability(profile: dict) -> dict[str, str]:
    provider = provider_key(profile)
    if provider == "codex":
        has_limits = bool(str(profile.get("shortLimitUsedPercent") or "").strip() or str(profile.get("weeklyLimitUsedPercent") or "").strip())
        has_usage = bool(profile.get("usageDailyBuckets"))
        if has_limits and has_usage:
            return {"label": "Real limits + usage", "state": "ready", "detail": "Codex app-server exposes rate limits and daily usage buckets."}
        if has_limits:
            return {"label": "Real limits", "state": "ready", "detail": "Codex app-server exposes rate limits; usage buckets are not available yet."}
        return {"label": "Refresh needed", "state": "login", "detail": "Use Refresh to read Codex app-server rate limits and usage."}
    if provider == "claude":
        has_limit_text = bool(str(profile.get("weeklyLimitUsedPercent") or "").strip() or str(profile.get("shortLimitUsedPercent") or "").strip())
        has_usage = bool(profile.get("usageDailyBuckets"))
        if has_limit_text and has_usage:
            return {"label": "Parsed limits + local usage", "state": "ready", "detail": "Claude Code /usage supplies limits; local JSONL logs supply usage buckets."}
        if has_limit_text:
            return {"label": "Parsed limits", "state": "ready", "detail": "Claude Code /usage supplies limits, but local usage buckets are empty."}
        return {"label": "Login metadata", "state": "login", "detail": "Claude Desktop/Code login is detected, but usage limits have not been parsed yet."}
    if provider in {"cursor", "antigravity"}:
        state = effective_state(profile)
        if state == "login":
            return {"label": "Login required", "state": "login", "detail": f"{provider_label(profile)} login was not detected."}
        return {"label": "Local metadata", "state": "ready", "detail": f"{provider_label(profile)} exposes local account metadata, not limits or usage buckets."}
    return {"label": "Provider pending", "state": "idle", "detail": "This provider does not have a usage integration yet."}


def combined_limit_left_text(profiles: list[dict], used_field: str) -> str:
    values = [percent_left(profile.get(used_field)) for profile in profiles]
    known = [value for value in values if value is not None]
    if not known:
        return "-"
    return f"{format_percent(sum(known))} / {format_percent(len(known) * 100)}"


def is_safe_online_url(url: object) -> bool:
    parsed = urlparse(str(url or "").strip())
    return parsed.scheme in {"https", "http"} and bool(parsed.netloc)


def normalized_online_link(raw: object, fallback_key: str = "custom") -> dict | None:
    if isinstance(raw, dict):
        label = str(raw.get("label") or raw.get("name") or fallback_key).strip()
        url = str(raw.get("url") or raw.get("href") or "").strip()
        key = str(raw.get("key") or fallback_key).strip() or fallback_key
    else:
        text = str(raw or "").strip()
        if not text:
            return None
        label = fallback_key
        url = text
        for separator in ("|", "="):
            if separator in text:
                left, right = text.split(separator, 1)
                label = left.strip() or fallback_key
                url = right.strip()
                break
        key = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or fallback_key
    if not label or not is_safe_online_url(url):
        return None
    return {"key": key, "label": label, "url": url}


def parse_custom_online_links_text(text: object) -> list[dict]:
    links: list[dict] = []
    for index, line in enumerate(str(text or "").splitlines(), start=1):
        link = normalized_online_link(line, fallback_key=f"custom-{index}")
        if link is not None:
            links.append(link)
    return links


def serialize_online_links_text(links: object) -> str:
    if not isinstance(links, list):
        return ""
    rows = []
    for index, raw in enumerate(links, start=1):
        link = normalized_online_link(raw, fallback_key=f"custom-{index}")
        if link is not None:
            rows.append(f"{link['label']} | {link['url']}")
    return "\n".join(rows)


def online_links_for_profile(profile: dict) -> list[dict]:
    provider = provider_key(profile)
    merged: list[dict] = []
    seen: set[str] = set()
    for index, raw in enumerate(ONLINE_LINKS.get(provider, []), start=1):
        link = normalized_online_link(raw, fallback_key=f"{provider}-{index}")
        if link is None:
            continue
        seen.add(link["key"])
        merged.append(link)
    custom_links = profile.get("onlineLinks")
    if isinstance(custom_links, str):
        custom = parse_custom_online_links_text(custom_links)
    elif isinstance(custom_links, list):
        custom = [link for index, raw in enumerate(custom_links, start=1) if (link := normalized_online_link(raw, fallback_key=f"custom-{index}"))]
    else:
        custom = []
    for link in custom:
        key = link["key"]
        if key in seen:
            key = f"custom-{key}"
            link = dict(link)
            link["key"] = key
        seen.add(key)
        merged.append(link)
    return merged


def browser_command_for_url(profile: dict, url: str) -> str:
    command = str(profile.get("browserCommand") or "").strip()
    if not command:
        return ""
    quoted_url = '"' + url.replace('"', "%22") + '"'
    if "{url}" in command:
        return command.replace("{url}", quoted_url)
    return f"{command} {quoted_url}"


def browser_profile_mode(profile: dict) -> str:
    mode = str(profile.get("browserProfileMode") or "isolated").strip().lower()
    if mode in {"system", "default", "off", "disabled", "none"}:
        return "system"
    return "isolated"


def browser_profile_dir_for_profile(profile: dict) -> Path:
    explicit = str(profile.get("browserProfileDir") or "").strip()
    if explicit:
        return Path(os.path.expandvars(os.path.expanduser(explicit)))
    name_slug = re.sub(r"[^a-z0-9]+", "-", str(profile.get("name") or "account").lower()).strip("-") or "account"
    name_slug = name_slug[:36].strip("-") or "account"
    digest = hashlib.sha256(profile_id(profile).encode("utf-8", "replace")).hexdigest()[:10]
    return BROWSER_PROFILES_ROOT / f"{name_slug}-{digest}"


def browser_profile_launch_args(profile: dict, url: str, browser_path: str) -> list[str]:
    profile_dir = browser_profile_dir_for_profile(profile)
    return [
        browser_path,
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--new-window",
        url,
    ]


def uses_isolated_browser_profile(profile: dict) -> bool:
    return browser_profile_mode(profile) == "isolated" and not str(profile.get("browserCommand") or "").strip()


def browser_profile_cookie_db_paths(profile: dict) -> list[Path]:
    root = browser_profile_dir_for_profile(profile)
    return [
        root / "Default" / "Network" / "Cookies",
        root / "Default" / "Cookies",
        root / "Network" / "Cookies",
        root / "Cookies",
    ]


def browser_profile_has_cookie_for_url(profile: dict, url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    host = parsed.netloc.lower()
    if not host:
        return False
    host = host.split("@")[-1].split(":")[0]
    parts = host.split(".")
    root_domain = ".".join(parts[-2:]) if len(parts) >= 2 else host
    for db_path in browser_profile_cookie_db_paths(profile):
        if not db_path.exists():
            continue
        try:
            connection = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True, timeout=1)
            try:
                row = connection.execute("select 1 from cookies where host_key like ? limit 1", (f"%{root_domain}",)).fetchone()
            finally:
                connection.close()
            if row:
                return True
        except sqlite3.Error:
            continue
    return False


def browser_profile_web_login_label(profile: dict, links: list[dict] | None = None) -> str:
    if str(profile.get("browserCommand") or "").strip():
        return "Custom browser"
    if browser_profile_mode(profile) == "system":
        return "System browser"
    link_items = links if links is not None else online_links_for_profile(profile)
    has_cookie = any(browser_profile_has_cookie_for_url(profile, str(link.get("url") or "")) for link in link_items)
    return "Web login saved" if has_cookie else "Web login needed"


def parse_json_object_from_text(text: object) -> dict:
    raw = str(text or "").strip()
    if not raw:
        return {}
    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def parse_claude_auth_status_text(text: object) -> dict:
    payload = parse_json_object_from_text(text)
    if not payload:
        return {}
    return {
        "loggedIn": payload.get("loggedIn"),
        "authMethod": payload.get("authMethod") or "",
        "apiProvider": payload.get("apiProvider") or "",
        "email": payload.get("email") or "",
        "orgId": payload.get("orgId") or "",
        "orgName": payload.get("orgName") or "",
        "subscriptionType": payload.get("subscriptionType") or payload.get("planType") or payload.get("plan") or "",
    }


def display_plan_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    aliases = {
        "free": "Free",
        "pro": "Pro",
        "team": "Team",
        "teams": "Teams",
        "enterprise": "Enterprise",
        "business": "Business",
        "paid": "Paid",
        "unpaid": "Unpaid",
        "trial": "Trial",
        "unknown": "",
    }
    key = text.lower().replace("_", "-").replace(" ", "-")
    if key in aliases:
        return aliases[key]
    return re.sub(r"[-_]+", " ", text).strip().title()


def clip_text(value: object, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if max_chars <= 1 or len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)].rstrip() + "..."


def coding_user_message_parts(value: object) -> tuple[str, list[str]]:
    text = str(value or "").replace("\r\n", "\n").strip()
    attachments: list[str] = []
    request_marker = "## My request for Codex:"
    if "# Files mentioned by the user:" in text:
        prefix, separator, request = text.partition(request_marker)
        for match in re.finditer(r"(?m)^##\s+([^:\n]+):\s+(.+)$", prefix):
            name = match.group(1).strip()
            if name and name not in attachments:
                attachments.append(name)
        text = request.strip() if separator else re.sub(
            r"(?ms)^# Files mentioned by the user:.*?(?=^#|\Z)",
            "",
            text,
        ).strip()
    text = re.sub(r"(?is)<image\b[^>]*>.*?</image>", "", text)
    text = re.sub(r"(?is)<image\b[^>]*/?>", "", text)
    return text.strip(), attachments


def htmlish_attr(source: str, name: str) -> str:
    match = re.search(rf"""{name}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", source, flags=re.I)
    if not match:
        return ""
    return next((group for group in match.groups() if group is not None), "").strip()


def markdown_image_refs(value: object) -> list[dict]:
    text = str(value or "")
    refs: list[dict] = []
    pattern = re.compile(r"!\[([^\]]*)\]\((<[^>]+>|[^)]+)\)")
    for match in pattern.finditer(text):
        label = match.group(1).strip()
        target = match.group(2).strip().strip("<>").strip()
        if not target:
            continue
        refs.append(
            {
                "name": label or Path(target).name or "Image",
                "path": target if not target.startswith(("http://", "https://", "data:")) else "",
                "url": target if target.startswith(("http://", "https://")) else "",
                "data": target.split(",", 1)[1] if target.startswith("data:") and "," in target else "",
                "mediaType": target[5:].split(";", 1)[0] if target.startswith("data:") else "",
            }
        )
    return refs


def image_refs_from_transport_text(value: object) -> list[dict]:
    text = str(value or "")
    refs: list[dict] = []
    for match in re.finditer(r"(?is)<image\b([^>]*)>(.*?)</image>|<image\b([^>]*)/?>", text):
        attrs = match.group(1) or match.group(3) or ""
        name = htmlish_attr(attrs, "name") or "Image"
        path = clean_windows_path_text(htmlish_attr(attrs, "path"))
        url = htmlish_attr(attrs, "url")
        if path or url:
            refs.append({"name": name, "path": path, "url": url, "data": "", "mediaType": ""})
    refs.extend(markdown_image_refs(text))
    unique: list[dict] = []
    seen: set[str] = set()
    for ref in refs:
        key = str(ref.get("path") or ref.get("url") or ref.get("data") or ref.get("name") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return unique


def coding_user_message_details(value: object) -> tuple[str, list[dict]]:
    raw = str(value or "").replace("\r\n", "\n").strip()
    body, attachment_names = coding_user_message_parts(raw)
    body = re.sub(r"!\[[^\]]*\]\((<[^>]+>|[^)]+)\)", "", body).strip()
    refs: list[dict] = []
    prefix = raw.partition("## My request for Codex:")[0]
    for match in re.finditer(r"(?m)^##\s+([^:\n]+):\s+(.+)$", prefix):
        name = match.group(1).strip()
        path = clean_windows_path_text(match.group(2).strip())
        if name:
            refs.append({"name": name, "path": path, "url": "", "data": "", "mediaType": ""})
    refs.extend(image_refs_from_transport_text(raw))
    known = {str(ref.get("name") or "").lower() for ref in refs if str(ref.get("name") or "").strip()}
    for name in attachment_names:
        if name.lower() not in known:
            refs.append({"name": name, "path": "", "url": "", "data": "", "mediaType": ""})
    unique: list[dict] = []
    seen: set[str] = set()
    for ref in refs:
        key = str(ref.get("path") or ref.get("url") or ref.get("data") or ref.get("name") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return body, unique


CODING_IMAGE_ATTACHMENT_SUFFIXES = {".png", ".gif", ".webp", ".jpg", ".jpeg"}
CODING_SIDEBAR_SELECTED_THREAD_PREVIEW_LIMIT = 7
CODING_SIDEBAR_COLLAPSED_THREAD_PREVIEW_LIMIT = 3
CODING_NATIVE_RENDER_DELAY_MS = 150
CODING_NATIVE_FULL_RENDER_DELAY_MS = 90
CODING_COMMAND_OUTPUT_LIMIT = 1600
CODING_ACTIVITY_TEXT_LIMIT = 1200


def coding_display_text(value: object) -> str:
    text = strip_ansi("" if value is None else str(value))
    return text.replace("\r\n", "\n").replace("\r", "\n")


def coding_compact_display_text(value: object, limit: int = CODING_ACTIVITY_TEXT_LIMIT) -> str:
    return compact_history_text(coding_display_text(value).strip(), limit=limit)


def coding_command_display_line(command: str) -> str:
    text = coding_display_text(command).strip()
    match = re.search(r"(?:^|\s)-Command\s+(.+)$", text, flags=re.I)
    if match:
        candidate = match.group(1).strip()
        if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {"'", '"'}:
            candidate = candidate[1:-1].strip()
        if candidate:
            return candidate
    return text


def coding_command_activity_parts(value: object) -> tuple[str, str, str]:
    lines = [line.rstrip() for line in coding_display_text(value).strip().splitlines()]
    lines = [line for line in lines if line.strip()]
    if not lines:
        return "", "", ""
    command = ""
    details = ""
    output_lines: list[str] = []
    if lines[0].strip().lower() == "output":
        output_lines = lines[1:]
    else:
        command = coding_command_display_line(lines[0])
        rest = lines[1:]
        if rest and rest[0].strip().lower() != "output":
            details = rest[0].strip()
            rest = rest[1:]
        if rest and rest[0].strip().lower() == "output":
            rest = rest[1:]
        output_lines = rest
    output = "\n".join(output_lines).strip()
    return command, details, output


def native_attachment_kind(path: Path) -> str:
    return "image" if Path(path).suffix.lower() in CODING_IMAGE_ATTACHMENT_SUFFIXES else "file"


def native_attachment_size_label(path: Path) -> str:
    try:
        size = Path(path).stat().st_size
    except OSError:
        return ""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def native_attachment_status_text(attachments: list[Path]) -> str:
    count = len(attachments)
    if count == 0:
        return "Native passthrough"
    return f"{count} attachment{'s' if count != 1 else ''} staged"


def native_attachment_prompt(text: str, attachments: list[Path]) -> str:
    clean_text = str(text or "").strip()
    if not attachments:
        return clean_text
    lines = ["# Files attached by the user", ""]
    for index, path in enumerate(attachments, start=1):
        path = Path(path)
        lines.append(f"{index}. {path.name}")
        lines.append(f"   Path: {path}")
        lines.append(f"   Type: {native_attachment_kind(path)}")
    lines.extend(["", "# User request", clean_text or "Please review the attached files."])
    return "\n".join(lines)


def parse_coding_slash_command(value: object) -> dict | None:
    text = str(value or "").strip()
    if not text.startswith("/"):
        return None
    match = re.match(r"^/([A-Za-z][\w-]*)(?:\s+([\s\S]*))?$", text)
    if not match:
        return {"name": "", "args": "", "raw": text}
    return {"name": match.group(1).lower(), "args": str(match.group(2) or "").strip(), "raw": text}


def compact_duration_seconds(value: float) -> str:
    seconds = max(0, int(round(value)))
    minutes, second_part = divmod(seconds, 60)
    hours, minute_part = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minute_part}m"
    if minutes:
        return f"{minutes}m {second_part}s"
    return f"{second_part}s"


def codex_goal_display_text(goal: object) -> str:
    if not isinstance(goal, dict):
        return "No Codex goal is set for this thread."
    objective = str(goal.get("objective") or "").strip()
    status = str(goal.get("status") or "active").strip()
    tokens = goal.get("tokensUsed")
    budget = goal.get("tokenBudget")
    time_used = sanitize_float(goal.get("timeUsedSeconds"))
    parts = [f"Status: {status}"]
    if tokens is not None:
        token_text = f"Tokens: {compact_number(sanitize_float(tokens) or 0)}"
        if budget is not None:
            token_text += f" / {compact_number(sanitize_float(budget) or 0)}"
        parts.append(token_text)
    if time_used is not None and time_used > 0:
        parts.append(f"Time: {compact_duration_seconds(time_used)}")
    if objective:
        parts.append("")
        parts.append(objective)
    return "\n".join(parts)


def coding_sidebar_thread_preview_limit(thread_count: int, selected: bool, expanded: bool) -> int:
    if expanded:
        return max(0, thread_count)
    limit = (
        CODING_SIDEBAR_SELECTED_THREAD_PREVIEW_LIMIT
        if selected
        else CODING_SIDEBAR_COLLAPSED_THREAD_PREVIEW_LIMIT
    )
    return max(0, min(thread_count, limit))


CODING_FALLBACK_MODELS = {
    "codex": [
        ("Default", ""),
        ("GPT-5.5", "gpt-5.5"),
        ("GPT-5.4 mini", "gpt-5.4-mini"),
        ("GPT-5.3 Codex Spark", "gpt-5.3-codex-spark"),
    ],
    "claude": [("Default", ""), ("Opus", "opus"), ("Sonnet", "sonnet"), ("Fable", "fable")],
    "cursor": [("Default", "")],
    "antigravity": [("Default", "")],
}

CODING_ACCESS_OPTIONS = {
    "codex": [("Workspace", "workspace"), ("Read only", "read-only"), ("Full access", "full-access")],
    "claude": [
        ("Default", "default"),
        ("Accept edits", "accept-edits"),
        ("Plan", "plan"),
        ("Full access", "full-access"),
    ],
    "cursor": [("Default", "default"), ("Plan", "plan"), ("Ask", "ask"), ("Full access", "full-access")],
    "antigravity": [("Default", "default"), ("Sandbox", "sandbox"), ("Full access", "full-access")],
}

CODING_PERSONALITY_OPTIONS = {
    "codex": [("Friendly", "friendly"), ("Pragmatic", "pragmatic"), ("None", "none")],
    "claude": [("Provider default", "")],
    "cursor": [("Provider default", "")],
    "antigravity": [("Provider default", "")],
}

CODING_EFFORT_OPTIONS = {
    "codex": [
        ("Default", ""),
        ("Low", "low"),
        ("Medium", "medium"),
        ("High", "high"),
        ("Extra high", "xhigh"),
    ],
    "claude": [
        ("Default", ""),
        ("Low", "low"),
        ("Medium", "medium"),
        ("High", "high"),
        ("Extra high", "xhigh"),
        ("Max", "max"),
    ],
    "cursor": [("Model default", "")],
    "antigravity": [("Model default", "")],
}


def read_coding_profile_defaults(profile: dict) -> dict[str, str]:
    provider = provider_key(profile)
    if provider == "codex":
        path = Path(str(profile.get("codexHome") or DEFAULT_CODEX_HOME)) / "config.toml"
        if not path.is_file():
            return {}
        try:
            payload = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, tomllib.TOMLDecodeError):
            return {}
        sandbox = str(payload.get("sandbox_mode") or "")
        access = {
            "workspace-write": "workspace",
            "read-only": "read-only",
            "danger-full-access": "full-access",
        }.get(sandbox, "workspace")
        return {
            "model": str(payload.get("model") or ""),
            "effort": str(payload.get("model_reasoning_effort") or ""),
            "access": access,
            "personality": str(payload.get("personality") or "friendly"),
        }
    if provider == "claude":
        projects = claude_profile_home(profile) / "projects"
        try:
            sessions = sorted(projects.rglob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)[:12]
        except OSError:
            sessions = []
        for session in sessions:
            try:
                lines = session.read_text(encoding="utf-8", errors="replace").splitlines()[-160:]
            except OSError:
                continue
            for line in reversed(lines):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = row.get("message") if isinstance(row.get("message"), dict) else {}
                model = str(message.get("model") or "")
                if model:
                    return {"model": model, "effort": "", "access": "default"}
    return {}


def parse_native_model_listing(value: object) -> list[dict]:
    models: list[dict] = []
    seen: set[str] = set()
    ansi = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    for raw_line in ansi.sub("", str(value or "")).splitlines():
        line = raw_line.strip().lstrip("*-> ").strip()
        if not line or line.lower().startswith(("available model", "model ", "error:", "usage:")):
            continue
        candidate = re.split(r"\s{2,}|\t|\s+-\s+", line, maxsplit=1)[0].strip(" `\"'")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/\-\[\]=,]*", candidate):
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        models.append({"model": candidate, "displayName": candidate, "isDefault": False, "supportedReasoningEfforts": []})
    return models


def codex_access_parameters(access: str, workspace: Path) -> dict:
    if access == "read-only":
        return {
            "approvalPolicy": "on-request",
            "threadSandbox": "read-only",
            "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
        }
    if access == "full-access":
        return {
            "approvalPolicy": "on-request",
            "threadSandbox": "danger-full-access",
            "sandboxPolicy": {"type": "dangerFullAccess"},
        }
    return {
        "approvalPolicy": "on-request",
        "threadSandbox": "workspace-write",
        "sandboxPolicy": {
            "type": "workspaceWrite",
            "writableRoots": [str(Path(workspace))],
            "networkAccess": False,
            "excludeTmpdirEnvVar": False,
            "excludeSlashTmp": False,
        },
    }


def mask_email(value: object) -> str:
    text = str(value or "").strip()
    if "@" not in text:
        return clip_text(text, 28)
    local, domain = text.split("@", 1)
    if not local or not domain:
        return clip_text(text, 28)
    local_mask = local[:2] + "..." if len(local) > 3 else local[:1] + "..."
    domain_parts = domain.split(".")
    domain_head = domain_parts[0]
    domain_tail = "." + domain_parts[-1] if len(domain_parts) > 1 else ""
    domain_mask = (domain_head[:2] + "..." if len(domain_head) > 3 else domain_head[:1] + "...") + domain_tail
    return f"{local_mask}@{domain_mask}"


def account_identity_label(profile: dict) -> str:
    email = str(profile.get("accountEmail") or "").strip()
    name = str(profile.get("accountName") or "").strip()
    if email and name and email.lower() not in name.lower():
        return f"{name} / {email}"
    return email or name or "-"


def masked_account_identity_label(profile: dict) -> str:
    email = str(profile.get("accountEmail") or "").strip()
    name = str(profile.get("accountName") or "").strip()
    if email:
        return mask_email(email)
    if name:
        return clip_text(name, 28)
    return "-"


def account_plan_label(profile: dict) -> str:
    summary = profile.get("usageSummary") if isinstance(profile.get("usageSummary"), dict) else {}
    for key in ("accountPlan", "planType", "plan", "membershipType", "subscriptionTier", "subscriptionType"):
        text = display_plan_text(profile.get(key) or summary.get(key))
        if text:
            status = display_plan_text(profile.get("accountPlanStatus") or summary.get("accountPlanStatus") or summary.get("subscriptionStatus"))
            if status and status.lower() not in text.lower():
                return f"{text} ({status})"
            return text
    account_type = display_plan_text(profile.get("accountType") or summary.get("accountType"))
    return account_type or "Plan not exposed"


def read_vscdb_value(db_path: Path, key: str) -> str:
    if not db_path.exists():
        return ""
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        try:
            row = connection.execute("select value from ItemTable where key=?", (key,)).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return ""
    return "" if not row or row[0] is None else str(row[0])


def read_vscdb_json(db_path: Path, key: str) -> object:
    value = read_vscdb_value(db_path, key)
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def proto_strings_from_base64(value: object) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    try:
        raw = base64.b64decode(text + "=" * (-len(text) % 4), validate=False)
    except Exception:
        return []
    strings = re.findall(rb"[ -~]{2,}", raw)
    return [item.decode("utf-8", "ignore").strip() for item in strings if item.strip()]


def status_colors(state: str) -> tuple[str, str]:
    if state == "ready":
        return GREEN, GREEN_SOFT
    if state in {"not_ready", "error"}:
        return RED, RED_SOFT
    if state == "login":
        return AMBER, AMBER_SOFT
    return BLUE, BLUE_SOFT


def status_label(state: str) -> str:
    return {
        "ready": "Ready",
        "not_ready": "Not Ready",
        "error": "Error",
        "login": "Login",
        "idle": "Idle",
    }.get(state, state.title())


def profile_auth_path(profile: dict) -> Path:
    return Path(str(profile.get("codexHome", ""))).expanduser() / "auth.json"


def default_auth_path() -> Path:
    return DEFAULT_CODEX_HOME / "auth.json"


def has_profile_auth(profile: dict) -> bool:
    return profile_auth_path(profile).exists()


def cooldown_remaining(profile: dict) -> dt.timedelta:
    until = parse_iso_datetime(profile.get("cooldownUntilUtc"))
    if until is None:
        return dt.timedelta()
    remaining = until - dt.datetime.now(dt.timezone.utc)
    return remaining if remaining.total_seconds() > 0 else dt.timedelta()


def format_local_timer(profile: dict) -> str:
    remaining = cooldown_remaining(profile)
    if remaining.total_seconds() <= 0:
        return "-"
    hours = int(remaining.total_seconds() // 3600)
    minutes = int((remaining.total_seconds() % 3600) // 60)
    seconds = int(remaining.total_seconds() % 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def effective_state(profile: dict) -> str:
    last_error = str(profile.get("lastLimitsError", "")).strip()
    if is_revoked_token_message(last_error) or is_not_logged_in_message(last_error):
        return "login"
    provider = provider_key(profile)
    if provider == "claude":
        summary = profile.get("usageSummary") if isinstance(profile.get("usageSummary"), dict) else {}
        auth_status = summary.get("claudeAuthStatus") if isinstance(summary.get("claudeAuthStatus"), dict) else {}
        cli_ready = bool(auth_status.get("loggedIn"))
        desktop_ready = bool(summary.get("desktopReady")) or bool(cached_claude_desktop_login_status().get("ready"))
        desktop_only_error = (
            "claude desktop login not detected" in last_error.lower()
            or "claude desktop is not installed" in last_error.lower()
        )
        if desktop_only_error and not cli_ready:
            return "login"
        if last_error and not (desktop_only_error and cli_ready):
            return "error"
        if str(profile.get("limitReachedType", "")).strip():
            return "not_ready"
        if is_limit_exhausted(profile.get("shortLimitUsedPercent")):
            return "not_ready"
        if is_limit_exhausted(profile.get("weeklyLimitUsedPercent")):
            return "not_ready"
        return "ready" if cli_ready or desktop_ready else "login"
    if provider in {"cursor", "antigravity"}:
        if "login not detected" in last_error.lower():
            return "login"
        if last_error:
            return "error"
        return "ready"
    if provider != "codex":
        if last_error:
            return "error"
        return "ready"
    if last_error:
        return "error"
    if not has_profile_auth(profile):
        return "login"
    if cooldown_remaining(profile).total_seconds() > 0:
        return "not_ready"
    if str(profile.get("limitReachedType", "")).strip():
        return "not_ready"
    if is_limit_exhausted(profile.get("shortLimitUsedPercent")):
        return "not_ready"
    if is_limit_exhausted(profile.get("weeklyLimitUsedPercent")):
        return "not_ready"
    return "ready"


def ready_countdown(profile: dict) -> str:
    local_timer = format_local_timer(profile)
    if local_timer != "-":
        return local_timer

    limit_type = str(profile.get("limitReachedType") or "").strip().lower()
    weekly_reset = profile.get("weeklyResetEstimateUtc") or profile.get("weeklyLimitResetUtc")
    session_reset = profile.get("shortLimitResetUtc")

    def usable_countdown(raw: object) -> str:
        text = format_countdown(raw)
        return "" if text == "-" else text

    weekly_blocked = is_limit_exhausted(profile.get("weeklyLimitUsedPercent")) or "week" in limit_type
    session_blocked = (
        is_limit_exhausted(profile.get("shortLimitUsedPercent"))
        or "primary" in limit_type
        or "session" in limit_type
        or "short" in limit_type
        or "5h" in limit_type
    )
    if weekly_blocked:
        countdown = usable_countdown(weekly_reset)
        if countdown:
            return countdown
    if session_blocked:
        countdown = usable_countdown(session_reset)
        if countdown:
            return countdown

    for reset in (session_reset, weekly_reset):
        countdown = usable_countdown(reset)
        if countdown:
            return countdown
    return ""


def status_badge_text(profile: dict | None, state: str) -> str:
    label = status_label(state)
    if profile is not None and state == "not_ready":
        countdown = ready_countdown(profile)
        if countdown:
            return f"{label} {countdown}"
    return label


def day_from_bucket(bucket: dict) -> str | None:
    for key in ("startDate", "date", "day", "bucketDate"):
        value = bucket.get(key)
        if value:
            text = str(value)
            if re.match(r"^\d{4}-\d{2}-\d{2}", text):
                return text[:10]
    for key in ("startTime", "start", "from"):
        parsed = parse_iso_datetime(bucket.get(key))
        if parsed is not None:
            return parsed.date().isoformat()
    return None


def tokens_from_bucket(bucket: dict) -> int:
    for key in ("tokens", "totalTokens", "tokenCount", "total_token_count"):
        value = sanitize_float(bucket.get(key))
        if value is not None:
            return int(round(value))
    total = 0.0
    found = False
    for key in ("inputTokens", "outputTokens", "cachedInputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"):
        value = sanitize_float(bucket.get(key))
        if value is not None:
            total += value
            found = True
    return int(round(total)) if found else 0


def minutes_from_bucket(bucket: dict) -> int | None:
    for key in ("activeMinutes", "minutes", "durationMinutes", "totalMinutes", "usageMinutes"):
        value = sanitize_float(bucket.get(key))
        if value is not None:
            return int(round(value))
    for key in ("activeSeconds", "durationSeconds", "seconds"):
        value = sanitize_float(bucket.get(key))
        if value is not None:
            return int(round(value / 60))
    return None


def normalize_profile(raw: object, index: int) -> dict:
    source = dict(raw) if isinstance(raw, dict) else {}
    normalized = dict(source)
    for key, fallback in PROFILE_DEFAULTS.items():
        if key not in normalized or normalized[key] is None:
            normalized[key] = fallback.copy() if isinstance(fallback, (dict, list)) else fallback

    if not str(normalized.get("codexHome", "")).strip():
        normalized["codexHome"] = str(DEFAULT_ACCOUNTS_ROOT / f"account-{index + 1}")
    if not str(normalized.get("name", "")).strip():
        normalized["name"] = f"Account {index + 1}"
    normalized["provider"] = provider_key(normalized)
    if provider_key(normalized) == "claude":
        claude_home = claude_profile_home(normalized)
        normalized["claudeConfigDir"] = str(claude_home)
        normalized["codexHome"] = str(claude_home)
    normalized.pop("geminiConfigDir", None)
    if not isinstance(normalized.get("usageDailyBuckets"), list):
        normalized["usageDailyBuckets"] = []
    if not isinstance(normalized.get("usageSummary"), dict):
        normalized["usageSummary"] = {}
    if not isinstance(normalized.get("onlineLinks"), (list, str)):
        normalized["onlineLinks"] = []
    normalized["browserCommand"] = str(normalized.get("browserCommand") or "")
    normalized["browserProfileDir"] = str(normalized.get("browserProfileDir") or "")
    normalized["browserProfileMode"] = browser_profile_mode(normalized)
    return normalized


def default_profiles() -> list[dict]:
    return [normalize_profile({"name": f"Account {index}", "codexHome": str(DEFAULT_ACCOUNTS_ROOT / f"account-{index}")}, index - 1) for index in range(1, 4)]


def load_profiles() -> list[dict]:
    if not PROFILES_FILE.exists():
        profiles = default_profiles()
        save_profiles(profiles)
        return profiles
    try:
        with PROFILES_FILE.open("r", encoding="utf-8-sig") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError):
        profiles = default_profiles()
        save_profiles(profiles)
        return profiles
    items = raw if isinstance(raw, list) else [raw]
    return [normalize_profile(item, index) for index, item in enumerate(items)]


def save_profiles(profiles: list[dict]) -> None:
    LAUNCHER_ROOT.mkdir(parents=True, exist_ok=True)
    cleaned = [normalize_profile(profile, index) for index, profile in enumerate(profiles)]
    with PROFILES_FILE.open("w", encoding="utf-8") as handle:
        json.dump(cleaned, handle, indent=2)


def init_history_db() -> None:
    LAUNCHER_ROOT.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(HISTORY_DB_FILE)
    try:
        connection.executescript(
            """
            create table if not exists usage_history (
                profile_id text not null,
                profile_name text not null,
                provider text not null,
                bucket_day text not null,
                tokens integer not null default 0,
                active_minutes integer,
                message_count integer,
                source text not null,
                bucket_hash text not null,
                bucket_json text not null,
                first_seen_utc text not null,
                last_seen_utc text not null,
                primary key (profile_id, bucket_day, source, bucket_hash)
            );
            create index if not exists idx_usage_history_day on usage_history(bucket_day);
            create index if not exists idx_usage_history_profile on usage_history(profile_id);

            create table if not exists limit_history (
                id integer primary key autoincrement,
                profile_id text not null,
                profile_name text not null,
                provider text not null,
                refreshed_at_utc text not null,
                refresh_reason text not null,
                state text not null,
                short_used_percent real,
                short_left_percent real,
                short_reset_utc text,
                weekly_used_percent real,
                weekly_left_percent real,
                weekly_reset_utc text,
                weekly_estimate_utc text,
                reset_credits_available text,
                limit_reached_type text,
                last_error text
            );
            create unique index if not exists idx_limit_history_profile_refresh
                on limit_history(profile_id, refreshed_at_utc, refresh_reason);
            create index if not exists idx_limit_history_profile on limit_history(profile_id);
            """
        )
        connection.commit()
    finally:
        connection.close()


def history_bucket_hash(bucket: dict) -> str:
    text = json.dumps(bucket, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def history_bucket_source(profile: dict, bucket: dict) -> str:
    source = str(bucket.get("source") or "").strip()
    return source or provider_key(profile)


def history_message_count(bucket: dict) -> int | None:
    for key in ("messageCount", "messages", "requestCount", "requests"):
        value = sanitize_float(bucket.get(key))
        if value is not None:
            return int(round(value))
    return None


def record_profile_history(profile: dict, refresh_reason: str = "refresh") -> None:
    init_history_db()
    now = iso_utc_now()
    pid = profile_id(profile)
    provider = provider_key(profile)
    name = str(profile.get("name") or "Account")
    connection = sqlite3.connect(HISTORY_DB_FILE)
    try:
        for bucket in profile.get("usageDailyBuckets") or []:
            if not isinstance(bucket, dict):
                continue
            day = day_from_bucket(bucket)
            if not day:
                continue
            bucket_json = json.dumps(bucket, sort_keys=True, default=str)
            connection.execute(
                """
                insert into usage_history (
                    profile_id, profile_name, provider, bucket_day, tokens, active_minutes,
                    message_count, source, bucket_hash, bucket_json, first_seen_utc, last_seen_utc
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(profile_id, bucket_day, source, bucket_hash) do update set
                    profile_name = excluded.profile_name,
                    provider = excluded.provider,
                    tokens = excluded.tokens,
                    active_minutes = excluded.active_minutes,
                    message_count = excluded.message_count,
                    bucket_json = excluded.bucket_json,
                    last_seen_utc = excluded.last_seen_utc
                """,
                (
                    pid,
                    name,
                    provider,
                    day,
                    tokens_from_bucket(bucket),
                    minutes_from_bucket(bucket),
                    history_message_count(bucket),
                    history_bucket_source(profile, bucket),
                    history_bucket_hash(bucket),
                    bucket_json,
                    now,
                    now,
                ),
            )

        refreshed_at = iso_from_value(profile.get("lastLimitsRefreshUtc")) or now
        weekly_reset = profile.get("weeklyResetEstimateUtc") or profile.get("weeklyLimitResetUtc")
        connection.execute(
            """
            insert or ignore into limit_history (
                profile_id, profile_name, provider, refreshed_at_utc, refresh_reason, state,
                short_used_percent, short_left_percent, short_reset_utc,
                weekly_used_percent, weekly_left_percent, weekly_reset_utc, weekly_estimate_utc,
                reset_credits_available, limit_reached_type, last_error
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                name,
                provider,
                refreshed_at,
                refresh_reason,
                effective_state(profile),
                sanitize_float(profile.get("shortLimitUsedPercent")),
                percent_left(profile.get("shortLimitUsedPercent")),
                iso_from_value(profile.get("shortLimitResetUtc")),
                sanitize_float(profile.get("weeklyLimitUsedPercent")),
                percent_left(profile.get("weeklyLimitUsedPercent")),
                iso_from_value(profile.get("weeklyLimitResetUtc")),
                iso_from_value(weekly_reset),
                str(profile.get("resetCreditsAvailable") or ""),
                str(profile.get("limitReachedType") or ""),
                str(profile.get("lastLimitsError") or profile.get("lastUsageError") or ""),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def seed_history_from_profiles(profiles: list[dict]) -> None:
    for profile in profiles:
        record_profile_history(profile, refresh_reason="seed")


def history_usage_entries(profiles: list[dict], iso_day: str | None = None) -> list[dict]:
    init_history_db()
    profiles_by_id = {profile_id(profile): profile for profile in profiles}
    allowed = set(profiles_by_id)
    query = (
        "select profile_id, profile_name, provider, bucket_day, tokens, active_minutes, "
        "message_count, source, bucket_json from usage_history"
    )
    params: list[object] = []
    if iso_day is not None:
        query += " where bucket_day = ?"
        params.append(iso_day)
    query += " order by bucket_day, profile_name, provider"
    connection = sqlite3.connect(HISTORY_DB_FILE)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(query, params).fetchall()
    finally:
        connection.close()

    entries: list[dict] = []
    for row in rows:
        pid = str(row["profile_id"])
        if pid not in allowed:
            continue
        profile = profiles_by_id.get(pid) or {"id": pid, "name": row["profile_name"], "provider": row["provider"]}
        try:
            bucket = json.loads(str(row["bucket_json"] or "{}"))
        except json.JSONDecodeError:
            bucket = {}
        entries.append(
            {
                "profileId": pid,
                "profile": profile,
                "day": str(row["bucket_day"] or ""),
                "tokens": int(row["tokens"] or 0),
                "minutes": None if row["active_minutes"] is None else int(row["active_minutes"]),
                "messageCount": None if row["message_count"] is None else int(row["message_count"]),
                "source": str(row["source"] or ""),
                "bucket": bucket,
            }
        )
    return entries


def history_limit_count() -> int:
    init_history_db()
    connection = sqlite3.connect(HISTORY_DB_FILE)
    try:
        row = connection.execute("select count(*) from limit_history").fetchone()
        return int(row[0] if row else 0)
    finally:
        connection.close()


def locate_codex_cli() -> str:
    env_path = os.environ.get("CODEX_CLI_PATH")
    if env_path and Path(env_path).exists():
        return str(Path(env_path).resolve())

    local_bin = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
    if local_bin.exists():
        candidates = list(local_bin.rglob("codex.exe"))
        if candidates:
            return str(max(candidates, key=lambda path: path.stat().st_mtime))

    command = shutil.which("codex.exe") or shutil.which("codex")
    if command:
        return command
    raise RuntimeError("Could not find codex.exe. Start Codex once, then retry.")


def locate_node() -> str:
    command = shutil.which("node.exe") or shutil.which("node")
    if command:
        return command
    raise RuntimeError("Could not find node.exe. Install Node.js or start from a shell where node is on PATH.")


def locate_account_browser_path() -> str:
    env_path = os.environ.get("AI_HUB_BROWSER_PATH") or os.environ.get("BROWSER_PATH")
    if env_path and Path(env_path).exists():
        return str(Path(env_path).resolve())

    candidates = [
        LOCALAPPDATA_ROOT / "Google" / "Chrome" / "Application" / "chrome.exe",
        PROGRAMFILES_ROOT / "Google" / "Chrome" / "Application" / "chrome.exe",
        PROGRAMFILES_X86_ROOT / "Google" / "Chrome" / "Application" / "chrome.exe",
        LOCALAPPDATA_ROOT / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        PROGRAMFILES_ROOT / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        PROGRAMFILES_X86_ROOT / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        LOCALAPPDATA_ROOT / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
        PROGRAMFILES_ROOT / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
        PROGRAMFILES_X86_ROOT / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    command = shutil.which("chrome.exe") or shutil.which("msedge.exe") or shutil.which("brave.exe")
    return command or ""


def get_appx_install_location(package_name: str) -> str:
    try:
        process = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", f"(Get-AppxPackage {package_name}).InstallLocation"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=5,
            creationflags=CREATE_NO_WINDOW,
        )
        for line in process.stdout.splitlines():
            text = line.strip()
            if text:
                return text
    except Exception:
        return ""
    return ""


def locate_codex_icon_path() -> str:
    root = Path("C:/Program Files/WindowsApps")
    package_locations: list[Path] = []
    if root.exists():
        package_locations.extend(sorted(root.glob("OpenAI.Codex_*"), key=lambda path: path.name, reverse=True))

    try:
        process = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", "(Get-AppxPackage OpenAI.Codex).InstallLocation"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=5,
            creationflags=CREATE_NO_WINDOW,
        )
        for line in process.stdout.splitlines():
            text = line.strip()
            if text:
                package_locations.append(Path(text))
    except Exception:
        _logger.debug("locate_codex_icon_path: AppxPackage lookup failed", exc_info=True)

    candidates: list[Path] = []
    seen = set()
    for package in package_locations:
        if str(package).lower() in seen:
            continue
        seen.add(str(package).lower())
        candidates.extend(
            [
                package / "assets" / "Square44x44Logo.targetsize-30_altform-unplated.png",
                package / "assets" / "Square44x44Logo.targetsize-32_altform-unplated.png",
                package / "assets" / "Square44x44Logo.png",
                package / "assets" / "icon.png",
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            local_assets = SCRIPT_DIR / "assets"
            local_assets.mkdir(parents=True, exist_ok=True)
            local_icon = local_assets / "codex-icon.png"
            try:
                if not local_icon.exists() or local_icon.stat().st_size != candidate.stat().st_size:
                    shutil.copy2(candidate, local_icon)
                return str(local_icon)
            except OSError:
                return str(candidate)
    return ""


def locate_claude_desktop_path() -> str:
    install = get_appx_install_location("Claude")
    candidates: list[Path] = []
    if install:
        candidates.append(Path(install) / "app" / "Claude.exe")
    root = Path("C:/Program Files/WindowsApps")
    if root.exists():
        for package in sorted(root.glob("Claude_*"), key=lambda path: path.name, reverse=True):
            candidates.append(package / "app" / "Claude.exe")
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ""


def locate_claude_code_path() -> str:
    root = CLAUDE_ROAMING_HOME / "claude-code"
    if not root.exists():
        return ""
    candidates = list(root.rglob("claude.exe"))
    if not candidates:
        return ""
    return str(max(candidates, key=lambda path: path.stat().st_mtime))


def locate_claude_icon_path() -> str:
    install = get_appx_install_location("Claude")
    package_locations: list[Path] = []
    if install:
        package_locations.append(Path(install))
    root = Path("C:/Program Files/WindowsApps")
    if root.exists():
        package_locations.extend(sorted(root.glob("Claude_*"), key=lambda path: path.name, reverse=True))

    candidates: list[Path] = []
    seen = set()
    for package in package_locations:
        key = str(package).lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.extend(
            [
                package / "assets" / "icon.png",
                package / "assets" / "Square44x44Logo.png",
                package / "app" / "resources" / "ion-dist" / "images" / "claude_app_icon.png",
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            local_assets = SCRIPT_DIR / "assets"
            local_assets.mkdir(parents=True, exist_ok=True)
            local_icon = local_assets / "claude-icon.png"
            try:
                if not local_icon.exists() or local_icon.stat().st_size != candidate.stat().st_size:
                    shutil.copy2(candidate, local_icon)
                return str(local_icon)
            except OSError:
                return str(candidate)
    return ""


def cache_asset_from_candidate(candidate: Path, asset_name: str) -> str:
    local_assets = SCRIPT_DIR / "assets"
    local_assets.mkdir(parents=True, exist_ok=True)
    local_icon = local_assets / asset_name
    try:
        if not local_icon.exists() or local_icon.stat().st_size != candidate.stat().st_size:
            shutil.copy2(candidate, local_icon)
        return str(local_icon)
    except OSError:
        return str(candidate)


def extract_associated_icon_png(exe_path: Path, asset_name: str) -> str:
    if not exe_path.exists():
        return ""
    local_assets = SCRIPT_DIR / "assets"
    local_assets.mkdir(parents=True, exist_ok=True)
    local_icon = local_assets / asset_name
    if local_icon.exists():
        return str(local_icon)
    script = (
        "Add-Type -AssemblyName System.Drawing; "
        f"$icon=[System.Drawing.Icon]::ExtractAssociatedIcon({quote_ps(exe_path)}); "
        "if ($null -eq $icon) { exit 2 }; "
        "$bmp=$icon.ToBitmap(); "
        f"$bmp.Save({quote_ps(local_icon)}, [System.Drawing.Imaging.ImageFormat]::Png); "
        "$bmp.Dispose(); $icon.Dispose();"
    )
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=8,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return ""
    return str(local_icon) if local_icon.exists() else ""


def locate_cursor_desktop_path() -> str:
    env_path = os.environ.get("CURSOR_PATH")
    if env_path and Path(env_path).exists():
        return str(Path(env_path).resolve())
    candidates = [
        PROGRAMFILES_ROOT / "cursor" / "Cursor.exe",
        LOCALAPPDATA_ROOT / "Programs" / "Cursor" / "Cursor.exe",
        Path("C:/Program Files/cursor/Cursor.exe"),
    ]
    command = shutil.which("Cursor.exe") or shutil.which("cursor.exe")
    if command:
        candidates.append(Path(command))
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ""


def locate_cursor_cli_path() -> str:
    env_path = os.environ.get("CURSOR_CLI_PATH")
    if env_path and Path(env_path).exists():
        return str(Path(env_path).resolve())
    candidates = [
        PROGRAMFILES_ROOT / "cursor" / "resources" / "app" / "bin" / "cursor.cmd",
        LOCALAPPDATA_ROOT / "Programs" / "Cursor" / "resources" / "app" / "bin" / "cursor.cmd",
        Path("C:/Program Files/cursor/resources/app/bin/cursor.cmd"),
    ]
    command = shutil.which("cursor.cmd") or shutil.which("cursor")
    if command:
        candidates.append(Path(command))
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ""


def locate_cursor_icon_path() -> str:
    desktop = locate_cursor_desktop_path()
    roots = []
    if desktop:
        roots.append(Path(desktop).parent / "resources" / "app")
    roots.extend(
        [
            PROGRAMFILES_ROOT / "cursor" / "resources" / "app",
            LOCALAPPDATA_ROOT / "Programs" / "Cursor" / "resources" / "app",
        ]
    )
    candidates: list[Path] = []
    for root in roots:
        candidates.extend(
            [
                root / "out" / "media" / "logo.png",
                root / "out" / "vs" / "workbench" / "browser" / "parts" / "editor" / "media" / "logo.png",
                root / "resources" / "win32" / "code_150x150.png",
                root / "resources" / "win32" / "code_70x70.png",
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return cache_asset_from_candidate(candidate, "cursor-icon.png")
    if desktop:
        return extract_associated_icon_png(Path(desktop), "cursor-icon.png")
    return ""


def locate_antigravity_desktop_path() -> str:
    env_path = os.environ.get("ANTIGRAVITY_PATH")
    if env_path and Path(env_path).exists():
        return str(Path(env_path).resolve())
    candidates = [
        ANTIGRAVITY_PROGRAM_DIR / "Antigravity.exe",
        LOCALAPPDATA_ROOT / "Programs" / "Antigravity" / "Antigravity.exe",
        Path("C:/Program Files/Google/Antigravity/Antigravity.exe"),
    ]
    command = shutil.which("Antigravity.exe") or shutil.which("antigravity.exe")
    if command:
        candidates.append(Path(command))
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ""


def locate_antigravity_cli_path() -> str:
    env_path = os.environ.get("ANTIGRAVITY_CLI_PATH")
    if env_path and Path(env_path).exists():
        return str(Path(env_path).resolve())
    candidates = [
        LOCALAPPDATA_ROOT / "agy" / "bin" / "agy.exe",
        ANTIGRAVITY_ROAMING_HOME / "bin" / "agy.cmd",
        ANTIGRAVITY_ROAMING_HOME / "bin" / "agy-node.cmd",
        ANTIGRAVITY_PROGRAM_DIR / "resources" / "app" / "bin" / "agy.cmd",
    ]
    command = shutil.which("agy.cmd") or shutil.which("agy") or shutil.which("antigravity")
    if command:
        candidates.append(Path(command))
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ""


def locate_antigravity_icon_path() -> str:
    desktop = locate_antigravity_desktop_path()
    if desktop:
        return extract_associated_icon_png(Path(desktop), "antigravity-icon.png")
    return ""


def cursor_local_account_status() -> dict:
    db_path = CURSOR_ROAMING_HOME / "User" / "globalStorage" / "state.vscdb"
    email = read_vscdb_value(db_path, "cursorAuth/cachedEmail")
    membership = read_vscdb_value(db_path, "cursorAuth/stripeMembershipType")
    subscription = read_vscdb_value(db_path, "cursorAuth/stripeSubscriptionStatus")
    signup_type = read_vscdb_value(db_path, "cursorAuth/cachedSignUpType")
    return {
        "ready": bool(email),
        "name": "",
        "email": email,
        "plan": membership,
        "status": subscription,
        "accountType": signup_type,
        "summary": f"Cursor account {display_plan_text(membership) or 'detected'}" if email else "Cursor login not detected.",
    }


def antigravity_local_account_status() -> dict:
    db_path = ANTIGRAVITY_ROAMING_HOME / "User" / "globalStorage" / "state.vscdb"
    auth = read_vscdb_json(db_path, "antigravityAuthStatus")
    if not isinstance(auth, dict):
        return {"ready": False, "summary": "Antigravity login not detected."}
    strings = proto_strings_from_base64(auth.get("userStatusProtoBinaryBase64"))
    plan = ""
    for candidate in strings:
        if candidate.lower() in {"pro", "free", "team", "enterprise", "business"}:
            plan = candidate
            break
    if not plan:
        for candidate in strings:
            if re.search(r"\b(pro|free|team|enterprise|business)\b", candidate, re.IGNORECASE):
                plan = re.search(r"\b(pro|free|team|enterprise|business)\b", candidate, re.IGNORECASE).group(1)
                break
    return {
        "ready": bool(auth.get("email") or auth.get("name")),
        "name": str(auth.get("name") or ""),
        "email": str(auth.get("email") or ""),
        "plan": plan,
        "status": "",
        "accountType": display_plan_text(plan),
        "profileUrl": read_vscdb_value(db_path, "antigravity.profileUrl"),
        "summary": f"Antigravity account {display_plan_text(plan) or 'detected'}",
    }


def claude_desktop_login_status() -> dict:
    config_path = CLAUDE_ROAMING_HOME / "config.json"
    cookie_db = CLAUDE_ROAMING_HOME / "Network" / "Cookies"
    status = {
        "desktopInstalled": bool(locate_claude_desktop_path()),
        "profileHome": str(CLAUDE_ROAMING_HOME),
        "hasOAuthCache": False,
        "hasSessionCookie": False,
        "sessionExpires": "",
        "ready": False,
        "summary": "Claude Desktop login not detected.",
    }

    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8-sig"))
            status["hasOAuthCache"] = bool(data.get("oauth:tokenCache") or data.get("oauth:tokenCacheV2"))
        except (OSError, json.JSONDecodeError):
            pass

    if cookie_db.exists():
        try:
            con = sqlite3.connect(f"file:{cookie_db}?mode=ro", uri=True)
            row = con.execute(
                """
                select name, expires_utc
                from cookies
                where host_key like '%claude.ai%' and name in ('sessionKey', 'sessionKeyLC')
                order by case name when 'sessionKey' then 0 else 1 end
                limit 1
                """
            ).fetchone()
            con.close()
            if row:
                status["hasSessionCookie"] = True
                expires = int(row[1] or 0)
                if expires > 0:
                    expiry = dt.datetime(1601, 1, 1) + dt.timedelta(microseconds=expires)
                    status["sessionExpires"] = expiry.replace(tzinfo=dt.timezone.utc).isoformat()
        except Exception:
            _logger.debug("Claude Desktop cookie DB read failed", exc_info=True)

    status["ready"] = bool(status["desktopInstalled"] and (status["hasOAuthCache"] or status["hasSessionCookie"]))
    if status["ready"]:
        bits = []
        if status["hasOAuthCache"]:
            bits.append("OAuth cache")
        if status["hasSessionCookie"]:
            expiry = local_datetime_label(status["sessionExpires"]) if status["sessionExpires"] else "unknown expiry"
            bits.append(f"session cookie expires {expiry}")
        status["summary"] = "Claude Desktop login metadata found: " + "; ".join(bits)
    elif not status["desktopInstalled"]:
        status["summary"] = "Claude Desktop is not installed."
    return status


def cached_claude_desktop_login_status(max_age_seconds: int = STATE_CACHE_SECONDS) -> dict:
    cached_at = _CLAUDE_STATUS_CACHE.get("at")
    cached_value = _CLAUDE_STATUS_CACHE.get("value")
    now = dt.datetime.now(dt.timezone.utc)
    if isinstance(cached_at, dt.datetime) and isinstance(cached_value, dict):
        if (now - cached_at).total_seconds() <= max_age_seconds:
            return cached_value
    value = claude_desktop_login_status()
    _CLAUDE_STATUS_CACHE["at"] = now
    _CLAUDE_STATUS_CACHE["value"] = value
    return value


def parse_claude_reset_label(label: object, base: dt.datetime | None = None) -> str:
    text = re.sub(r"\s*\([^)]*\)\s*$", "", str(label or "").strip())
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    now = base.astimezone() if base is not None else dt.datetime.now().astimezone()
    lower = text.lower()
    day_offset = None
    if lower.startswith("today"):
        day_offset = 0
        text = re.sub(r"(?i)^today(?:\s+at)?\s*,?\s*", "", text)
    elif lower.startswith("tomorrow"):
        day_offset = 1
        text = re.sub(r"(?i)^tomorrow(?:\s+at)?\s*,?\s*", "", text)

    normalized = re.sub(r"(?i)(\d)(am|pm)\b", r"\1 \2", text).upper()
    candidates: list[dt.datetime] = []
    for fmt in (
        "%b %d, %I:%M %p",
        "%B %d, %I:%M %p",
        "%b %d %I:%M %p",
        "%B %d %I:%M %p",
        "%b %d, %I %p",
        "%B %d, %I %p",
        "%b %d %I %p",
        "%B %d %I %p",
    ):
        try:
            parsed = dt.datetime.strptime(normalized, fmt).replace(year=now.year)
            if parsed.astimezone() < now - dt.timedelta(days=1):
                parsed = parsed.replace(year=now.year + 1)
            candidates.append(parsed)
        except ValueError:
            pass
    for fmt in ("%I:%M %p", "%I %p"):
        try:
            time_value = dt.datetime.strptime(normalized, fmt).time()
            date_value = now.date() + dt.timedelta(days=day_offset or 0)
            parsed = dt.datetime.combine(date_value, time_value)
            if day_offset is None and parsed.astimezone() < now - dt.timedelta(minutes=5):
                parsed += dt.timedelta(days=1)
            candidates.append(parsed)
        except ValueError:
            pass

    if not candidates:
        return ""
    return candidates[0].astimezone(dt.timezone.utc).isoformat()


def parse_claude_usage_text(text: object) -> dict:
    result = {
        "sessionUsedPercent": None,
        "sessionResetUtc": "",
        "weeklyUsedPercent": None,
        "weeklyResetUtc": "",
        "summary": str(text or "").strip(),
    }
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("current session"):
            match = re.search(r"(\d+(?:\.\d+)?)%\s+used", line, re.IGNORECASE)
            if match:
                result["sessionUsedPercent"] = float(match.group(1))
            reset_match = re.search(r"\bresets\s+(.+)$", line, re.IGNORECASE)
            if reset_match:
                result["sessionResetUtc"] = parse_claude_reset_label(reset_match.group(1))
        elif line.lower().startswith("current week"):
            match = re.search(r"(\d+(?:\.\d+)?)%\s+used", line, re.IGNORECASE)
            if match:
                result["weeklyUsedPercent"] = float(match.group(1))
            reset_match = re.search(r"\bresets\s+(.+)$", line, re.IGNORECASE)
            if reset_match:
                result["weeklyResetUtc"] = parse_claude_reset_label(reset_match.group(1))
    return result


def hydrate_claude_profile_from_cached_usage(profile: dict) -> bool:
    summary = profile.get("usageSummary") if isinstance(profile.get("usageSummary"), dict) else {}
    cached_text = str(summary.get("claudeUsageStatus") or "").strip()
    if not cached_text:
        return False
    parsed = parse_claude_usage_text(cached_text)
    changed = False
    session_used = parsed.get("sessionUsedPercent")
    weekly_used = parsed.get("weeklyUsedPercent")
    session_reset = str(parsed.get("sessionResetUtc") or "")
    weekly_reset = str(parsed.get("weeklyResetUtc") or "")
    if session_used is not None and str(profile.get("shortLimitUsedPercent") or "") != str(session_used):
        profile["shortLimitUsedPercent"] = str(session_used)
        changed = True
    if weekly_used is not None and str(profile.get("weeklyLimitUsedPercent") or "") != str(weekly_used):
        profile["weeklyLimitUsedPercent"] = str(weekly_used)
        changed = True
    if session_reset and not str(profile.get("shortLimitResetUtc") or "").strip():
        profile["shortLimitResetUtc"] = session_reset
        changed = True
    if weekly_reset and not str(profile.get("weeklyLimitResetUtc") or "").strip():
        profile["weeklyLimitResetUtc"] = weekly_reset
        changed = True
    if weekly_reset and not str(profile.get("weeklyResetEstimateUtc") or "").strip():
        profile["weeklyResetEstimateUtc"] = weekly_reset
        profile["weeklyResetEstimateSource"] = "claude-usage"
        changed = True
    return changed


def claude_usage_total_tokens(usage: dict) -> int:
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return sum(int(sanitize_float(usage.get(key)) or 0) for key in keys)


def build_claude_usage_buckets(projects_root: Path = CLAUDE_PROJECTS_ROOT) -> list[dict]:
    if not projects_root.exists():
        return []
    buckets: dict[str, dict] = {}
    for path in projects_root.rglob("*.jsonl"):
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                timestamp = parse_iso_datetime(item.get("timestamp"))
                message = item.get("message") if isinstance(item.get("message"), dict) else {}
                usage = message.get("usage") if isinstance(message.get("usage"), dict) else None
                if timestamp is None or not usage:
                    continue
                total_tokens = claude_usage_total_tokens(usage)
                if total_tokens <= 0:
                    continue
                day = timestamp.date().isoformat()
                bucket = buckets.setdefault(day, {"date": day, "tokens": 0, "messageCount": 0, "first": timestamp, "last": timestamp})
                bucket["tokens"] += total_tokens
                bucket["messageCount"] += 1
                if timestamp < bucket["first"]:
                    bucket["first"] = timestamp
                if timestamp > bucket["last"]:
                    bucket["last"] = timestamp

    rows: list[dict] = []
    for day, bucket in sorted(buckets.items()):
        duration = bucket["last"] - bucket["first"]
        minutes = max(0, int(round(duration.total_seconds() / 60)))
        rows.append(
            {
                "date": day,
                "tokens": int(bucket["tokens"]),
                "activeMinutes": minutes,
                "messageCount": int(bucket["messageCount"]),
                "source": "claude-code-jsonl",
            }
        )
    return rows


def quote_ps(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def ensure_profile_home(profile: dict) -> None:
    home = claude_profile_home(profile) if provider_key(profile) == "claude" else Path(str(profile.get("codexHome")))
    home.mkdir(parents=True, exist_ok=True)
    workspace = Path(str(profile.get("workspace") or DEFAULT_WORKSPACE))
    workspace.mkdir(parents=True, exist_ok=True)


def seed_config_text() -> str:
    lines = [
        "# Minimal Codex config created by AI Account Hub",
        "# Account credentials are stored separately in this CODEX_HOME.",
        'cli_auth_credentials_store = "file"',
    ]
    source = DEFAULT_CODEX_HOME / "config.toml"
    safe_keys = {"model", "model_reasoning_effort", "service_tier", "approval_policy", "sandbox_mode"}
    if source.exists():
        try:
            for line in source.read_text(encoding="utf-8-sig").splitlines():
                if re.match(r"^\s*\[", line):
                    break
                match = re.match(r"^\s*([A-Za-z0-9_]+)\s*=", line)
                if match and match.group(1) in safe_keys and not re.search(r"(?i)(token|secret|password|credential|authorization|api_key|bearer)", line):
                    if line not in lines:
                        lines.append(line)
        except OSError:
            pass
    return "\n".join(lines) + "\n"


def ensure_file_credential_store(profile: dict) -> str:
    ensure_profile_home(profile)
    target = Path(str(profile.get("codexHome"))) / "config.toml"
    if not target.exists():
        target.write_text(seed_config_text(), encoding="utf-8")
        return f"Created config with file-backed credentials: {target}"

    lines = target.read_text(encoding="utf-8-sig").splitlines()
    first_section = len(lines)
    for index, line in enumerate(lines):
        if re.match(r"^\s*\[", line):
            first_section = index
            break

    for index in range(first_section):
        if re.match(r"^\s*cli_auth_credentials_store\s*=", lines[index]):
            if '"file"' in lines[index]:
                return f"Config already has file-backed credentials: {target}"
            lines[index] = 'cli_auth_credentials_store = "file"'
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return f"Updated config to use file-backed credentials: {target}"

    insert = 'cli_auth_credentials_store = "file"'
    if first_section == 0:
        lines = [insert, ""] + lines
    elif first_section < len(lines):
        lines = lines[:first_section] + [insert, ""] + lines[first_section:]
    else:
        lines.append(insert)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"Added file-backed credentials to config: {target}"


def run_capture(executable: str, args: list[str], cwd: str | Path, env: dict[str, str] | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        [executable, *args],
        cwd=str(cwd),
        env=merged_env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


def windows_file_version(path: str | Path) -> str:
    target = Path(str(path))
    if not target.exists():
        return ""
    script = f"(Get-Item -LiteralPath {quote_ps(target)}).VersionInfo.ProductVersion"
    try:
        process = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=5,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return ""
    return process.stdout.strip()


def get_weekly_reset_estimate(profile: dict, result: dict) -> tuple[str, str]:
    rate_limits = result.get("rateLimits") or {}
    weekly_window = rate_limits.get("weeklyWindow") or {}
    api_reset = iso_from_value(weekly_window.get("resetsAtIso")) if weekly_window else ""

    last_reset = parse_iso_datetime(profile.get("lastResetUtc"))
    if last_reset is not None and last_reset >= dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7):
        return api_reset, "api"

    buckets = ((result.get("usage") or {}).get("dailyUsageBuckets") or [])
    today_utc = dt.datetime.now(dt.timezone.utc).date()
    cutoff = today_utc - dt.timedelta(days=6)
    used_dates: list[dt.date] = []
    for bucket in buckets:
        if not isinstance(bucket, dict) or tokens_from_bucket(bucket) <= 0:
            continue
        day_text = day_from_bucket(bucket)
        if not day_text:
            continue
        try:
            day = dt.date.fromisoformat(day_text)
        except ValueError:
            continue
        if cutoff <= day <= today_utc:
            used_dates.append(day)

    if used_dates:
        estimate = min(used_dates) + dt.timedelta(days=7)
        return dt.datetime.combine(estimate, dt.time(), tzinfo=dt.timezone.utc).isoformat(), "usage"
    return api_reset, "api"


def set_profile_limits_from_result(profile: dict, result: dict) -> None:
    profile["lastLimitsRefreshUtc"] = iso_utc_now()
    if not result.get("ok"):
        message = str(result.get("error") or "Unknown limits refresh error")
        if is_revoked_token_message(message) or is_not_logged_in_message(message):
            mark_auth_error(profile, message)
        else:
            profile["lastLimitsError"] = message
        return

    profile["lastLimitsError"] = ""
    rate_limits = result.get("rateLimits") or {}
    if rate_limits.get("planType") is not None:
        profile["accountPlan"] = str(rate_limits.get("planType") or "")
        profile["accountType"] = str(rate_limits.get("limitName") or rate_limits.get("limitId") or "")
    profile["limitReachedType"] = str(rate_limits.get("rateLimitReachedType") or "")

    reset_outcome = result.get("resetOutcome")
    if reset_outcome is not None:
        profile["lastResetOutcome"] = str(reset_outcome)
        profile["lastResetUtc"] = iso_utc_now()
        if str(reset_outcome) == "reset":
            profile["cooldownUntilUtc"] = ""

    reset_credits = rate_limits.get("rateLimitResetCredits")
    if isinstance(reset_credits, dict) and reset_credits.get("availableCount") is not None:
        profile["resetCreditsAvailable"] = str(reset_credits.get("availableCount"))

    short_window = rate_limits.get("shortWindow")
    if isinstance(short_window, dict):
        profile["shortLimitLabel"] = str(short_window.get("label") or "5h")
        profile["shortLimitUsedPercent"] = "" if short_window.get("usedPercent") is None else str(short_window.get("usedPercent"))
        profile["shortLimitResetUtc"] = iso_from_value(short_window.get("resetsAtIso"))

    weekly_window = rate_limits.get("weeklyWindow")
    if isinstance(weekly_window, dict):
        profile["weeklyLimitLabel"] = str(weekly_window.get("label") or "Weekly")
        profile["weeklyLimitUsedPercent"] = "" if weekly_window.get("usedPercent") is None else str(weekly_window.get("usedPercent"))
        profile["weeklyLimitResetUtc"] = iso_from_value(weekly_window.get("resetsAtIso"))

    usage = result.get("usage") or {}
    profile["usageSummary"] = usage.get("summary") if isinstance(usage.get("summary"), dict) else {}
    buckets = usage.get("dailyUsageBuckets")
    if isinstance(buckets, list):
        profile["usageDailyBuckets"] = [bucket for bucket in buckets if isinstance(bucket, dict)]
    profile["lastUsageError"] = str(result.get("usageError") or "")

    estimate, source = get_weekly_reset_estimate(profile, result)
    profile["weeklyResetEstimateUtc"] = estimate
    profile["weeklyResetEstimateSource"] = source


from coding_transport_bridge import CodingTransportBridge, configure_helpers as _configure_transport_bridge_helpers  # noqa: E402

_configure_transport_bridge_helpers(globals())


from coding_ui_renderer import CodingUiRenderer, configure_helpers as _configure_ui_renderer_helpers  # noqa: E402

_configure_ui_renderer_helpers(globals())


from account_manager import AccountManager, configure_helpers as _configure_account_manager_helpers  # noqa: E402

_configure_account_manager_helpers(globals())


class ScrollFrame(tk.Frame):
    def __init__(self, master: tk.Misc, bg: str = PANEL_ALT, auto_hide: bool = False, **kwargs) -> None:
        super().__init__(master, bg=bg, **kwargs)
        self.auto_hide = auto_hide
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview, style="Vertical.TScrollbar")
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        if not self.auto_hide:
            self.scrollbar.pack(side="right", fill="y", padx=(4, 0))
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

    def _on_inner_configure(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._sync_scrollbar()

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)
        self._sync_scrollbar()

    def _sync_scrollbar(self) -> None:
        if not self.auto_hide:
            return
        bbox = self.canvas.bbox("all")
        content_height = 0 if bbox is None else bbox[3] - bbox[1]
        needs_scrollbar = content_height > max(1, self.canvas.winfo_height()) + 2
        mapped = bool(self.scrollbar.winfo_ismapped())
        if needs_scrollbar and not mapped:
            self.scrollbar.pack(side="right", fill="y", padx=(4, 0))
        elif not needs_scrollbar and mapped:
            self.scrollbar.pack_forget()

    def _bind_mousewheel(self, _event: tk.Event) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, _event: tk.Event) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event: tk.Event) -> None:
        delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, "units")


class AccountCalendarApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.settings = load_settings()
        self.theme_name = "dark" if self.settings.get("theme") == "dark" else "light"
        apply_theme(self.theme_name)
        self.auto_refresh_enabled = bool(self.settings.get("autoRefreshEnabled", True))
        self.auto_refresh_minutes = max(2, int(sanitize_float(self.settings.get("autoRefreshMinutes")) or 10))
        self.next_auto_refresh_at = dt.datetime.now() + dt.timedelta(minutes=1)
        self.title("AI Account Hub")
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        initial_w = min(1540, max(1220, screen_w - 80))
        initial_h = min(900, max(760, screen_h - 120))
        self.geometry(f"{initial_w}x{initial_h}+10+10")
        self.minsize(1220, 760)
        self.configure(bg=BG)
        self.hub_icon_photo = self._hub_icon_photo()
        try:
            self.iconphoto(False, self.hub_icon_photo)
        except tk.TclError:
            pass
        configure_windows_titlebar(self, self.theme_name)

        self.profiles = load_profiles()
        self.codex_cli_path = ""
        self.node_path = ""
        self.claude_desktop_path = ""
        self.claude_code_path = ""
        self.cursor_desktop_path = ""
        self.cursor_cli_path = ""
        self.cursor_agent_path = ""
        self.antigravity_desktop_path = ""
        self.antigravity_cli_path = ""
        self.codex_cli_error = ""
        self.node_error = ""
        self._discover_tools()
        self._prime_local_provider_profiles()
        seed_history_from_profiles(self.profiles)
        self.codex_icon_path = locate_codex_icon_path()
        self.claude_icon_path = locate_claude_icon_path()
        self.cursor_icon_path = locate_cursor_icon_path()
        self.antigravity_icon_path = locate_antigravity_icon_path()

        today = dt.date.today()
        reset_month = self._default_calendar_date(today)
        self.calendar_year = reset_month.year
        self.calendar_month = reset_month.month
        self.selected_date = today.isoformat()
        self.selected_profile = "all"
        self.active_section = "coding"
        saved_coding_projects = self.settings.get("codingProjects")
        self.coding_projects = [
            str(Path(value).expanduser())
            for value in (saved_coding_projects if isinstance(saved_coding_projects, list) else [])
            if str(value).strip()
        ]
        initial_workspace = self.coding_workspace_choices()[0] if self.coding_workspace_choices() else str(DEFAULT_WORKSPACE)
        initial_profile = profile_id(self.profiles[0]) if self.profiles else ""
        self.coding_workspace_var = tk.StringVar(value=initial_workspace)
        self.coding_profile_id = initial_profile
        self.coding_profile_var = tk.StringVar()
        self.coding_model_var = tk.StringVar()
        self.coding_effort_var = tk.StringVar()
        self.coding_personality_var = tk.StringVar()
        self.coding_access_var = tk.StringVar()
        self.coding_model_options: dict[str, str] = {}
        self.coding_effort_options: dict[str, str] = {}
        self.coding_personality_options: dict[str, str] = {}
        self.coding_access_options: dict[str, str] = {}
        self._coding_defaults_cache: dict[str, dict[str, str]] = {}
        self.native_models: list[dict] = []
        self.native_models_profile_id = ""
        self.native_models_loading = False
        self.native_skills: list[dict] = []
        self.native_skills_profile_id = ""
        self.native_skills_workspace = ""
        self.native_skills_loading = False
        self.native_skills_error = ""
        self.coding_search_var = tk.StringVar()
        self.coding_context_tab = "session"
        self.coding_details_visible = False
        self.coding_session_active = False
        self.native_transport: CodexTransport | StreamJsonTransport | None = None
        self.native_transport_key = ""
        self.native_thread_id = ""
        self.native_thread_title = ""
        self.native_turn_id = ""
        self.native_messages: list[dict] = []
        self.native_threads: list[dict] = []
        self.native_diagnostics: list[str] = []
        self.native_attachments: list[Path] = []
        self.expanded_coding_project_threads: set[str] = set()
        self.native_turn_diff = ""
        self.native_file_changes: list[dict] = []
        self.native_token_usage: dict = {}
        self.native_busy = False
        self.native_loading_threads = False
        self.native_generation = 0
        self.native_ui_queue: queue.Queue[object] = queue.Queue()
        self.native_pending_transports: list[CodexTransport | StreamJsonTransport] = []
        self._native_pending_command: dict | None = None
        self.native_transport_lock = threading.Lock()
        self.claude_permission_server: http.server.ThreadingHTTPServer | None = None
        self.claude_permission_thread: threading.Thread | None = None
        self.claude_permission_url = ""
        self.claude_permission_token = ""
        self._closing = False
        self._native_queue_after_id: str | None = None
        self._native_refresh_after_id: str | None = None
        self._native_models_after_id: str | None = None
        self._native_render_after_id: str | None = None
        self._native_render_full = False
        self._coding_stream_message_ranges: dict[str, tuple[str, str]] = {}
        self._coding_stream_signature: tuple[str, ...] = ()
        self._coding_stream_last_texts: dict[str, str] = {}
        self._tick_after_id: str | None = None
        self._coding_scroll_after_id: str | None = None
        self.search_var = tk.StringVar()
        self.sort_var = tk.StringVar(value=str(self.settings.get("sortMode") or "Manual"))
        self.card_template_var = tk.StringVar(value=str(self.settings.get("cardTemplate") or "Balanced"))
        self.mode_var = tk.StringVar(value="month")
        self.status_var = tk.StringVar(value="Ready")
        self.busy = False
        self.buttons: list[tk.Button] = []
        self.account_cards: dict[str, tk.Frame] = {}
        self.account_badges: dict[str, tk.Label] = {}
        self.selected_status_badge: tk.Label | None = None
        self.image_refs: list[tk.PhotoImage] = []
        self.icon_images: dict[tuple[str, int, str], tk.PhotoImage] = {}
        self._profile_state_cache: dict[str, str] = {}
        self._live_account_states: dict[str, str] = {}
        self._last_periodic_render_minute: int | None = None
        self._search_trace_registered = False
        self._sort_trace_registered = False
        self._card_template_trace_registered = False

        self.transport_bridge = CodingTransportBridge(self)
        self.ui_renderer = CodingUiRenderer(self)
        self.account_manager = AccountManager(self)

        self._setup_style()
        self._build()
        self.render()
        self.bind("<Control-n>", lambda _event: self.prepare_native_thread())
        self.bind("<Control-Return>", self._coding_send_key)
        self.bind("<Control-l>", self._focus_coding_input_key)
        self.bind("<Control-k>", self._focus_coding_search_key)
        self.bind("<Control-o>", self._open_coding_project_key)
        self.bind("<F5>", self._refresh_coding_key)
        self.protocol("WM_DELETE_WINDOW", self.close_application)
        self._native_queue_after_id = self.after(50, self._drain_native_ui_queue)
        self._native_refresh_after_id = self.after(250, self.refresh_native_threads)
        self._native_models_after_id = self.after(500, self.refresh_native_models)
        self._tick_after_id = self.after(1000, self._tick)

    def _discover_tools(self) -> None:
        try:
            self.codex_cli_path = locate_codex_cli()
        except Exception as error:
            self.codex_cli_error = str(error)
        try:
            self.node_path = locate_node()
        except Exception as error:
            self.node_error = str(error)
        self.claude_desktop_path = locate_claude_desktop_path()
        self.claude_code_path = locate_claude_code_path()
        self.cursor_desktop_path = locate_cursor_desktop_path()
        self.cursor_cli_path = locate_cursor_cli_path()
        self.cursor_agent_path = locate_cursor_agent()
        self.antigravity_desktop_path = locate_antigravity_desktop_path()
        self.antigravity_cli_path = locate_antigravity_cli_path()

    def _prime_local_provider_profiles(self) -> None:
        changed = False
        for profile in self.profiles:
            try:
                provider = provider_key(profile)
                if provider == "claude":
                    changed = hydrate_claude_profile_from_cached_usage(profile) or changed
                elif provider == "cursor":
                    self.refresh_cursor_profile(profile)
                elif provider == "antigravity":
                    self.refresh_antigravity_profile(profile)
            except Exception as error:
                profile["lastLimitsRefreshUtc"] = iso_utc_now()
                profile["lastLimitsError"] = str(error)
        if changed:
            save_profiles(self.profiles)

    def _default_calendar_date(self, today: dt.date) -> dt.date:
        upcoming: list[dt.date] = []
        for profile in self.profiles:
            reset_raw = profile.get("weeklyResetEstimateUtc") or profile.get("weeklyLimitResetUtc")
            parsed = parse_iso_datetime(reset_raw)
            if parsed is not None:
                reset_day = parsed.date()
                while reset_day < today:
                    reset_day += dt.timedelta(days=7)
                upcoming.append(reset_day)
        return min(upcoming) if upcoming else today

    def _setup_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Vertical.TScrollbar", gripcount=0, background=LINE_STRONG, troughcolor=BG, bordercolor=BG, arrowcolor=MUTED, width=15, arrowsize=13)
        coding = coding_palette(self.theme_name)
        style.configure(
            "Coding.Vertical.TScrollbar",
            gripcount=0,
            background=coding["line_strong"],
            troughcolor=coding["bg"],
            bordercolor=coding["bg"],
            arrowcolor=coding["muted"],
            width=8,
            arrowsize=0,
        )
        style.layout(
            "Coding.Vertical.TScrollbar",
            [
                (
                    "Vertical.Scrollbar.trough",
                    {
                        "sticky": "ns",
                        "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})],
                    },
                )
            ],
        )
        style.configure(
            "Hub.TCombobox",
            fieldbackground=PANEL,
            background=PANEL,
            foreground=INK,
            arrowcolor=MUTED,
            bordercolor=LINE,
            lightcolor=LINE,
            darkcolor=LINE,
            insertcolor=INK,
            padding=(8, 5),
        )
        style.map(
            "Hub.TCombobox",
            fieldbackground=[("readonly", PANEL), ("disabled", PANEL_ALT)],
            foreground=[("readonly", INK), ("disabled", MUTED)],
            selectbackground=[("readonly", PANEL)],
            selectforeground=[("readonly", INK)],
            arrowcolor=[("disabled", MUTED), ("readonly", MUTED)],
        )
        coding = coding_palette(self.theme_name)
        style.configure(
            "Coding.TCombobox",
            fieldbackground=coding["composer"],
            background=coding["composer"],
            foreground=coding["ink"],
            arrowcolor=coding["muted"],
            bordercolor=coding["line"],
            lightcolor=coding["line"],
            darkcolor=coding["line"],
            insertcolor=coding["ink"],
            padding=(7, 3),
        )
        style.map(
            "Coding.TCombobox",
            fieldbackground=[("readonly", coding["composer"]), ("disabled", coding["panel"])],
            foreground=[("readonly", coding["ink"]), ("disabled", coding["faint"])],
            selectbackground=[("readonly", coding["composer"])],
            selectforeground=[("readonly", coding["ink"])],
            arrowcolor=[("disabled", coding["faint"]), ("readonly", coding["muted"])],
        )
        self.update_idletasks()

    def _build(self) -> None:
        self.configure(bg=BG)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._build_topbar()
        self._build_body()
        self._build_statusbar()
        self.show_section(self.active_section, render_page=False)

    def _build_topbar(self) -> None:
        self.topbar = tk.Frame(self, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        self.topbar.grid(row=0, column=0, sticky="nsew")
        self.topbar.grid_columnconfigure(1, weight=1)

        brand = tk.Frame(self.topbar, bg=PANEL)
        brand.grid(row=0, column=0, sticky="w", padx=18, pady=11)
        self._hub_icon(brand).pack(side="left", padx=(0, 10))
        title_box = tk.Frame(brand, bg=PANEL)
        title_box.pack(side="left")
        tk.Label(title_box, text="AI Account Hub", bg=PANEL, fg=INK, font=("Segoe UI", 13, "bold")).pack(anchor="w")
        self.section_subtitle_label = tk.Label(title_box, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 8))
        self.section_subtitle_label.pack(anchor="w")

        section_nav = tk.Frame(self.topbar, bg=PANEL)
        section_nav.grid(row=0, column=1, sticky="w", padx=(24, 8), pady=10)
        self.section_buttons: dict[str, tk.Button] = {}
        for label, section in (("Coding", "coding"), ("Accounts", "accounts")):
            button = self._seg_button(section_nav, label, lambda value=section: self.show_section(value))
            button.pack(side="left", padx=3)
            self.section_buttons[section] = button

        actions = tk.Frame(self.topbar, bg=PANEL)
        actions.grid(row=0, column=2, sticky="e", padx=18, pady=10)

        self.coding_top_actions = tk.Frame(actions, bg=PANEL)
        self.coding_top_actions.pack(side="left")
        self._button(self.coding_top_actions, "Open Project", "+", self.add_coding_project).pack(side="left", padx=4)
        self.coding_new_thread_button = self._button(
            self.coding_top_actions,
            "New Thread",
            "N",
            self.prepare_native_thread,
            variant="primary",
        )
        self.coding_new_thread_button.pack(side="left", padx=4)

        self.account_top_actions = tk.Frame(actions, bg=PANEL)
        self.account_top_actions.pack(side="left")
        self._button(self.account_top_actions, "Reload", "R", self.reload_profiles).pack(side="left", padx=4)
        self._button(self.account_top_actions, "Refresh All", "A", self.refresh_all_limits).pack(side="left", padx=4)
        self.top_profile_buttons = []
        self.auto_refresh_button = self._button(self.account_top_actions, "Auto On" if self.auto_refresh_enabled else "Auto Off", "O", self.toggle_auto_refresh)
        self.auto_refresh_button.pack(side="left", padx=4)
        self.theme_button = self._button(actions, "Dark" if self.theme_name == "light" else "Light", "D", self.toggle_theme)
        self.theme_button.pack(side="left", padx=(10, 4))

    def _hub_icon_photo(self) -> tk.PhotoImage:
        size = 32
        image = tk.PhotoImage(width=size, height=size)

        def block(x: int, y: int, color: str, radius: int = 1) -> None:
            image.put(color, to=(max(0, x - radius), max(0, y - radius), min(size, x + radius + 1), min(size, y + radius + 1)))

        def line(x0: int, y0: int, x1: int, y1: int, color: str) -> None:
            steps = max(abs(x1 - x0), abs(y1 - y0), 1)
            for step in range(steps + 1):
                x = round(x0 + (x1 - x0) * step / steps)
                y = round(y0 + (y1 - y0) * step / steps)
                block(x, y, color, 0)

        image.put(PRIMARY, to=(0, 0, size, size))
        center = (16, 16)
        nodes = [(16, 7, "#6bd68f"), (25, 16, "#79c4e8"), (16, 25, "#ffd166"), (7, 16, "#d97742")]
        for x, y, color in nodes:
            line(center[0], center[1], x, y, "#ffffff")
            block(x, y, color, 3)
        block(center[0], center[1], "#ffffff", 4)
        return image

    def _hub_icon(self, master: tk.Misc, background: str | None = None) -> tk.Canvas:
        size = 34
        canvas = tk.Canvas(master, width=size, height=size, bg=background or PANEL, highlightthickness=0, bd=0)
        canvas.create_oval(2, 2, size - 2, size - 2, fill=PRIMARY, outline=PRIMARY)
        nodes = [
            (size * 0.5, size * 0.24, "#6bd68f"),
            (size * 0.75, size * 0.5, "#79c4e8"),
            (size * 0.5, size * 0.76, "#ffd166"),
            (size * 0.25, size * 0.5, "#d97742"),
        ]
        for x, y, color in nodes:
            canvas.create_line(size / 2, size / 2, x, y, fill="white", width=1)
            canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=color, outline="")
        canvas.create_oval(size / 2 - 5, size / 2 - 5, size / 2 + 5, size / 2 + 5, fill="white", outline="")
        return canvas

    def _build_body(self) -> None:
        self.page_host = tk.Frame(self, bg=BG)
        self.page_host.grid(row=1, column=0, sticky="nsew")
        self.page_host.grid_rowconfigure(0, weight=1)
        self.page_host.grid_columnconfigure(0, weight=1)

        self.coding_page = tk.Frame(self.page_host, bg=BG)
        self.coding_page.grid(row=0, column=0, sticky="nsew")
        self._build_coding_page(self.coding_page)

        self.account_page = tk.Frame(self.page_host, bg=BG)
        self.account_page.grid(row=0, column=0, sticky="nsew")
        self._build_account_page(self.account_page)

    def _build_account_page(self, master: tk.Misc) -> None:
        master.grid_rowconfigure(1, weight=1)
        master.grid_columnconfigure(0, weight=1)

        page_header = tk.Frame(master, bg=PANEL_ALT, highlightbackground=LINE, highlightthickness=1)
        page_header.grid(row=0, column=0, sticky="ew")
        page_header.grid_columnconfigure(0, weight=1)
        title = tk.Frame(page_header, bg=PANEL_ALT)
        title.grid(row=0, column=0, sticky="w", padx=14, pady=8)
        tk.Label(title, text="Account dashboard", bg=PANEL_ALT, fg=INK, font=("Segoe UI", 10, "bold")).pack(side="left")
        tk.Label(
            title,
            text=f"  Profiles: {PROFILES_FILE}",
            bg=PANEL_ALT,
            fg=MUTED,
            font=("Segoe UI", 8),
        ).pack(side="left")
        self.active_account_label = tk.Label(page_header, text="", bg=PANEL_ALT, fg=BLUE, font=("Segoe UI", 8, "bold"))
        self.active_account_label.grid(row=0, column=1, sticky="e", padx=14, pady=8)

        body = tk.Frame(master, bg=BG)
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        self._build_accounts(body)
        self._build_calendar_area(body)
        self._build_details(body)

    def _build_coding_page(self, master: tk.Misc) -> None:
        coding = coding_palette(self.theme_name)
        master.configure(bg=coding["bg"])
        master.grid_rowconfigure(0, weight=1)
        master.grid_columnconfigure(1, weight=1)

        sidebar = tk.Frame(master, bg=coding["rail"], width=292, highlightbackground=coding["line"], highlightthickness=1)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_columnconfigure(0, weight=1)
        sidebar.grid_rowconfigure(4, weight=1)

        brand = tk.Frame(sidebar, bg=coding["rail"])
        brand.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 12))
        self._hub_icon(brand, coding["rail"]).pack(side="left", padx=(0, 9))
        brand_text = tk.Frame(brand, bg=coding["rail"])
        brand_text.pack(side="left")
        tk.Label(brand_text, text="AI Account Hub", bg=coding["rail"], fg=coding["ink"], font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(brand_text, text="Native coding", bg=coding["rail"], fg=coding["muted"], font=("Segoe UI", 8)).pack(anchor="w", pady=(2, 0))

        navigation = tk.Frame(sidebar, bg=coding["rail"])
        navigation.grid(row=1, column=0, sticky="ew", padx=8)
        self.coding_sidebar_new_thread_button = self._coding_sidebar_button(
            navigation,
            "New chat",
            self.prepare_native_thread,
            primary=True,
            shortcut="Ctrl+N",
        )
        self.coding_sidebar_new_thread_button.pack(fill="x", pady=2)
        self.coding_sidebar_accounts_button = self._coding_sidebar_button(
            navigation,
            "Accounts",
            lambda: self.show_section("accounts"),
            shortcut=str(len(self.profiles)),
        )
        self.coding_sidebar_accounts_button.pack(fill="x", pady=2)

        self.coding_search_entry = tk.Entry(sidebar, textvariable=self.coding_search_var, relief="flat", bd=0, font=("Segoe UI", 9))
        self.coding_search_entry.grid(row=2, column=0, sticky="ew", padx=14, pady=(10, 12), ipady=7)
        self.coding_search_entry.configure(
            bg=coding["field"],
            fg=coding["muted"],
            insertbackground=coding["ink"],
            highlightbackground=coding["line"],
            highlightcolor=coding["line_strong"],
            highlightthickness=1,
        )
        self.coding_search_entry.insert(0, "Search")
        self.coding_search_placeholder_active = True
        self.coding_search_entry.bind("<FocusIn>", self._coding_search_focus_in)
        self.coding_search_entry.bind("<FocusOut>", self._coding_search_focus_out)
        self.coding_search_entry.bind("<KeyRelease>", lambda _event: self._render_coding_projects())

        project_head = tk.Frame(sidebar, bg=coding["rail"])
        project_head.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 5))
        project_head.grid_columnconfigure(0, weight=1)
        tk.Label(project_head, text="Projects", bg=coding["rail"], fg=coding["muted"], font=("Segoe UI", 8), anchor="w").grid(row=0, column=0, sticky="w")
        self.coding_project_count = tk.Label(project_head, text="", bg=coding["rail"], fg=coding["muted"], font=("Segoe UI", 7))
        self.coding_project_count.grid(row=0, column=1, sticky="e", padx=(4, 8))
        refresh_projects = tk.Button(
            project_head,
            text="R",
            command=self.refresh_native_threads,
            bg=coding["rail"],
            fg=coding["muted"],
            activebackground=coding["active"],
            activeforeground=coding["ink"],
            relief="flat",
            bd=0,
            font=("Segoe UI", 8),
            cursor="hand2",
            padx=4,
            pady=0,
        )
        refresh_projects.grid(row=0, column=2, sticky="e", padx=(0, 3))
        self.buttons.append(refresh_projects)
        add_project = tk.Button(
            project_head,
            text="+",
            command=self.add_coding_project,
            bg=coding["rail"],
            fg=coding["muted"],
            activebackground=coding["active"],
            activeforeground=coding["ink"],
            relief="flat",
            bd=0,
            font=("Segoe UI", 10),
            cursor="hand2",
            padx=4,
            pady=0,
        )
        add_project.grid(row=0, column=3, sticky="e")
        self.buttons.append(add_project)

        self.coding_project_scroll = ScrollFrame(sidebar, bg=coding["rail"], auto_hide=True)
        self.coding_project_scroll.grid(row=4, column=0, sticky="nsew", padx=(8, 4), pady=(0, 8))

        self.coding_sidebar_account = tk.Frame(sidebar, bg=coding["rail"], highlightbackground=coding["line"], highlightthickness=1)
        self.coding_sidebar_account.grid(row=5, column=0, sticky="ew")

        center = tk.Frame(master, bg=coding["bg"])
        center.grid(row=0, column=1, sticky="nsew")
        center.grid_rowconfigure(1, weight=1)
        center.grid_columnconfigure(0, weight=1)
        center.bind("<Configure>", self._resize_coding_composer)

        session_header = tk.Frame(center, bg=coding["bg"], highlightbackground=coding["line"], highlightthickness=1)
        session_header.grid(row=0, column=0, sticky="ew")
        session_header.grid_columnconfigure(0, weight=1)
        session_title = tk.Frame(session_header, bg=coding["bg"])
        session_title.grid(row=0, column=0, sticky="w", padx=18, pady=10)
        self.coding_title = tk.Label(session_title, text="New thread", bg=coding["bg"], fg=coding["ink"], font=("Segoe UI", 11, "bold"))
        self.coding_title.pack(anchor="w")
        self.coding_subtitle = tk.Label(session_title, text="", bg=coding["bg"], fg=coding["muted"], font=("Segoe UI", 8))
        self.coding_subtitle.pack(anchor="w", pady=(2, 0))
        header_actions = tk.Frame(session_header, bg=coding["bg"])
        header_actions.grid(row=0, column=1, sticky="e", padx=12, pady=9)
        self.coding_stop_button = self._coding_header_button(header_actions, "Stop", self.stop_native_turn)
        self.coding_stop_button.pack(side="left", padx=3)
        self.coding_stop_button.configure(state="disabled")
        self.coding_skills_button = self._coding_header_button(header_actions, "Skills", self.show_coding_skills)
        self.coding_skills_button.pack(side="left", padx=3)
        self.coding_details_button = self._coding_header_button(header_actions, "Details", self.toggle_coding_details)
        self.coding_details_button.pack(side="left", padx=3)

        stream_shell = tk.Frame(center, bg=coding["bg"])
        stream_shell.grid(row=1, column=0, sticky="nsew")
        stream_shell.grid_rowconfigure(0, weight=1)
        stream_shell.grid_columnconfigure(0, weight=1)
        self.coding_stream_text = tk.Text(
            stream_shell,
            bg=coding["bg"],
            fg=coding["ink"],
            insertbackground=coding["ink"],
            selectbackground=coding["active"],
            selectforeground=coding["ink"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            wrap="word",
            cursor="arrow",
            padx=0,
            pady=0,
            undo=False,
            state="disabled",
        )
        self.coding_stream_text.grid(row=0, column=0, sticky="nsew")
        self.coding_stream_scrollbar = ttk.Scrollbar(
            stream_shell,
            orient="vertical",
            command=self.coding_stream_text.yview,
            style="Coding.Vertical.TScrollbar",
        )
        self.coding_stream_scrollbar.grid(row=0, column=1, sticky="ns")
        self.coding_stream_text.configure(yscrollcommand=self.coding_stream_scrollbar.set)
        self.coding_stream = self.coding_stream_text

        composer_dock = tk.Frame(center, bg=coding["bg"])
        composer_dock.grid(row=2, column=0, sticky="ew", pady=(6, 12))
        composer_dock.grid_columnconfigure(0, weight=1)
        self.coding_composer = tk.Frame(
            composer_dock,
            bg=coding["composer"],
            width=860,
            height=126,
            highlightbackground=coding["line_strong"],
            highlightthickness=1,
        )
        self.coding_composer.grid(row=0, column=0)
        self.coding_composer.grid_propagate(False)
        self.coding_composer.grid_rowconfigure(0, weight=1)
        self.coding_composer.grid_columnconfigure(0, weight=1)

        self.coding_input = tk.Text(
            self.coding_composer,
            height=2,
            bg=coding["composer"],
            fg=coding["muted"],
            relief="flat",
            font=("Segoe UI", 9),
            wrap="word",
            undo=True,
        )
        self.coding_input.grid(row=0, column=0, sticky="nsew", padx=14, pady=(9, 2))
        self.coding_input.configure(insertbackground=coding["ink"], selectbackground=PRIMARY, selectforeground="white")
        self.coding_input.insert("1.0", "Ask for code changes or questions")
        self.coding_input_placeholder_active = True
        self.coding_input.bind("<FocusIn>", self._coding_input_focus_in)
        self.coding_input.bind("<FocusOut>", self._coding_input_focus_out)
        self.coding_input.bind("<Return>", self._coding_input_return)
        self.coding_input.bind("<Control-Return>", self._coding_send_key)

        self.coding_attachment_tray = tk.Frame(self.coding_composer, bg=coding["composer"])
        self.coding_attachment_tray.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 2))
        self.coding_attachment_tray.grid_columnconfigure(0, weight=1)
        self.coding_attachment_tray.grid_remove()

        composer_footer = tk.Frame(self.coding_composer, bg=coding["composer"])
        composer_footer.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))
        composer_footer.grid_columnconfigure(4, weight=1)
        self.coding_attach_button = self._coding_compact_button(composer_footer, "+", self.attach_native_files)
        self.coding_attach_button.grid(row=0, column=0, sticky="s", padx=(0, 8), pady=(8, 0))

        model_box = tk.Frame(composer_footer, bg=coding["composer"])
        model_box.grid(row=0, column=1, sticky="w", padx=(0, 6))
        tk.Label(model_box, text="MODEL", bg=coding["composer"], fg=coding["faint"], font=("Segoe UI", 6, "bold")).pack(anchor="w", padx=2)
        self.coding_model_combo = ttk.Combobox(
            model_box,
            textvariable=self.coding_model_var,
            state="readonly",
            width=18,
            style="Coding.TCombobox",
        )
        self.coding_model_combo.pack(anchor="w")
        self.coding_model_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_coding_control_changed("model"))

        effort_box = tk.Frame(composer_footer, bg=coding["composer"])
        effort_box.grid(row=0, column=2, sticky="w", padx=(0, 6))
        tk.Label(effort_box, text="EFFORT", bg=coding["composer"], fg=coding["faint"], font=("Segoe UI", 6, "bold")).pack(anchor="w", padx=2)
        self.coding_effort_combo = ttk.Combobox(
            effort_box,
            textvariable=self.coding_effort_var,
            state="readonly",
            width=11,
            style="Coding.TCombobox",
        )
        self.coding_effort_combo.pack(anchor="w")
        self.coding_effort_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_coding_control_changed("effort"))

        personality_box = tk.Frame(composer_footer, bg=coding["composer"])
        personality_box.grid(row=0, column=3, sticky="w", padx=(0, 6))
        tk.Label(personality_box, text="STYLE", bg=coding["composer"], fg=coding["faint"], font=("Segoe UI", 6, "bold")).pack(anchor="w", padx=2)
        self.coding_personality_combo = ttk.Combobox(
            personality_box,
            textvariable=self.coding_personality_var,
            state="readonly",
            width=11,
            style="Coding.TCombobox",
        )
        self.coding_personality_combo.pack(anchor="w")
        self.coding_personality_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_coding_control_changed("personality"))

        access_box = tk.Frame(composer_footer, bg=coding["composer"])
        access_box.grid(row=0, column=4, sticky="w")
        tk.Label(access_box, text="ACCESS", bg=coding["composer"], fg=coding["faint"], font=("Segoe UI", 6, "bold")).pack(anchor="w", padx=2)
        self.coding_access_combo = ttk.Combobox(
            access_box,
            textvariable=self.coding_access_var,
            state="readonly",
            width=13,
            style="Coding.TCombobox",
        )
        self.coding_access_combo.pack(anchor="w")
        self.coding_access_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_coding_control_changed("access"))

        self.coding_composer_status = tk.Label(
            composer_footer,
            text="Native passthrough",
            bg=coding["composer"],
            fg=coding["muted"],
            font=("Segoe UI", 7),
        )
        self.coding_composer_status.grid(row=0, column=5, sticky="e", padx=8, pady=(10, 0))

        limit_box = tk.Frame(composer_footer, bg=coding["composer"])
        limit_box.grid(row=0, column=6, sticky="e", padx=(0, 8))
        tk.Label(limit_box, text="5H SESSION", bg=coding["composer"], fg=coding["faint"], font=("Segoe UI", 6, "bold")).pack(anchor="e")
        self.coding_short_limit_label = tk.Label(
            limit_box,
            text="-",
            bg=coding["composer"],
            fg=coding["ink"],
            font=("Segoe UI", 8, "bold"),
        )
        self.coding_short_limit_label.pack(anchor="e", pady=(1, 1))
        self.coding_short_limit_meter = tk.Canvas(
            limit_box,
            width=76,
            height=4,
            bg=coding["line"],
            highlightthickness=0,
            bd=0,
        )
        self.coding_short_limit_meter.pack(anchor="e")

        self.coding_send_button = self._coding_compact_button(composer_footer, "\u2191", self.send_native_message, primary=True)
        self.coding_send_button.grid(row=0, column=7, sticky="s", pady=(8, 0))
        self.coding_send_button.configure(width=3, padx=0, pady=4, font=("Segoe UI", 11, "bold"))
        self.coding_send_button.configure(state="disabled")

        self.coding_inspector = tk.Frame(master, bg=coding["rail"], width=340, highlightbackground=coding["line"], highlightthickness=1)
        self.coding_inspector.grid(row=0, column=2, sticky="nsew")
        self.coding_inspector.grid_propagate(False)
        self.coding_inspector.grid_rowconfigure(1, weight=1)
        self.coding_inspector.grid_columnconfigure(0, weight=1)

        inspector_tabs = tk.Frame(self.coding_inspector, bg=coding["rail"])
        inspector_tabs.grid(row=0, column=0, sticky="ew", padx=12, pady=12)
        self.coding_context_buttons: dict[str, tk.Button] = {}
        for label, value in (("Session", "session"), ("Skills", "skills"), ("Files", "files"), ("Terminal", "terminal")):
            button = self._seg_button(inspector_tabs, label, lambda tab=value: self.set_coding_context_tab(tab))
            button.pack(side="left", padx=2)
            self.coding_context_buttons[value] = button

        self.coding_context_scroll = ScrollFrame(self.coding_inspector, bg=coding["rail"], auto_hide=True)
        self.coding_context_scroll.grid(row=1, column=0, sticky="nsew", padx=(12, 8), pady=(0, 12))
        self.coding_inspector.grid_remove()

    def _build_statusbar(self) -> None:
        self.statusbar = tk.Frame(self, bg=DARK)
        self.statusbar.grid(row=2, column=0, sticky="ew")
        self.statusbar.grid_columnconfigure(0, weight=1)
        tk.Label(self.statusbar, textvariable=self.status_var, bg=DARK, fg="white", font=("Segoe UI", 8), anchor="w").grid(row=0, column=0, sticky="ew", padx=12, pady=5)
        cli = self.codex_cli_path or self.codex_cli_error or "codex.exe not found"
        self.status_context_label = tk.Label(self.statusbar, text=cli, bg=DARK, fg=MUTED, font=("Segoe UI", 8), anchor="e")
        self.status_context_label.grid(row=0, column=1, sticky="e", padx=12, pady=5)

    def _build_accounts(self, master: tk.Misc) -> None:
        panel = tk.Frame(master, bg=PANEL_ALT, width=380, highlightbackground=LINE, highlightthickness=1)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.grid_propagate(False)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(6, weight=1)

        head = tk.Frame(panel, bg=PANEL_ALT)
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))
        tk.Label(head, text="Profiles", bg=PANEL_ALT, fg=INK, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.pool_summary = tk.Label(head, text="", bg=PANEL_ALT, fg=MUTED, font=("Segoe UI", 8), justify="left")
        self.pool_summary.pack(anchor="w", pady=(3, 0))

        search = tk.Entry(panel, textvariable=self.search_var, relief="solid", bd=1, font=("Segoe UI", 9))
        search.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8), ipady=7)
        search.configure(bg=PANEL, fg=INK, insertbackground=INK, highlightbackground=LINE, highlightcolor=PRIMARY)
        if not self._search_trace_registered:
            self.search_var.trace_add("write", lambda *_: self.render())
            self._search_trace_registered = True

        control_row = tk.Frame(panel, bg=PANEL_ALT)
        control_row.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 8))
        control_row.grid_columnconfigure(1, weight=1)
        control_row.grid_columnconfigure(3, weight=1)
        tk.Label(control_row, text="Sort", bg=PANEL_ALT, fg=MUTED, font=("Segoe UI", 8), anchor="w").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.sort_combo = ttk.Combobox(control_row, textvariable=self.sort_var, values=SORT_CHOICES, state="readonly", width=13, style="Hub.TCombobox")
        self.sort_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        tk.Label(control_row, text="View", bg=PANEL_ALT, fg=MUTED, font=("Segoe UI", 8), anchor="w").grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.card_template_combo = ttk.Combobox(control_row, textvariable=self.card_template_var, values=CARD_TEMPLATE_CHOICES, state="readonly", width=13, style="Hub.TCombobox")
        self.card_template_combo.grid(row=0, column=3, sticky="ew")
        if not self._sort_trace_registered:
            self.sort_var.trace_add("write", lambda *_: self.on_sort_changed())
            self._sort_trace_registered = True
        if not self._card_template_trace_registered:
            self.card_template_var.trace_add("write", lambda *_: self.on_card_template_changed())
            self._card_template_trace_registered = True

        tools = tk.Frame(panel, bg=PANEL_ALT)
        tools.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 8))
        for column in range(3):
            tools.grid_columnconfigure(column, weight=1)
        self.profile_action_buttons: dict[str, tk.Button] = {}
        self.profile_action_buttons["add"] = self._button(tools, "Add", "+", self.add_account_dialog, variant="primary")
        self.profile_action_buttons["add"].grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        self.profile_action_buttons["edit"] = self._button(tools, "Edit", "E", self.edit_selected_account)
        self.profile_action_buttons["edit"].grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        self.profile_action_buttons["rename"] = self._button(tools, "Rename", "N", self.rename_selected_account)
        self.profile_action_buttons["rename"].grid(row=0, column=2, sticky="ew", padx=3, pady=3)
        self.profile_action_buttons["delete"] = self._button(tools, "Delete", "X", self.delete_selected_account, variant="warning")
        self.profile_action_buttons["delete"].grid(row=1, column=0, sticky="ew", padx=3, pady=3)
        self.profile_action_buttons["up"] = self._button(tools, "Up", "^", lambda: self.move_selected_account(-1))
        self.profile_action_buttons["up"].grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        self.profile_action_buttons["down"] = self._button(tools, "Down", "v", lambda: self.move_selected_account(1))
        self.profile_action_buttons["down"].grid(row=1, column=2, sticky="ew", padx=3, pady=3)

        self.account_status_line = tk.Label(panel, text="", bg=PANEL_ALT, fg=MUTED, font=("Segoe UI", 8), anchor="w", justify="left")
        self.account_status_line.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 8))

        self.account_scroll = ScrollFrame(panel, bg=PANEL_ALT)
        self.account_scroll.grid(row=6, column=0, sticky="nsew", padx=(10, 8), pady=(0, 12))

    def _build_calendar_area(self, master: tk.Misc) -> None:
        area = tk.Frame(master, bg=BG)
        area.grid(row=0, column=1, sticky="nsew")
        area.grid_rowconfigure(2, weight=1)
        area.grid_columnconfigure(0, weight=1)

        self.summary = tk.Frame(area, bg=BG)
        self.summary.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))
        for index in range(4):
            self.summary.grid_columnconfigure(index, weight=1)

        toolbar = tk.Frame(area, bg=BG)
        toolbar.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))
        toolbar.grid_columnconfigure(1, weight=1)
        self._button(toolbar, "Prev", "<", self.previous_month).grid(row=0, column=0, sticky="w", padx=(0, 8))
        title_box = tk.Frame(toolbar, bg=BG)
        title_box.grid(row=0, column=1, sticky="w")
        self.calendar_title = tk.Label(title_box, text="", bg=BG, fg=INK, font=("Segoe UI", 16, "bold"))
        self.calendar_title.pack(anchor="w")
        self.calendar_subtitle = tk.Label(title_box, text="", bg=BG, fg=MUTED, font=("Segoe UI", 8))
        self.calendar_subtitle.pack(anchor="w", pady=(3, 0))

        controls = tk.Frame(toolbar, bg=BG)
        controls.grid(row=0, column=2, sticky="e")
        self._button(controls, "Today", "T", self.go_today).pack(side="left", padx=3)
        self._button(controls, "Next", ">", self.next_month).pack(side="left", padx=3)
        self.mode_buttons: dict[str, tk.Button] = {}
        for label, mode in (("Month", "month"), ("Week", "week")):
            button = self._seg_button(controls, label, lambda m=mode: self.set_mode(m))
            button.pack(side="left", padx=3)
            self.mode_buttons[mode] = button

        self.calendar_panel = tk.Frame(area, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        self.calendar_panel.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 14))
        for column in range(7):
            self.calendar_panel.grid_columnconfigure(column, weight=1, uniform="calendar")

    def _build_details(self, master: tk.Misc) -> None:
        panel = tk.Frame(master, bg=PANEL_ALT, width=360, highlightbackground=LINE, highlightthickness=1)
        panel.grid(row=0, column=2, sticky="nsew")
        panel.grid_propagate(False)
        panel.grid_rowconfigure(2, weight=1)
        panel.grid_columnconfigure(0, weight=1)

        header = tk.Frame(panel, bg=PANEL_ALT)
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=14)
        header.grid_columnconfigure(0, weight=1)
        self.detail_title = tk.Label(header, text="Select a day", bg=PANEL_ALT, fg=INK, font=("Segoe UI", 12, "bold"))
        self.detail_title.grid(row=0, column=0, sticky="w")
        self.detail_subtitle = tk.Label(header, text="", bg=PANEL_ALT, fg=MUTED, font=("Segoe UI", 8), wraplength=290, justify="left")
        self.detail_subtitle.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.detail_status = self._status_badge(header, "Ready", "ready")
        self.detail_status.grid(row=0, column=1, rowspan=2, sticky="ne")

        self.detail_metrics = tk.Frame(panel, bg=PANEL_ALT)
        self.detail_metrics.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.detail_metrics.grid_columnconfigure(0, weight=1)
        self.detail_metrics.grid_columnconfigure(1, weight=1)

        self.breakdown_scroll = ScrollFrame(panel, bg=PANEL_ALT)
        self.breakdown_scroll.grid(row=2, column=0, sticky="nsew", padx=(14, 8), pady=(0, 8))

        self.actions_frame = tk.Frame(panel, bg=PANEL_ALT)
        self.actions_frame.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 8))
        for column in range(3):
            self.actions_frame.grid_columnconfigure(column, weight=1)
        self.account_action_buttons: dict[str, tk.Button] = {}
        button_specs = [
            ("coding", "Use in Coding", ">", self.use_selected_in_coding, "primary"),
            ("desktop", "Desktop", "S", self.switch_selected_desktop, "primary"),
            ("cli", "Open CLI", "C", self.open_selected_cli, "normal"),
            ("login", "Login", "L", self.login_selected, "normal"),
            ("device_login", "Device Login", "D", self.device_login_selected, "normal"),
            ("status", "Status", "I", self.status_selected, "normal"),
            ("doctor", "Doctor", "M", self.doctor_selected, "normal"),
            ("online", "Online", "O", self.online_selected, "primary"),
            ("dry_run", "Dry Run", "V", self.dry_run_selected_desktop_switch, "normal"),
            ("restore", "Restore", "B", self.restore_default_desktop_backup, "warning"),
            ("reset", "Use Reset", "!", self.use_reset_credit, "warning"),
            ("set_timer", "Set 5h", "T", self.set_selected_cooldown, "warning"),
            ("clear_timer", "Clear Timer", "X", self.clear_selected_cooldown, "normal"),
            ("seed", "Seed Config", "F", self.seed_selected_config, "normal"),
            ("home", "Open Home", "H", self.open_selected_home, "normal"),
            ("refresh", "Refresh", "U", self.refresh_selected_limits, "primary"),
        ]
        for index, (key, text, icon, command, variant) in enumerate(button_specs):
            button = self._button(self.actions_frame, text, icon, command, variant=variant)
            button.grid(row=index // 3, column=index % 3, sticky="ew", padx=4, pady=4)
            self.account_action_buttons[key] = button

        log_frame = tk.Frame(panel, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        log_frame.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 14))
        log_frame.grid_columnconfigure(0, weight=1)
        tk.Label(log_frame, text="Log", bg=PANEL, fg=INK, font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w", padx=8, pady=(6, 0))
        self.log_box = tk.Text(log_frame, height=7, bg=PANEL, fg=INK, relief="flat", font=("Consolas", 8), wrap="word")
        self.log_box.grid(row=1, column=0, sticky="ew", padx=8, pady=6)
        self.log_box.configure(state="disabled", insertbackground=INK, selectbackground=PRIMARY, selectforeground="white")

    def _button(self, master: tk.Misc, text: str, icon: str, command, variant: str = "normal") -> tk.Button:
        bg = PANEL
        fg = INK
        border = LINE_STRONG
        if variant == "primary":
            bg = PRIMARY
            fg = "white"
            border = PRIMARY
        elif variant == "warning":
            bg = AMBER_SOFT
            fg = AMBER
            border = AMBER
        button = tk.Button(
            master,
            text=f"{icon}  {text}",
            command=command,
            bg=bg,
            fg=fg,
            activebackground=bg,
            activeforeground=fg,
            relief="flat" if variant == "primary" else "solid",
            bd=1,
            highlightbackground=border,
            highlightcolor=border,
            font=("Segoe UI", 8),
            padx=8,
            pady=5,
            cursor="hand2",
        )
        self.buttons.append(button)
        return button

    def _seg_button(self, master: tk.Misc, text: str, command) -> tk.Button:
        button = tk.Button(
            master,
            text=text,
            command=command,
            bg=PANEL,
            fg=INK,
            activebackground=PANEL_ALT,
            activeforeground=INK,
            relief="solid",
            bd=1,
            highlightbackground=LINE_STRONG,
            highlightcolor=PRIMARY,
            font=("Segoe UI", 8),
            padx=10,
            pady=5,
            cursor="hand2",
        )
        self.buttons.append(button)
        return button

    def _coding_sidebar_button(
        self,
        master: tk.Misc,
        text: str,
        command,
        primary: bool = False,
        shortcut: str = "",
    ) -> tk.Button:
        coding = coding_palette(self.theme_name)
        label = text if not shortcut else f"{text}    {shortcut}"
        button = tk.Button(
            master,
            text=label,
            command=command,
            bg=coding["active"] if primary else coding["rail"],
            fg=coding["ink"],
            activebackground=coding["active"],
            activeforeground=coding["ink"],
            relief="flat",
            bd=0,
            highlightbackground=coding["line"],
            highlightthickness=0,
            font=("Segoe UI", 9, "bold" if primary else "normal"),
            anchor="w",
            padx=10,
            pady=7,
            cursor="hand2",
        )
        self.buttons.append(button)
        return button

    def _coding_header_button(self, master: tk.Misc, text: str, command) -> tk.Button:
        coding = coding_palette(self.theme_name)
        button = tk.Button(
            master,
            text=text,
            command=command,
            bg=coding["bg"],
            fg=coding["muted"],
            activebackground=coding["active"],
            activeforeground=coding["ink"],
            relief="flat",
            bd=0,
            highlightbackground=coding["line"],
            highlightthickness=1,
            font=("Segoe UI", 8),
            padx=9,
            pady=4,
            cursor="hand2",
        )
        self.buttons.append(button)
        return button

    def _coding_compact_button(
        self,
        master: tk.Misc,
        text: str,
        command,
        primary: bool = False,
    ) -> tk.Button:
        coding = coding_palette(self.theme_name)
        bg = PRIMARY if primary else PANEL
        fg = "white" if primary else MUTED
        if not primary:
            bg = coding["composer"]
            fg = coding["muted"]
        button = tk.Button(
            master,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=PRIMARY_HOVER if primary else coding["active"],
            activeforeground="white" if primary else coding["ink"],
            relief="flat",
            bd=0,
            highlightbackground=PRIMARY if primary else coding["line"],
            highlightthickness=1,
            font=("Segoe UI", 8, "bold" if primary else "normal"),
            padx=9,
            pady=4,
            cursor="hand2",
        )
        self.buttons.append(button)
        return button

    def _coding_input_focus_in(self, _event: tk.Event) -> None:
        if getattr(self, "coding_input_placeholder_active", False):
            self.coding_input.delete("1.0", "end")
            self.coding_input.configure(fg=coding_palette(self.theme_name)["ink"])
            self.coding_input_placeholder_active = False

    def _coding_input_focus_out(self, _event: tk.Event) -> None:
        if not self.coding_input.get("1.0", "end-1c").strip():
            coding = coding_palette(self.theme_name)
            self.coding_input.insert("1.0", "Ask for code changes or questions")
            self.coding_input.configure(fg=coding["muted"])
            self.coding_input_placeholder_active = True

    def _coding_send_key(self, _event: tk.Event | None = None) -> str:
        self.send_native_message()
        return "break"

    def _coding_input_return(self, event: tk.Event) -> str | None:
        shift_pressed = bool(getattr(event, "state", 0) & 0x0001)
        if shift_pressed:
            return None
        self.send_native_message()
        return "break"

    def _focus_coding_input_key(self, _event: tk.Event | None = None) -> str:
        if self.active_section == "coding" and hasattr(self, "coding_input"):
            self.coding_input.focus_set()
            self._coding_input_focus_in(tk.Event())
        return "break"

    def _focus_coding_search_key(self, _event: tk.Event | None = None) -> str:
        if self.active_section == "coding" and hasattr(self, "coding_search_entry"):
            self.coding_search_entry.focus_set()
            self._coding_search_focus_in(tk.Event())
        return "break"

    def _open_coding_project_key(self, _event: tk.Event | None = None) -> str:
        if self.active_section == "coding":
            self.add_coding_project()
        return "break"

    def _refresh_coding_key(self, _event: tk.Event | None = None) -> str:
        if self.active_section == "coding":
            self.refresh_native_threads()
            if self.coding_context_tab == "skills":
                self.refresh_native_skills(force=True)
        else:
            self.reload_profiles()
        return "break"

    def _coding_search_focus_in(self, _event: tk.Event) -> None:
        if getattr(self, "coding_search_placeholder_active", False):
            self.coding_search_entry.delete(0, "end")
            self.coding_search_entry.configure(fg=coding_palette(self.theme_name)["ink"])
            self.coding_search_placeholder_active = False

    def _coding_search_focus_out(self, _event: tk.Event) -> None:
        if not self.coding_search_entry.get().strip():
            coding = coding_palette(self.theme_name)
            self.coding_search_entry.insert(0, "Search")
            self.coding_search_entry.configure(fg=coding["muted"])
            self.coding_search_placeholder_active = True

    def _coding_search_term(self) -> str:
        if getattr(self, "coding_search_placeholder_active", False):
            return ""
        return self.coding_search_var.get().strip().lower()

    def _coding_project_thread_expansion_key(self, workspace: str) -> str:
        return str(Path(workspace)).lower()

    def toggle_coding_project_thread_expansion(self, workspace: str) -> None:
        key = self._coding_project_thread_expansion_key(workspace)
        if key in self.expanded_coding_project_threads:
            self.expanded_coding_project_threads.remove(key)
        else:
            self.expanded_coding_project_threads.add(key)
        self._render_coding_projects()

    def _add_native_attachments(self, paths: list[Path]) -> None:
        existing = {str(path).lower() for path in self.native_attachments}
        for path in paths:
            normalized = Path(path)
            key = str(normalized).lower()
            if key and key not in existing:
                self.native_attachments.append(normalized)
                existing.add(key)
        self._render_native_attachments()

    def remove_native_attachment(self, index: int) -> None:
        if 0 <= index < len(self.native_attachments):
            del self.native_attachments[index]
        self._render_native_attachments()

    def clear_native_attachments(self) -> None:
        self.native_attachments = []
        self._render_native_attachments()

    def _render_native_attachments(self) -> None:
        if not hasattr(self, "coding_attachment_tray"):
            return
        coding = coding_palette(self.theme_name)
        self._clear(self.coding_attachment_tray)
        attachments = list(self.native_attachments)
        if not attachments:
            self.coding_attachment_tray.grid_remove()
            return

        self.coding_attachment_tray.grid()
        row = tk.Frame(self.coding_attachment_tray, bg=coding["composer"])
        row.grid(row=0, column=0, sticky="ew")
        row.grid_columnconfigure(1, weight=1)
        tk.Label(
            row,
            text=native_attachment_status_text(attachments),
            bg=coding["composer"],
            fg=coding["faint"],
            font=("Segoe UI", 7, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(2, 8))

        chips = tk.Frame(row, bg=coding["composer"])
        chips.grid(row=0, column=1, sticky="w")
        max_visible = 4
        for index, path in enumerate(attachments[:max_visible]):
            chip = tk.Frame(chips, bg=coding["active"], highlightbackground=coding["line"], highlightthickness=1)
            chip.pack(side="left", padx=(0, 5))
            size_label = native_attachment_size_label(path)
            kind = native_attachment_kind(path)
            detail = size_label or kind
            tk.Label(
                chip,
                text=clip_text(f"{path.name} - {detail}", 34),
                bg=coding["active"],
                fg=coding["ink"],
                font=("Segoe UI", 7),
                anchor="w",
            ).pack(side="left", padx=(6, 3), pady=3)
            remove = tk.Button(
                chip,
                text="x",
                command=lambda item_index=index: self.remove_native_attachment(item_index),
                bg=coding["active"],
                fg=coding["muted"],
                activebackground=coding["panel"],
                activeforeground=coding["ink"],
                relief="flat",
                bd=0,
                font=("Segoe UI", 7, "bold"),
                padx=3,
                pady=1,
                cursor="hand2",
            )
            remove.pack(side="left", padx=(0, 3), pady=1)
        hidden = len(attachments) - max_visible
        if hidden > 0:
            tk.Label(
                chips,
                text=f"+{hidden} more",
                bg=coding["composer"],
                fg=coding["muted"],
                font=("Segoe UI", 7),
            ).pack(side="left", padx=(0, 5))

        clear = tk.Button(
            row,
            text="Clear",
            command=self.clear_native_attachments,
            bg=coding["composer"],
            fg=coding["muted"],
            activebackground=coding["active"],
            activeforeground=coding["ink"],
            relief="flat",
            bd=0,
            font=("Segoe UI", 7),
            padx=5,
            pady=1,
            cursor="hand2",
        )
        clear.grid(row=0, column=2, sticky="e", padx=(6, 2))
        if hasattr(self, "coding_composer_status") and not self.native_busy:
            self.coding_composer_status.configure(text=native_attachment_status_text(attachments))

    def _resize_coding_composer(self, event: tk.Event) -> None:
        if not hasattr(self, "coding_composer"):
            return
        width = max(620, min(820, int(event.width) - 96))
        self.coding_composer.configure(width=width)

    def toggle_coding_details(self) -> None:
        self.coding_details_visible = not self.coding_details_visible
        if self.coding_details_visible:
            self.coding_inspector.grid(row=0, column=2, sticky="nsew")
        else:
            self.coding_inspector.grid_remove()
        self._update_coding_details_button()

    def show_coding_skills(self) -> None:
        self.coding_details_visible = True
        self.coding_inspector.grid(row=0, column=2, sticky="nsew")
        self.set_coding_context_tab("skills")
        self._update_coding_details_button()

    def _update_coding_details_button(self) -> None:
        if not hasattr(self, "coding_details_button"):
            return
        selected = self.coding_details_visible
        self.coding_details_button.configure(
            bg=PANEL if selected else BG,
            fg=INK if selected else MUTED,
            activebackground=PANEL,
            activeforeground=INK,
        )
        if hasattr(self, "coding_skills_button"):
            skills_selected = selected and self.coding_context_tab == "skills"
            coding = coding_palette(self.theme_name)
            self.coding_skills_button.configure(
                bg=PANEL if skills_selected else coding["bg"],
                fg=INK if skills_selected else coding["muted"],
                activebackground=PANEL,
                activeforeground=INK,
            )

    def _clear_native_skills_cache(self) -> None:
        self.native_skills = []
        self.native_skills_profile_id = ""
        self.native_skills_workspace = ""
        self.native_skills_loading = False
        self.native_skills_error = ""

    def coding_workspace_choices(self) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        candidates = list(getattr(self, "coding_projects", []))
        candidates.extend(str(profile.get("workspace") or "") for profile in self.profiles)
        selected_profile = (
            self.coding_selected_profile()
            if "coding_profile_id" in self.__dict__
            else (self.profiles[0] if self.profiles else None)
        )
        if selected_profile is not None and provider_key(selected_profile) == "codex":
            candidates.extend(
                load_codex_saved_workspaces(
                    Path(str(selected_profile.get("codexHome") or DEFAULT_CODEX_HOME)),
                    include_default=True,
                )
            )
        for thread in getattr(self, "native_threads", []):
            if not isinstance(thread, dict):
                continue
            candidates.append(str(thread.get("cwd") or ""))
            candidates.append(str(thread.get("actualCwd") or ""))
        for raw in candidates:
            text = clean_windows_path_text(raw)
            if not text:
                continue
            normalized = str(Path(text).expanduser())
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            values.append(normalized)
        if not values:
            values.append(str(DEFAULT_WORKSPACE))
        return values

    def coding_profile_options(self) -> dict[str, str]:
        return self.account_manager.coding_profile_options()

    def coding_selected_profile(self) -> dict | None:
        return self.account_manager.coding_selected_profile()

    def _sync_coding_profile_combo(self) -> None:
        return self.account_manager._sync_coding_profile_combo()

    def _coding_control_preferences(self, profile: dict) -> dict[str, str]:
        return self.account_manager._coding_control_preferences(profile)

    def _save_coding_control_preference(self, profile: dict, key: str, value: str) -> None:
        return self.account_manager._save_coding_control_preference(profile, key, value)

    def _coding_model_rows(self, profile: dict) -> list[tuple[str, str]]:
        return self.account_manager._coding_model_rows(profile)

    def _coding_effort_rows(self, profile: dict, model_value: str) -> list[tuple[str, str]]:
        return self.account_manager._coding_effort_rows(profile, model_value)

    @staticmethod
    def _coding_option_label(options: dict[str, str], value: str) -> str:
        return next((label for label, option in options.items() if option == value), next(iter(options), ""))

    def _sync_coding_controls(self) -> None:
        return self.account_manager._sync_coding_controls()

    def coding_control_values(self) -> dict[str, str]:
        return self.account_manager.coding_control_values()

    def on_coding_control_changed(self, key: str) -> None:
        return self.account_manager.on_coding_control_changed(key)

    def _set_coding_control_value(self, key: str, value: str) -> bool:
        return self.account_manager._set_coding_control_value(key, value)

    def _effective_codex_model(self, profile: dict, controls: dict[str, str]) -> str:
        return self.account_manager._effective_codex_model(profile, controls)

    def _codex_plan_collaboration_mode(self, profile: dict, controls: dict[str, str]) -> dict:
        return self.account_manager._codex_plan_collaboration_mode(profile, controls)

    def _append_slash_notice(self, title: str, text: str, kind: str = "notice") -> None:
        self._upsert_native_activity(
            f"slash-{hashlib.sha1((title + text).encode('utf-8', errors='replace')).hexdigest()[:12]}",
            text,
            kind=kind,
            title=title,
        )

    def _run_codex_settings_update(self, settings: dict, success_text: str) -> None:
        transport = self.native_transport if isinstance(self.native_transport, CodexTransport) else None
        if transport is None:
            self._append_native_message("error", "Start or resume a Codex thread before changing this setting.")
            return

        def worker() -> dict:
            return transport.update_thread_settings(**settings)

        def success(_result: object) -> None:
            self._append_slash_notice("Codex settings", success_text)
            self.coding_composer_status.configure(text=success_text)

        self._run_native_worker(worker, success, self.native_generation)

    def _run_codex_goal_command(self, args: str, attachments: list[Path]) -> None:
        transport = self.native_transport if isinstance(self.native_transport, CodexTransport) else None
        if transport is None:
            self._append_native_message("error", "Start or resume a Codex thread before using /goal.")
            return
        command = args.strip()
        lowered = command.lower()

        def worker() -> tuple[str, dict]:
            if not command:
                return "view", transport.get_goal()
            if lowered == "clear":
                return "clear", transport.clear_goal()
            if lowered in {"pause", "paused"}:
                return "pause", transport.set_goal(status="paused")
            if lowered in {"resume", "active"}:
                return "resume", transport.set_goal(status="active")
            if len(command) > 4000:
                raise NativeTransportError("Goal objectives must be 4,000 characters or fewer.")
            return "set", transport.set_goal(objective=command, status="active")

        def success(result: object) -> None:
            action, payload = result if isinstance(result, tuple) else ("view", {})
            if action == "clear":
                cleared = bool(payload.get("cleared")) if isinstance(payload, dict) else False
                self._append_slash_notice("Codex goal", "Goal cleared." if cleared else "No active goal was cleared.")
                return
            goal = payload.get("goal") if isinstance(payload, dict) else None
            self._append_slash_notice("Codex goal", codex_goal_display_text(goal), kind="plan")
            if action == "set" and command:
                self._send_native_now(command, attachments)

        self._run_native_worker(worker, success, self.native_generation)

    def _run_coding_slash_command(self, parsed: dict, attachments: list[Path]) -> None:
        profile = self.coding_selected_profile()
        if profile is None:
            return
        name = str(parsed.get("name") or "").lower()
        args = str(parsed.get("args") or "").strip()
        provider = provider_key(profile)
        if name == "skills":
            self.show_coding_skills()
            self.refresh_native_skills(force=True)
            self.coding_composer_status.configure(text="Skills")
            return
        if name == "status":
            self.coding_details_visible = True
            self.set_coding_context_tab("session")
            controls = self.coding_control_values()
            text = (
                f"{provider_label(profile)} status\n"
                f"Model: {controls['model'] or 'Provider default'}\n"
                f"Effort: {controls['effort'] or 'Model default'}\n"
                f"Style: {self.coding_personality_var.get() or controls['personality'] or 'Provider default'}\n"
                f"Access: {self.coding_access_var.get() or controls['access']}\n"
                f"Thread: {self.native_thread_id or '-'}"
            )
            self._append_slash_notice("Session status", text)
            return
        if name == "diff":
            self.coding_details_visible = True
            self.set_coding_context_tab("files")
            self._append_slash_notice("Files", "Opened the file changes and diff view.")
            return
        if name == "personality":
            if provider != "codex":
                self._append_native_message("error", f"/personality is a Codex app-server setting; {provider_label(profile)} uses its native style controls.")
                return
            if not args:
                current = self.coding_control_values().get("personality") or "friendly"
                self._append_slash_notice("Codex personality", f"Current personality: {current}.")
                return
            value = args.split()[0].lower()
            if value not in {"friendly", "pragmatic", "none"}:
                self._append_native_message("error", "Use /personality friendly, /personality pragmatic, or /personality none.")
                return
            self._set_coding_control_value("personality", value)
            if isinstance(self.native_transport, CodexTransport):
                self._run_codex_settings_update({"personality": value}, f"Personality set to {value}.")
            else:
                self._append_slash_notice("Codex personality", f"Personality set to {value} for the next Codex thread.")
            return
        if name == "plan":
            controls = self.coding_control_values()
            if provider == "codex":
                plan_mode = self._codex_plan_collaboration_mode(profile, controls)
                if args:
                    self._send_native_now(args, attachments, {"collaborationMode": plan_mode})
                elif isinstance(self.native_transport, CodexTransport):
                    self._run_codex_settings_update({"collaborationMode": plan_mode}, "Plan mode enabled.")
                else:
                    self._append_native_message("error", "Start or resume a Codex thread before entering plan mode.")
                return
            if provider in {"claude", "cursor"}:
                self._set_coding_control_value("access", "plan")
                if args:
                    self._send_native_now(args, attachments)
                else:
                    self._append_slash_notice("Plan mode", f"{provider_label(profile)} access set to Plan for the next turn.")
                return
            self._append_native_message("error", f"{provider_label(profile)} does not expose a native plan-mode control in AI Account Hub yet.")
            return
        if name == "goal":
            if provider != "codex":
                self._append_native_message("error", "/goal is currently available for Codex app-server accounts only.")
                return
            self._run_codex_goal_command(args, attachments)
            return
        self._append_native_message("error", f"Unsupported slash command: /{name or '?'}")

    def _coding_slash_requires_thread(self, parsed: dict, attachments: list[Path]) -> bool:
        name = str(parsed.get("name") or "").lower()
        args = str(parsed.get("args") or "").strip()
        profile = self.coding_selected_profile()
        if profile is None:
            return False
        provider = provider_key(profile)
        if name == "goal" and provider == "codex":
            return True
        if name == "plan":
            if provider == "codex":
                return True
            return bool(args or attachments)
        return False

    def _handle_coding_slash_command(self, parsed: dict, attachments: list[Path]) -> bool:
        name = str(parsed.get("name") or "").lower()
        known = {"skills", "status", "diff", "personality", "plan", "goal"}
        self.coding_input.delete("1.0", "end")
        self.coding_input_placeholder_active = False
        self.native_attachments = []
        self._render_native_attachments()
        if name not in known:
            self._append_native_message("error", f"Unsupported slash command: /{name or '?'}")
            return True
        if self._coding_slash_requires_thread(parsed, attachments) and (
            not self.coding_session_active or self.native_transport is None
        ):
            self._native_pending_command = {"parsed": parsed, "attachments": attachments}
            self.prepare_native_thread()
            return True
        self._run_coding_slash_command(parsed, attachments)
        return True

    def refresh_native_models(self, force: bool = False) -> None:
        self._native_models_after_id = None
        profile = self.coding_selected_profile()
        if profile is None or self.native_models_loading:
            return
        expected_profile = profile_id(profile)
        if not force and self.native_models_profile_id == expected_profile and self.native_models:
            return
        provider = provider_key(profile)
        if provider == "claude":
            self.native_models = []
            self.native_models_profile_id = expected_profile
            self._sync_coding_controls()
            return
        self.native_models_loading = True
        workspace = Path(self.coding_workspace_var.get() or profile.get("workspace") or DEFAULT_WORKSPACE)

        def worker() -> list[dict]:
            if provider == "codex":
                transport = CodexTransport(
                    self.codex_cli_path,
                    Path(str(profile.get("codexHome") or DEFAULT_CODEX_HOME)),
                    workspace,
                    self._native_event_callback,
                )
                self._track_pending_transport(transport)
                try:
                    transport.connect()
                    return transport.list_models()
                finally:
                    transport.shutdown()
                    self._untrack_pending_transport(transport)
            executable = self.cursor_agent_path if provider == "cursor" else self.antigravity_cli_path
            if not executable:
                return []
            command = [executable, "models"]
            result = subprocess.run(
                command,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=25,
                creationflags=CREATE_NO_WINDOW,
            )
            return parse_native_model_listing(f"{result.stdout}\n{result.stderr}")

        def finish(models: list[dict]) -> None:
            self.native_models_loading = False
            current = self.coding_selected_profile()
            if current is None or profile_id(current) != expected_profile:
                return
            self.native_models = models
            self.native_models_profile_id = expected_profile
            self._sync_coding_controls()

        def run() -> None:
            try:
                models = worker()
            except Exception as error:
                self.native_diagnostics.append(f"Model list: {error}")
                models = []
            self._post_native_ui(lambda value=models: finish(value))

        threading.Thread(target=run, name="ai-hub-native-models", daemon=True).start()

    def refresh_native_skills(self, force: bool = False) -> None:
        profile = self.coding_selected_profile()
        if profile is None or self.native_skills_loading:
            return
        workspace = Path(self.coding_workspace_var.get() or profile.get("workspace") or DEFAULT_WORKSPACE)
        expected_profile = profile_id(profile)
        expected_workspace = str(workspace).lower()
        if provider_key(profile) != "codex":
            self.native_skills = []
            self.native_skills_profile_id = expected_profile
            self.native_skills_workspace = expected_workspace
            self.native_skills_error = f"{provider_label(profile)} skills are managed by that provider outside Codex app-server."
            if self.coding_details_visible and self.coding_context_tab == "skills":
                self._render_coding_context()
            return
        if (
            not force
            and self.native_skills_profile_id == expected_profile
            and self.native_skills_workspace == expected_workspace
            and (self.native_skills or self.native_skills_error)
        ):
            return
        self.native_skills_loading = True
        self.native_skills_error = ""
        if self.coding_details_visible and self.coding_context_tab == "skills":
            self._render_coding_context()

        def worker() -> list[dict]:
            if not self.codex_cli_path:
                raise NativeTransportError(self.codex_cli_error or "Codex CLI was not found.")
            codex_home = Path(str(profile.get("codexHome") or DEFAULT_CODEX_HOME))
            if not (codex_home / "auth.json").exists():
                raise NativeTransportError(f"{profile.get('name', 'Account')} is not logged in. Use Accounts > Login first.")
            transport = CodexTransport(
                self.codex_cli_path,
                codex_home,
                workspace,
                self._native_event_callback,
            )
            self._track_pending_transport(transport)
            try:
                transport.connect()
                return transport.list_skills(workspace, force_reload=force)
            finally:
                transport.shutdown()
                self._untrack_pending_transport(transport)

        def finish(entries: list[dict], error: str = "") -> None:
            self.native_skills_loading = False
            current = self.coding_selected_profile()
            if current is None or profile_id(current) != expected_profile:
                return
            if str(Path(self.coding_workspace_var.get() or DEFAULT_WORKSPACE)).lower() != expected_workspace:
                return
            self.native_skills = entries
            self.native_skills_profile_id = expected_profile
            self.native_skills_workspace = expected_workspace
            self.native_skills_error = error
            if self.coding_details_visible and self.coding_context_tab == "skills":
                self._render_coding_context()

        def run() -> None:
            try:
                entries = worker()
                error = ""
            except Exception as exc:
                entries = []
                error = str(exc)
            self._post_native_ui(lambda value=entries, detail=error: finish(value, detail))

        threading.Thread(target=run, name="ai-hub-native-skills", daemon=True).start()

    def set_native_skill_enabled(self, skill: dict, enabled: bool) -> None:
        profile = self.coding_selected_profile()
        if profile is None or provider_key(profile) != "codex" or self.native_skills_loading:
            return
        metadata = skill if isinstance(skill, dict) else {}
        name = str(metadata.get("name") or "").strip()
        path = clean_windows_path_text(metadata.get("path"))
        workspace = Path(self.coding_workspace_var.get() or profile.get("workspace") or DEFAULT_WORKSPACE)
        expected_profile = profile_id(profile)
        self.native_skills_loading = True
        self.native_skills_error = ""
        if self.coding_details_visible and self.coding_context_tab == "skills":
            self._render_coding_context()

        def worker() -> None:
            if not self.codex_cli_path:
                raise NativeTransportError(self.codex_cli_error or "Codex CLI was not found.")
            transport = CodexTransport(
                self.codex_cli_path,
                Path(str(profile.get("codexHome") or DEFAULT_CODEX_HOME)),
                workspace,
                self._native_event_callback,
            )
            self._track_pending_transport(transport)
            try:
                transport.connect()
                transport.write_skill_config(enabled=enabled, name=name, path=path)
            finally:
                transport.shutdown()
                self._untrack_pending_transport(transport)

        def finish(error: str = "") -> None:
            self.native_skills_loading = False
            current = self.coding_selected_profile()
            if current is None or profile_id(current) != expected_profile:
                return
            if error:
                self.native_skills_error = error
                self._render_coding_context()
                return
            self.refresh_native_skills(force=True)

        def run() -> None:
            try:
                worker()
                error = ""
            except Exception as exc:
                error = str(exc)
            self._post_native_ui(lambda detail=error: finish(detail))

        threading.Thread(target=run, name="ai-hub-native-skill-config", daemon=True).start()

    def on_coding_profile_changed(self) -> None:
        selected = self.coding_profile_option_map.get(self.coding_profile_var.get(), "")
        if not selected or selected == self.coding_profile_id:
            self._sync_coding_profile_combo()
            return
        if not self._confirm_native_context_change("change coding account"):
            self._sync_coding_profile_combo()
            return
        self.coding_profile_id = selected
        self.close_native_transport()
        self.native_models = []
        self.native_models_profile_id = ""
        self._clear_native_skills_cache()
        self._render_coding()
        self.refresh_native_threads()
        self.refresh_native_models(force=True)

    def show_section(self, section: str, render_page: bool = True) -> None:
        if section not in {"coding", "accounts"}:
            return
        self.active_section = section
        if section == "coding":
            self.topbar.grid_remove()
            self.statusbar.grid_remove()
            self.account_page.grid_remove()
            self.coding_page.grid(row=0, column=0, sticky="nsew")
            self.account_top_actions.pack_forget()
            self.coding_top_actions.pack(side="left", before=self.theme_button)
            subtitle = "Projects and native coding sessions"
        else:
            self.topbar.grid(row=0, column=0, sticky="nsew")
            self.statusbar.grid(row=2, column=0, sticky="ew")
            self.coding_page.grid_remove()
            self.account_page.grid(row=0, column=0, sticky="nsew")
            self.coding_top_actions.pack_forget()
            self.account_top_actions.pack(side="left", before=self.theme_button)
            subtitle = "Accounts, limits and usage history"
        self.section_subtitle_label.configure(text=subtitle)
        self._update_section_navigation()
        self._update_status_context()
        if render_page:
            if section == "coding":
                self._render_coding()
            else:
                self.render()

    def _update_section_navigation(self) -> None:
        for section, button in getattr(self, "section_buttons", {}).items():
            selected = section == self.active_section
            button.configure(
                bg=PRIMARY if selected else PANEL,
                fg="white" if selected else INK,
                activebackground=PRIMARY_HOVER if selected else PANEL_ALT,
                activeforeground="white" if selected else INK,
                relief="flat" if selected else "solid",
            )

    def _update_status_context(self) -> None:
        if not hasattr(self, "status_context_label"):
            return
        if self.active_section == "accounts":
            text = self.codex_cli_path or self.codex_cli_error or "codex.exe not found"
        else:
            profile = self.coding_selected_profile()
            provider = provider_label(profile) if profile is not None else "No harness"
            workspace = Path(self.coding_workspace_var.get() or DEFAULT_WORKSPACE).name
            text = f"{provider} | {workspace} | Native passthrough"
        self.status_context_label.configure(text=text)

    def add_coding_project(self) -> None:
        selected = filedialog.askdirectory(
            parent=self,
            title="Open coding project",
            initialdir=self.coding_workspace_var.get() or str(DEFAULT_WORKSPACE),
            mustexist=True,
        )
        if not selected:
            return
        normalized = str(Path(selected))
        if normalized.lower() not in {value.lower() for value in self.coding_projects}:
            self.coding_projects.insert(0, normalized)
            self.settings["codingProjects"] = list(self.coding_projects)
            save_settings(self.settings)
        self.coding_workspace_var.set(normalized)
        self.status_var.set(f"Opened project: {normalized}")
        self._render_coding()

    def select_coding_workspace(self, workspace: str) -> None:
        if workspace == self.coding_workspace_var.get():
            return
        if not self._confirm_native_context_change("change project"):
            return
        self.close_native_transport()
        self.coding_workspace_var.set(workspace)
        self.coding_session_active = False
        self._clear_native_skills_cache()
        self.status_var.set(f"Selected project: {workspace}")
        self._render_coding()
        self.refresh_native_threads()

    def _confirm_native_context_change(self, action: str) -> bool:
        if not self.native_busy:
            return True
        return messagebox.askyesno(
            "Stop active coding turn?",
            f"A native coding turn is still running. Stop it and {action}?",
            parent=self,
        )

    def use_selected_in_coding(self) -> None:
        profile = self.selected_required()
        if profile is None:
            return
        if not self._confirm_native_context_change("use this account in Coding"):
            return
        self.close_native_transport()
        self.coding_profile_id = profile_id(profile)
        workspace = str(profile.get("workspace") or DEFAULT_WORKSPACE)
        self.coding_workspace_var.set(workspace)
        if workspace.lower() not in {value.lower() for value in self.coding_projects}:
            self.coding_projects.insert(0, workspace)
            self.settings["codingProjects"] = list(self.coding_projects)
            save_settings(self.settings)
        self._sync_coding_profile_combo()
        self.native_messages = []
        self.native_threads = []
        self.native_models = []
        self.native_models_profile_id = ""
        self._clear_native_skills_cache()
        self.show_section("coding")
        self.status_var.set(f"Using {profile.get('name', 'Account')} in Coding.")
        self.refresh_native_threads()
        self.refresh_native_models(force=True)

    def prepare_native_thread(self) -> None:
        return self.transport_bridge.prepare_native_thread()

    def attach_native_files(self) -> None:
        return self.transport_bridge.attach_native_files()

    def send_native_message(self) -> None:
        return self.transport_bridge.send_native_message()

    def _send_native_now(self, text: str, attachments: list[Path], turn_options: dict | None = None) -> None:
        return self.transport_bridge._send_native_now(text, attachments, turn_options)

    def stop_native_turn(self) -> None:
        return self.transport_bridge.stop_native_turn()

    def close_native_transport(self) -> None:
        return self.transport_bridge.close_native_transport()

    def _ensure_claude_permission_bridge(self) -> tuple[str, str]:
        return self.transport_bridge._ensure_claude_permission_bridge()

    def _shutdown_claude_permission_bridge(self) -> None:
        return self.transport_bridge._shutdown_claude_permission_bridge()

    def _handle_claude_permission_payload(self, payload: dict) -> dict:
        return self.transport_bridge._handle_claude_permission_payload(payload)

    def _claude_permission_allow(self, payload: dict) -> dict:
        return self.transport_bridge._claude_permission_allow(payload)

    def _claude_permission_deny(self, payload: dict, message: str, interrupt: bool = False) -> dict:
        return self.transport_bridge._claude_permission_deny(payload, message, interrupt)

    def _claude_permission_summary(self, payload: dict) -> str:
        return self.transport_bridge._claude_permission_summary(payload)

    def _ask_claude_permission(self, payload: dict) -> dict:
        return self.transport_bridge._ask_claude_permission(payload)

    def _ask_claude_user_question(self, payload: dict) -> dict:
        return self.transport_bridge._ask_claude_user_question(payload)

    def _ask_claude_plan_review(self, payload: dict) -> dict:
        return self.transport_bridge._ask_claude_plan_review(payload)

    def _claude_tool_activity_text(self, name: str, tool_input: object) -> str:
        return self.transport_bridge._claude_tool_activity_text(name, tool_input)

    def _codex_activity_fields(self, item: dict) -> dict:
        return self.transport_bridge._codex_activity_fields(item)

    def _format_claude_rate_limit_event(self, info: dict) -> str:
        return self.transport_bridge._format_claude_rate_limit_event(info)

    def _apply_claude_rate_limit_event(self, info: dict) -> None:
        return self.transport_bridge._apply_claude_rate_limit_event(info)

    def _track_pending_transport(self, transport: CodexTransport | StreamJsonTransport) -> None:
        return self.transport_bridge._track_pending_transport(transport)

    def _untrack_pending_transport(self, transport: CodexTransport | StreamJsonTransport) -> None:
        return self.transport_bridge._untrack_pending_transport(transport)

    def _stop_transport(self, transport: CodexTransport | StreamJsonTransport) -> None:
        return self.transport_bridge._stop_transport(transport)

    def close_application(self) -> None:
        self.destroy()

    def destroy(self) -> None:
        if self._closing:
            return
        self._closing = True
        for callback_id in (
            self._native_queue_after_id,
            self._native_refresh_after_id,
            self._native_models_after_id,
            self._native_render_after_id,
            self._tick_after_id,
            self._coding_scroll_after_id,
        ):
            if callback_id:
                try:
                    self.after_cancel(callback_id)
                except tk.TclError:
                    pass
        self.close_native_transport()
        self._shutdown_claude_permission_bridge()
        super().destroy()

    def _create_native_transport(self, profile: dict, workspace: Path, session_id: str = "") -> CodexTransport | StreamJsonTransport:
        return self.transport_bridge._create_native_transport(profile, workspace, session_id)

    def _native_transport_key(self, profile: dict, workspace: Path) -> str:
        return self.transport_bridge._native_transport_key(profile, workspace)

    def _thread_updated_at(self, item: dict) -> float:
        return self.transport_bridge._thread_updated_at(item)

    def _thread_workspace_key(self, item: dict, fallback: object = "") -> str:
        return self.transport_bridge._thread_workspace_key(item, fallback)

    def _saved_native_thread_ref(self, profile: dict, workspace: Path) -> dict | None:
        return self.transport_bridge._saved_native_thread_ref(profile, workspace)

    def _merge_saved_thread_refs(self, threads: list[dict], profile: dict) -> list[dict]:
        return self.transport_bridge._merge_saved_thread_refs(threads, profile)

    def _collapse_native_threads(self, threads: list[dict], profile: dict) -> list[dict]:
        return self.transport_bridge._collapse_native_threads(threads, profile)

    def _run_native_worker(self, worker, success, generation: int) -> None:
        return self.transport_bridge._run_native_worker(worker, success, generation)

    def _native_worker_failed(self, error: str, generation: int) -> None:
        return self.transport_bridge._native_worker_failed(error, generation)

    def refresh_native_threads(self) -> None:
        return self.transport_bridge.refresh_native_threads()

    def _native_threads_failed(self, error: str) -> None:
        return self.transport_bridge._native_threads_failed(error)

    def select_native_thread(self, thread: dict) -> None:
        return self.transport_bridge.select_native_thread(thread)

    def _native_event_callback(self, message: dict) -> None:
        return self.transport_bridge._native_event_callback(message)

    def _post_native_ui(self, callback) -> None:
        return self.transport_bridge._post_native_ui(callback)

    def _drain_native_ui_queue(self) -> None:
        return self.transport_bridge._drain_native_ui_queue()

    def _handle_native_event(self, message: dict) -> None:
        return self.transport_bridge._handle_native_event(message)

    def _schedule_native_render(self, full: bool = False) -> None:
        return self.transport_bridge._schedule_native_render(full)

    def _flush_native_render(self) -> None:
        return self.transport_bridge._flush_native_render()

    def _handle_stream_event(self, provider: str, event: dict) -> None:
        return self.transport_bridge._handle_stream_event(provider, event)

    def _center_dialog(self, dialog: tk.Toplevel) -> None:
        dialog.update_idletasks()
        x = self.winfo_rootx() + max(40, (self.winfo_width() - dialog.winfo_width()) // 2)
        y = self.winfo_rooty() + max(40, (self.winfo_height() - dialog.winfo_height()) // 3)
        dialog.geometry(f"+{x}+{y}")

    def _native_request_dialog(
        self,
        title: str,
        prompt: str,
        choices: list[tuple[str, str, str]],
        secret: bool = False,
        allow_text: bool = True,
    ) -> str | None:
        result: dict[str, str | None] = {"value": None}
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.configure(bg=PANEL)
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        body = tk.Frame(dialog, bg=PANEL)
        body.pack(fill="both", expand=True, padx=18, pady=16)
        tk.Label(body, text=title, bg=PANEL, fg=INK, font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x")
        tk.Label(
            body,
            text=prompt,
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
            wraplength=480,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(8, 12))

        if choices:
            choice_frame = tk.Frame(body, bg=PANEL)
            choice_frame.pack(fill="x", pady=(0, 10))
            for label, value, description in choices:
                row = tk.Frame(choice_frame, bg=PANEL_ALT, highlightbackground=LINE, highlightthickness=1)
                row.pack(fill="x", pady=3)
                row.grid_columnconfigure(0, weight=1)
                command = lambda selected=value: (result.update({"value": selected}), dialog.destroy())
                button = tk.Button(
                    row,
                    text=label,
                    command=command,
                    bg=PANEL_ALT,
                    fg=INK,
                    activebackground=PANEL,
                    activeforeground=INK,
                    relief="flat",
                    bd=0,
                    font=("Segoe UI", 9, "bold"),
                    anchor="w",
                    padx=9,
                    pady=5,
                    cursor="hand2",
                )
                button.grid(row=0, column=0, sticky="ew")
                if description:
                    tk.Label(
                        row,
                        text=description,
                        bg=PANEL_ALT,
                        fg=MUTED,
                        font=("Segoe UI", 8),
                        wraplength=440,
                        justify="left",
                        anchor="w",
                        padx=9,
                        pady=4,
                    ).grid(row=1, column=0, sticky="ew")

        entry: tk.Entry | None = None
        if allow_text:
            entry = tk.Entry(
                body,
                bg=BG,
                fg=INK,
                insertbackground=INK,
                relief="solid",
                bd=1,
                font=("Segoe UI", 9),
                show="*" if secret else "",
            )
            entry.pack(fill="x", ipady=7, pady=(2, 12))

        buttons = tk.Frame(body, bg=PANEL)
        buttons.pack(fill="x")

        def submit() -> None:
            value = entry.get().strip() if entry is not None else ""
            result["value"] = value
            dialog.destroy()

        def cancel() -> None:
            result["value"] = None
            dialog.destroy()

        tk.Button(
            buttons,
            text="Cancel",
            command=cancel,
            bg=PANEL,
            fg=INK,
            relief="solid",
            bd=1,
            padx=12,
            pady=5,
        ).pack(side="right", padx=(6, 0))
        if allow_text:
            tk.Button(
                buttons,
                text="Send",
                command=submit,
                bg=PRIMARY,
                fg="white",
                activebackground=PRIMARY_HOVER,
                activeforeground="white",
                relief="flat",
                bd=0,
                padx=14,
                pady=6,
            ).pack(side="right")

        dialog.protocol("WM_DELETE_WINDOW", cancel)
        self._center_dialog(dialog)
        if entry is not None:
            entry.focus_set()
            dialog.bind("<Return>", lambda _event: submit())
        dialog.bind("<Escape>", lambda _event: cancel())
        self.wait_window(dialog)
        return result["value"]

    def _native_plan_review_dialog(self, plan: str, plan_file_path: str = "") -> str | None:
        result: dict[str, str | None] = {"value": None}
        dialog = tk.Toplevel(self)
        dialog.title("Claude plan review")
        dialog.configure(bg=PANEL)
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(True, True)

        body = tk.Frame(dialog, bg=PANEL)
        body.pack(fill="both", expand=True, padx=18, pady=16)
        body.grid_rowconfigure(2, weight=1)
        body.grid_columnconfigure(0, weight=1)

        tk.Label(
            body,
            text="Claude plan review",
            bg=PANEL,
            fg=INK,
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        subtitle = "Review or edit the plan before Claude exits plan mode."
        if plan_file_path:
            subtitle = f"{subtitle}\n{plan_file_path}"
        tk.Label(
            body,
            text=subtitle,
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8),
            wraplength=760,
            justify="left",
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", pady=(7, 10))

        editor_frame = tk.Frame(body, bg=PANEL, highlightbackground=LINE_STRONG, highlightthickness=1)
        editor_frame.grid(row=2, column=0, sticky="nsew")
        editor_frame.grid_rowconfigure(0, weight=1)
        editor_frame.grid_columnconfigure(0, weight=1)
        editor = tk.Text(
            editor_frame,
            width=92,
            height=24,
            bg=BG,
            fg=INK,
            insertbackground=INK,
            selectbackground=PRIMARY,
            selectforeground="white",
            relief="flat",
            font=("Consolas", 9),
            wrap="word",
            undo=True,
            padx=10,
            pady=10,
        )
        scrollbar = ttk.Scrollbar(editor_frame, orient="vertical", command=editor.yview, style="Vertical.TScrollbar")
        editor.configure(yscrollcommand=scrollbar.set)
        editor.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        editor.insert("1.0", plan)

        buttons = tk.Frame(body, bg=PANEL)
        buttons.grid(row=3, column=0, sticky="ew", pady=(12, 0))

        def approve() -> None:
            result["value"] = editor.get("1.0", "end-1c")
            dialog.destroy()

        def deny() -> None:
            result["value"] = None
            dialog.destroy()

        tk.Button(
            buttons,
            text="Deny",
            command=deny,
            bg=PANEL,
            fg=INK,
            relief="solid",
            bd=1,
            padx=12,
            pady=5,
        ).pack(side="right", padx=(6, 0))
        tk.Button(
            buttons,
            text="Approve plan",
            command=approve,
            bg=PRIMARY,
            fg="white",
            activebackground=PRIMARY_HOVER,
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=14,
            pady=6,
        ).pack(side="right")

        dialog.protocol("WM_DELETE_WINDOW", deny)
        dialog.bind("<Escape>", lambda _event: deny())
        dialog.geometry("860x620")
        self._center_dialog(dialog)
        editor.focus_set()
        self.wait_window(dialog)
        return result["value"]

    def _handle_native_server_request(self, message: dict) -> None:
        return self.transport_bridge._handle_native_server_request(message)

    def _capture_stream_session(self) -> None:
        return self.transport_bridge._capture_stream_session()

    def _save_active_native_thread(self) -> None:
        return self.transport_bridge._save_active_native_thread()

    def _save_native_thread_ref(self, profile: dict, workspace: Path, session_id: str, title: str) -> None:
        return self.transport_bridge._save_native_thread_ref(profile, workspace, session_id, title)

    def _append_native_message(
        self,
        role: str,
        text: str,
        native_id: str = "",
        render: bool = True,
        **fields,
    ) -> None:
        return self.transport_bridge._append_native_message(role, text, native_id, render, **fields)

    def _append_native_delta(self, native_id: str, delta: str, render: bool = True) -> None:
        return self.transport_bridge._append_native_delta(native_id, delta, render)

    def _finish_native_assistant_message(
        self,
        stream_native_id: str,
        result_native_id: str,
        text: str,
        render: bool = True,
    ) -> None:
        return self.transport_bridge._finish_native_assistant_message(stream_native_id, result_native_id, text, render)

    def _upsert_native_activity(self, native_id: str, text: str, render: bool = True, **fields) -> None:
        return self.transport_bridge._upsert_native_activity(native_id, text, render, **fields)

    def _append_native_activity_delta(
        self,
        native_id: str,
        delta: str,
        prefix: str = "Output",
        render: bool = True,
    ) -> None:
        return self.transport_bridge._append_native_activity_delta(native_id, delta, prefix, render)

    def _capture_native_file_changes(self, item: dict) -> None:
        return self.transport_bridge._capture_native_file_changes(item)

    def _capture_activity_file_fields(self, fields: dict, status: str = "") -> None:
        return self.transport_bridge._capture_activity_file_fields(fields, status)

    def _restore_native_file_context(self, messages: list[dict]) -> None:
        return self.transport_bridge._restore_native_file_context(messages)

    def _native_error_text(self, error: object) -> str:
        return self.transport_bridge._native_error_text(error)

    def _timestamp_from_iso(self, value: object) -> float:
        return self.transport_bridge._timestamp_from_iso(value)

    def set_coding_context_tab(self, tab: str) -> None:
        return self.ui_renderer.set_coding_context_tab(tab)

    def _render_coding(self) -> None:
        return self.ui_renderer._render_coding()

    def _render_coding_short_limit(self) -> None:
        return self.ui_renderer._render_coding_short_limit()

    def _render_coding_projects(self) -> None:
        return self.ui_renderer._render_coding_projects()

    def _render_coding_sidebar_account(self) -> None:
        return self.ui_renderer._render_coding_sidebar_account()

    def _configure_coding_stream_tags(self) -> None:
        return self.ui_renderer._configure_coding_stream_tags()

    def _insert_coding_inline(self, text: str, base_tag: str) -> None:
        return self.ui_renderer._insert_coding_inline(text, base_tag)

    def _insert_coding_markdown(self, value: str, muted: bool = False) -> None:
        return self.ui_renderer._insert_coding_markdown(value, muted)

    def _open_coding_link(self, target: str) -> None:
        return self.ui_renderer._open_coding_link(target)

    def _coding_image_refs(self, message: dict, text: str = "") -> list[dict]:
        return self.ui_renderer._coding_image_refs(message, text)

    def _load_coding_image(self, ref: dict, max_width: int = 520, max_height: int = 260) -> tk.PhotoImage | None:
        return self.ui_renderer._load_coding_image(ref, max_width, max_height)

    def _image_ref_label(self, ref: dict) -> str:
        return self.ui_renderer._image_ref_label(ref)

    def _build_image_preview(self, master: tk.Misc, ref: dict, bg: str, max_width: int = 520, max_height: int = 260) -> tk.Frame:
        return self.ui_renderer._build_image_preview(master, ref, bg, max_width, max_height)

    def _insert_coding_image_card(self, ref: dict, title: str = "Image") -> None:
        return self.ui_renderer._insert_coding_image_card(ref, title)

    def _insert_coding_user_message(self, value: str, message: dict | None = None) -> None:
        return self.ui_renderer._insert_coding_user_message(value, message)

    def _activity_kind_from_text(self, value: str) -> str:
        return self.ui_renderer._activity_kind_from_text(value)

    def _insert_diff_text(self, widget: tk.Text, value: str) -> None:
        return self.ui_renderer._insert_diff_text(widget, value)

    def _insert_plan_rows(self, master: tk.Misc, message: dict, value: str, bg: str) -> bool:
        return self.ui_renderer._insert_plan_rows(master, message, value, bg)

    def _insert_coding_activity_card(self, message: dict, value: str) -> None:
        return self.ui_renderer._insert_coding_activity_card(message, value)

    def _plain_coding_activity_text(self, message: dict | None, value: str) -> str:
        return self.ui_renderer._plain_coding_activity_text(message, value)

    def _insert_coding_activity(self, role: str, value: str, message: dict | None = None) -> None:
        return self.ui_renderer._insert_coding_activity(role, value, message)

    def _coding_stream_message_key(self, index: int, message: dict) -> str:
        return self.ui_renderer._coding_stream_message_key(index, message)

    def _coding_message_uses_windows(self, message: dict, text: str) -> bool:
        return self.ui_renderer._coding_message_uses_windows(message, text)

    def _coding_stream_state(self) -> tuple[tuple[str, ...], dict[str, str], dict[str, int]]:
        return self.ui_renderer._coding_stream_state()

    def _try_update_coding_stream_delta(self) -> bool:
        return self.ui_renderer._try_update_coding_stream_delta()

    def _try_append_coding_stream_messages(self) -> bool:
        return self.ui_renderer._try_append_coding_stream_messages()

    def _render_coding_stream(self) -> None:
        return self.ui_renderer._render_coding_stream()

    def _scroll_coding_to_bottom(self) -> None:
        return self.ui_renderer._scroll_coding_to_bottom()

    def _render_coding_context(self) -> None:
        return self.ui_renderer._render_coding_context()

    def _render_coding_session(self, master: tk.Misc) -> None:
        return self.ui_renderer._render_coding_session(master)

    def _native_skill_rows(self) -> tuple[list[dict], list[str]]:
        return self.ui_renderer._native_skill_rows()

    def _render_coding_skills(self, master: tk.Misc) -> None:
        return self.ui_renderer._render_coding_skills(master)

    def _render_coding_files(self, master: tk.Misc) -> None:
        return self.ui_renderer._render_coding_files(master)

    def _render_coding_terminal(self, master: tk.Misc) -> None:
        return self.ui_renderer._render_coding_terminal(master)

    def _status_badge(self, master: tk.Misc, text: str, state: str) -> tk.Label:
        fg, bg = status_colors(state)
        return tk.Label(master, text=text, bg=bg, fg=fg, font=("Segoe UI", 8, "bold"), padx=8, pady=4)

    def _mini_dot(self, master: tk.Misc, profile_or_provider: dict | str) -> tk.Widget:
        provider = provider_key(profile_or_provider) if isinstance(profile_or_provider, dict) else profile_or_provider
        return self._service_icon(master, provider)

    def _widget_background(self, master: tk.Misc, fallback: str = PANEL) -> str:
        try:
            value = str(master.cget("bg"))
            return value if value else fallback
        except tk.TclError:
            return fallback

    def _service_icon(self, master: tk.Misc, provider: str, size: int = 30) -> tk.Widget:
        provider = provider_key({"provider": provider})
        color = PROVIDER_COLORS.get(provider, "#666")
        host_bg = self._widget_background(master, PANEL)
        icon_path = ""
        if provider == "codex":
            icon_path = self.codex_icon_path
        elif provider == "claude":
            icon_path = self.claude_icon_path
        elif provider == "cursor":
            icon_path = self.cursor_icon_path
        elif provider == "antigravity":
            icon_path = self.antigravity_icon_path
        if icon_path:
            try:
                cache_key = (provider, size, icon_path)
                image = self.icon_images.get(cache_key)
                if image is None:
                    image = tk.PhotoImage(file=icon_path)
                    factor = max(1, min(image.width(), image.height()) // max(1, size))
                    if factor > 1:
                        image = image.subsample(factor, factor)
                    self.icon_images[cache_key] = image
                label = tk.Label(master, image=image, bg=host_bg, width=size, height=size, bd=0, highlightthickness=0)
                label.image = image
                return label
            except tk.TclError:
                pass

        icon_bg = host_bg if provider in {"claude", "cursor", "antigravity"} else color
        canvas = tk.Canvas(master, width=size, height=size, bg=icon_bg, highlightthickness=0, bd=0)
        pad = max(4, size // 7)
        mid = size / 2

        if provider == "codex":
            points = [mid, pad, size - pad, mid, mid, size - pad, pad, mid]
            canvas.create_polygon(points, outline="white", fill="", width=2, joinstyle="round")
            canvas.create_line(mid, pad + 4, size - pad - 4, mid, mid, size - pad - 4, pad + 4, mid, mid, pad + 4, fill="white", width=1)
        elif provider == "claude":
            ray = size * 0.32
            inner = size * 0.11
            claude_orange = "#d97742"
            for angle in range(0, 360, 20):
                rad = angle * 3.14159265 / 180
                x1 = mid + inner * math.cos(rad)
                y1 = mid + inner * math.sin(rad)
                x2 = mid + ray * math.cos(rad)
                y2 = mid + ray * math.sin(rad)
                canvas.create_line(x1, y1, x2, y2, fill=claude_orange, width=2, capstyle="round")
            canvas.create_oval(mid - 3, mid - 3, mid + 3, mid + 3, fill=claude_orange, outline="")
        elif provider == "cursor":
            cursor = [pad, pad, size - pad, mid, mid + 2, mid + 2, size - pad - 1, size - pad, mid, mid + 5, pad, size - pad]
            canvas.create_polygon(cursor, fill=INK, outline="")
            canvas.create_line(mid + 1, mid + 4, size - pad - 2, size - pad - 2, fill=PANEL, width=2)
        elif provider == "antigravity":
            canvas.create_text(mid, mid, text="A", fill=PROVIDER_COLORS["antigravity"], font=("Segoe UI", 13, "bold"))
        elif provider == "api":
            canvas.create_text(mid, mid, text="ALL", fill="white", font=("Segoe UI", 7, "bold"))
        else:
            canvas.create_text(mid, mid, text=PROVIDER_INITIALS.get(provider, provider[:3].upper() if provider else "?"), fill="white", font=("Segoe UI", 7, "bold"))

        return canvas

    def visible_profiles(self) -> list[dict]:
        return self.account_manager.visible_profiles()

    def sorted_profiles(self, profiles: list[dict]) -> list[dict]:
        return self.account_manager.sorted_profiles(profiles)

    def profile_state(self, profile: dict) -> str:
        return self.account_manager.profile_state(profile)

    def selected_profile_obj(self) -> dict | None:
        return self.account_manager.selected_profile_obj()

    def active_desktop_marker(self) -> dict:
        return self.account_manager.active_desktop_marker()

    def active_desktop_home(self) -> str:
        return self.account_manager.active_desktop_home()

    def reset_markers(self, days: list[dt.date] | None = None, filter_selected: bool = False) -> dict[str, list[str]]:
        markers: dict[str, list[str]] = {}
        calendar_days = list(days or self.calendar_days())
        try:
            calendar_days.append(dt.date.fromisoformat(self.selected_date))
        except ValueError:
            pass
        if not calendar_days:
            return markers
        start_day = min(calendar_days)
        end_day = max(calendar_days)
        allowed = {profile_id(profile) for profile in self.visible_profiles()}
        if filter_selected and self.selected_profile != "all":
            allowed = {self.selected_profile}
        for profile in self.profiles:
            if profile_id(profile) not in allowed:
                continue
            reset_raw = profile.get("weeklyResetEstimateUtc") or profile.get("weeklyLimitResetUtc")
            parsed = parse_iso_datetime(reset_raw)
            if parsed is None:
                continue
            source = str(profile.get("weeklyResetEstimateSource") or "api")
            label = f"{profile.get('name', 'Account')} weekly reset"
            if source == "usage":
                label += " estimate"
            occurrence = parsed.date()
            while occurrence > start_day:
                occurrence -= dt.timedelta(days=7)
            while occurrence < start_day:
                occurrence += dt.timedelta(days=7)
            while occurrence <= end_day:
                markers.setdefault(occurrence.isoformat(), []).append(label)
                occurrence += dt.timedelta(days=7)
        return markers

    def usage_entries_for_day(self, iso_day: str, filter_selected: bool = False) -> list[dict]:
        visible = self.visible_profiles()
        entries = history_usage_entries(visible, iso_day=iso_day)
        allowed = {profile_id(profile) for profile in visible}
        if filter_selected and self.selected_profile != "all":
            allowed = {self.selected_profile}
        entries = [entry for entry in entries if entry["profileId"] in allowed]
        entries.sort(key=lambda entry: str(entry["profile"].get("name", "")))
        return entries

    def all_visible_usage_entries(self) -> list[dict]:
        entries = history_usage_entries(self.visible_profiles())
        entries.sort(key=lambda entry: (str(entry.get("day") or ""), str(entry["profile"].get("name", ""))))
        return entries

    def usage_totals(self, entries: list[dict]) -> tuple[int, int | None]:
        total_tokens = sum(int(entry["tokens"]) for entry in entries)
        minute_values = [entry["minutes"] for entry in entries if entry["minutes"] is not None]
        total_minutes = sum(minute_values) if minute_values else None
        return total_tokens, total_minutes

    def render(self) -> None:
        self._profile_state_cache = {}
        self._update_buttons()
        self._render_accounts()
        self._render_summary()
        self._render_calendar()
        self._render_details()
        self._render_coding()

    def _update_buttons(self) -> None:
        self._update_section_navigation()
        for value, button in getattr(self, "mode_buttons", {}).items():
            selected = value == self.mode_var.get()
            if selected:
                button.configure(bg=PRIMARY, fg="white", activebackground=PRIMARY_HOVER, activeforeground="white")
            else:
                button.configure(bg=PANEL, fg=INK, activebackground=PANEL_ALT, activeforeground=INK)

        if hasattr(self, "theme_button"):
            self.theme_button.configure(text=("D  Dark" if self.theme_name == "light" else "L  Light"))
        if hasattr(self, "auto_refresh_button"):
            self.auto_refresh_button.configure(text=("O  Auto On" if self.auto_refresh_enabled else "O  Auto Off"))
        if hasattr(self, "active_account_label"):
            marker = self.active_desktop_marker()
            active_name = str(marker.get("name") or "").strip()
            synced = local_datetime_label(marker.get("syncedAtUtc")) if marker.get("syncedAtUtc") else "-"
            self.active_account_label.configure(text=f"Codex Desktop active: {active_name} | Synced {synced}" if active_name else "Codex Desktop active: default or unknown")

        coding_profile = self.coding_selected_profile()
        native_ready = coding_profile is not None and not self.busy and not self.native_busy
        if hasattr(self, "coding_new_thread_button"):
            self.coding_new_thread_button.configure(state=("normal" if native_ready else "disabled"))
        if hasattr(self, "coding_sidebar_new_thread_button"):
            self.coding_sidebar_new_thread_button.configure(state=("normal" if native_ready else "disabled"))
        if hasattr(self, "coding_send_button"):
            self.coding_send_button.configure(state=("normal" if native_ready else "disabled"))
        if hasattr(self, "coding_stop_button"):
            self.coding_stop_button.configure(state=("normal" if self.native_busy else "disabled"))

        selected_account = self.selected_profile_obj()
        account_actions_enabled = selected_account is not None and not self.busy
        selected_index = self.profile_index(selected_account) if selected_account is not None else -1
        sort_is_manual = self.sort_var.get() == "Manual"
        for key, button in getattr(self, "profile_action_buttons", {}).items():
            enabled = not self.busy
            if key in {"edit", "rename", "delete"}:
                enabled = account_actions_enabled
            elif key == "up":
                enabled = account_actions_enabled and sort_is_manual and selected_index > 0
            elif key == "down":
                enabled = account_actions_enabled and sort_is_manual and 0 <= selected_index < len(self.profiles) - 1
            button.configure(state=("normal" if enabled else "disabled"))
        if hasattr(self, "actions_frame"):
            if selected_account is None:
                self.actions_frame.grid_remove()
            else:
                self.actions_frame.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 8))

        provider = provider_key(selected_account) if selected_account is not None else ""
        supported_actions = {
            "codex": {"coding", "desktop", "cli", "login", "device_login", "status", "doctor", "online", "dry_run", "restore", "reset", "set_timer", "clear_timer", "seed", "home", "refresh"},
            "claude": {"coding", "desktop", "cli", "login", "status", "doctor", "online", "home", "refresh"},
            "cursor": {"coding", "desktop", "cli", "login", "status", "doctor", "online", "home", "refresh"},
            "antigravity": {"coding", "desktop", "cli", "login", "status", "doctor", "online", "home", "refresh"},
        }.get(provider, {"home"} if selected_account is not None else set())
        if selected_account is not None and online_links_for_profile(selected_account):
            supported_actions = set(supported_actions) | {"online"}
        for button in getattr(self, "top_profile_buttons", []):
            button.configure(state=("normal" if account_actions_enabled else "disabled"))
        for key, button in getattr(self, "account_action_buttons", {}).items():
            enabled = account_actions_enabled and key in supported_actions
            button.configure(state=("normal" if enabled else "disabled"))

        if self.busy:
            for button in self.buttons:
                button.configure(state="disabled")

        title = f"{calendar.month_name[self.calendar_month]} {self.calendar_year}"
        if self.mode_var.get() == "week":
            start = self.week_start_date()
            title = f"Week of {start.strftime('%B')} {start.day}, {start.year}"
        self.calendar_title.configure(text=title)
        if self.auto_refresh_enabled:
            auto_text = f"Auto-refresh every {self.auto_refresh_minutes}m; next {self.next_auto_refresh_at.strftime('%H:%M')}."
        else:
            auto_text = "Auto-refresh is paused."
        self.calendar_subtitle.configure(text=f"Saved history, provider buckets, and reset markers where exposed. {auto_text}")

    def _render_accounts(self) -> None:
        self._clear(self.account_scroll.inner)
        self.account_cards.clear()
        self.account_badges.clear()
        visible = self.visible_profiles()
        ready = sum(1 for profile in self.profiles if self.profile_state(profile) == "ready")
        not_ready = sum(1 for profile in self.profiles if self.profile_state(profile) in {"not_ready", "error"})
        login = sum(1 for profile in self.profiles if self.profile_state(profile) == "login")
        self.pool_summary.configure(text=f"Total: {len(self.profiles)}    Ready: {ready}    Not Ready: {not_ready}    Login: {login}")

        node_text = "Node helper ready" if self.node_path and HELPER_PATH.exists() else "Node helper unavailable"
        self.account_status_line.configure(text=node_text)

        all_card = self._account_card(self.account_scroll.inner, None)
        self.account_cards["all"] = all_card
        all_card.pack(fill="x", padx=0, pady=4)
        for profile in visible:
            card = self._account_card(self.account_scroll.inner, profile)
            self.account_cards[profile_id(profile)] = card
            card.pack(fill="x", padx=0, pady=4)

    def _account_card(self, master: tk.Misc, profile: dict | None) -> tk.Frame:
        selected = (self.selected_profile == "all") if profile is None else (self.selected_profile == profile_id(profile))
        selected_bg = CARD_SELECTED_DARK if self.theme_name == "dark" else CARD_SELECTED
        hairline = CARD_HAIRLINE_DARK if self.theme_name == "dark" else CARD_HAIRLINE
        bg = selected_bg if selected else PANEL
        card = tk.Frame(master, bg=bg, highlightbackground=PRIMARY if selected else hairline, highlightthickness=1, cursor="hand2")
        card.configure(padx=10, pady=8)
        card.grid_columnconfigure(1, weight=1, minsize=0)
        card.grid_columnconfigure(2, minsize=116)

        if profile is None:
            target = "all"
            provider = "api"
            title = "All visible accounts"
            visible = self.visible_profiles()
            ready_count = sum(1 for item in visible if self.profile_state(item) == "ready")
            not_ready_count = sum(1 for item in visible if self.profile_state(item) in {"not_ready", "error"})
            login_count = sum(1 for item in visible if self.profile_state(item) == "login")
            pool_entries = self.all_visible_usage_entries()
            pool_tokens, _pool_minutes = self.usage_totals(pool_entries)
            provider_text = f"{len(visible)} visible profiles"
            plan = f"{ready_count} ready | {not_ready_count} not ready | {login_count} login"
            identity = "-"
            week_text = f"{compact_number(pool_tokens)} saved tok"
            session_text = f"{len(pool_entries)} records"
            state = "ready"
            meter = int((ready_count / len(visible)) * 100) if visible else 0
            meter_label = f"{ready_count}/{len(visible)} Ready" if visible else "No profiles"
            capability = {"label": "Pooled visible stats", "state": "ready", "detail": "Rollup of visible profile history and status."}
        else:
            target = profile_id(profile)
            provider = provider_key(profile)
            title = str(profile.get("name", "Account"))
            short_left = percent_left(profile.get("shortLimitUsedPercent"))
            weekly_left = percent_left(profile.get("weeklyLimitUsedPercent"))
            state = self.profile_state(profile)
            capability = provider_capability(profile)
            active = " | Desktop" if profile_id(profile) == self.active_desktop_home() else ""
            plan = account_plan_label(profile)
            identity = masked_account_identity_label(profile)
            week_text = f"Week {format_percent(weekly_left)}"
            session_text = f"Session {format_percent(short_left)}"
            if provider == "codex":
                provider_text = f"{provider_label(profile)}{active}"
            elif provider == "claude":
                summary = profile.get("usageSummary") if isinstance(profile.get("usageSummary"), dict) else {}
                desktop_state = "Desktop login" if summary.get("desktopReady") else "Desktop separate"
                cli_state = "CLI found" if self.claude_code_path else "CLI missing"
                provider_text = f"{provider_label(profile)} | {desktop_state} | {cli_state}"
            elif provider == "cursor":
                cli_state = "Agent found" if self.cursor_agent_path else "Agent missing"
                provider_text = f"{provider_label(profile)} | {cli_state}"
            elif provider == "antigravity":
                desktop_state = "Desktop found" if self.antigravity_desktop_path else "Desktop missing"
                cli_state = "CLI found" if self.antigravity_cli_path and Path(self.antigravity_cli_path).name.lower() != "agy-node.cmd" else "CLI missing"
                provider_text = f"{provider_label(profile)} | {desktop_state} | {cli_state}"
            else:
                provider_text = f"{provider_label(profile)} | Provider wiring pending"
            if provider in {"codex", "claude"}:
                meter = int(weekly_left if weekly_left is not None else 0)
                meter_label = week_text
            else:
                meter = 100 if state == "ready" else 0
                meter_label = status_label(state)

        self._service_icon(card, provider, size=28).grid(row=0, column=0, rowspan=4, sticky="nw", padx=(0, 8))

        template = self.card_template_var.get() if hasattr(self, "card_template_var") else "Balanced"
        if template not in CARD_TEMPLATE_CHOICES:
            template = "Balanced"

        title_text = clip_text(title, 27)
        line_one = clip_text(f"{provider_text} | {capability['label']}", 35)
        line_two = clip_text(f"{plan} | {week_text} | {session_text}", 32)
        if template == "Compact":
            rows = [(title_text, INK, ("Segoe UI", 9, "bold")), (clip_text(f"{provider_label({'provider': provider})} | {plan}", 36), MUTED, ("Segoe UI", 8))]
        elif template == "Plan Chips":
            rows = [(title_text, INK, ("Segoe UI", 9, "bold"))]
        elif template == "Usage First":
            rows = [(title_text, INK, ("Segoe UI", 9, "bold")), (line_two, INK, ("Segoe UI", 8, "bold")), (clip_text(f"{provider_label({'provider': provider})} | {plan}", 38), MUTED, ("Segoe UI", 8))]
        elif template == "Identity":
            rows = [(title_text, INK, ("Segoe UI", 9, "bold")), (clip_text(identity, 34), MUTED, ("Segoe UI", 8)), (clip_text(f"{provider_label({'provider': provider})} | {plan}", 38), MUTED, ("Segoe UI", 8))]
        else:
            rows = [(title_text, INK, ("Segoe UI", 9, "bold")), (line_one, MUTED, ("Segoe UI", 8)), (line_two, MUTED, ("Segoe UI", 8))]

        for row_index, (text, fg, font) in enumerate(rows):
            tk.Label(card, text=text, bg=bg, fg=fg, font=font, anchor="w").grid(row=row_index, column=1, sticky="ew", pady=(0, 1))

        if template == "Plan Chips":
            chip_row = tk.Frame(card, bg=bg)
            chip_row.grid(row=1, column=1, sticky="w", pady=(2, 1))
            chip_items = [
                (clip_text(provider_label({"provider": provider}), 14), PROVIDER_COLORS.get(provider, BLUE), "white"),
                (clip_text(plan, 16), BLUE_SOFT, BLUE),
                (clip_text(identity, 18), PANEL_ALT, MUTED),
            ]
            for text, chip_bg, chip_fg in chip_items:
                tk.Label(chip_row, text=text, bg=chip_bg, fg=chip_fg, font=("Segoe UI", 7, "bold"), padx=6, pady=2).pack(side="left", padx=(0, 4))
            tk.Label(card, text=line_two, bg=bg, fg=MUTED, font=("Segoe UI", 8), anchor="w").grid(row=2, column=1, sticky="ew", pady=(0, 1))

        badge = self._status_badge(card, clip_text(status_badge_text(profile, state), 18), state)
        badge.configure(width=15, padx=2)
        badge.grid(row=0, column=2, rowspan=2, sticky="ne", padx=(6, 0))
        self.account_badges[target] = badge
        self._live_account_states[target] = state

        meter_row = 2 if template == "Compact" else 3
        meter_frame = tk.Frame(card, bg=METER_BG_DARK if self.theme_name == "dark" else METER_BG, height=8)
        meter_frame.grid(row=meter_row, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        meter_frame.grid_propagate(False)
        if state == "error":
            fill_color = RED
        elif state == "login":
            fill_color = AMBER
        else:
            fill_color = RED if meter <= 0 else (AMBER if meter < 35 else GREEN)
        tk.Frame(meter_frame, bg=fill_color).place(relx=0, rely=0, relwidth=max(0.0, min(1.0, meter / 100)), relheight=1)
        tk.Label(card, text=clip_text(meter_label, 11), bg=bg, fg=INK, font=("Segoe UI", 8, "bold")).grid(row=meter_row, column=2, sticky="e", padx=(6, 0), pady=(5, 0))

        self._bind_recursive(card, lambda _event, t=target: self.select_profile(t))
        return card

    def _account_card_bg(self, selected: bool) -> str:
        if not selected:
            return PANEL
        return CARD_SELECTED_DARK if self.theme_name == "dark" else CARD_SELECTED

    def _recolor_account_card_children(self, widget: tk.Widget, bg: str) -> None:
        managed_backgrounds = {PANEL, CARD_SELECTED, CARD_SELECTED_DARK}
        for child in widget.winfo_children():
            try:
                if str(child.cget("bg")) in managed_backgrounds:
                    child.configure(bg=bg)
            except tk.TclError:
                pass
            self._recolor_account_card_children(child, bg)

    def _refresh_account_selection_styles(self) -> None:
        hairline = CARD_HAIRLINE_DARK if self.theme_name == "dark" else CARD_HAIRLINE
        for pid, card in self.account_cards.items():
            selected = pid == self.selected_profile
            bg = self._account_card_bg(selected)
            try:
                card.configure(bg=bg, highlightbackground=PRIMARY if selected else hairline)
                self._recolor_account_card_children(card, bg)
            except tk.TclError:
                continue

    def _configure_status_badge_label(self, badge: tk.Label, profile: dict | None, state: str) -> None:
        fg, bg = status_colors(state)
        try:
            badge.configure(text=clip_text(status_badge_text(profile, state), 18), bg=bg, fg=fg)
        except tk.TclError:
            pass

    def _refresh_live_status_labels(self) -> None:
        profiles_by_id = {profile_id(profile): profile for profile in self.profiles}
        current_states: dict[str, str] = {"all": "ready"}
        needs_render = False
        for pid, profile in profiles_by_id.items():
            state = effective_state(profile)
            current_states[pid] = state
            previous = self._live_account_states.get(pid)
            if previous is not None and previous != state:
                needs_render = True

        self._live_account_states.update(current_states)
        self._profile_state_cache.update({pid: state for pid, state in current_states.items() if pid != "all"})
        if needs_render:
            self.render()
            return

        for pid, badge in list(self.account_badges.items()):
            if not badge.winfo_exists():
                continue
            if pid == "all":
                self._configure_status_badge_label(badge, None, "ready")
                continue
            profile = profiles_by_id.get(pid)
            if profile is None:
                continue
            self._configure_status_badge_label(badge, profile, current_states.get(pid, "ready"))

        selected = self.selected_profile_obj()
        if selected is not None and self.selected_status_badge is not None and self.selected_status_badge.winfo_exists():
            pid = profile_id(selected)
            self._configure_status_badge_label(self.selected_status_badge, selected, current_states.get(pid, effective_state(selected)))

    def _render_summary(self) -> None:
        self._clear(self.summary)
        entries = self.usage_entries_for_day(self.selected_date, filter_selected=False)
        marker_map = self.reset_markers()
        markers = marker_map.get(self.selected_date, [])
        total_tokens, total_minutes = self.usage_totals(entries)
        pool_entries = self.all_visible_usage_entries()
        pool_tokens, _pool_minutes = self.usage_totals(pool_entries)
        pool_accounts = len({entry["profileId"] for entry in pool_entries})
        reset_subtext = "; ".join(markers[:2]) if markers else "No reset marker on selected day"
        if not markers:
            upcoming_days = sorted(day for day in marker_map if day >= self.selected_date)
            if upcoming_days:
                reset_subtext = f"Next {upcoming_days[0]}: {marker_map[upcoming_days[0]][0]}"

        metrics = [
            ("Day tokens", compact_number(total_tokens), f"{len({entry['profileId'] for entry in entries})} accounts | {len(entries)} records"),
            ("Day active", format_minutes(total_minutes), "Provider minutes where exposed"),
            ("Pool tokens", compact_number(pool_tokens), f"{pool_accounts} accounts | {len(pool_entries)} records"),
            ("Reset markers", str(len(markers)), reset_subtext),
        ]
        hairline = CARD_HAIRLINE_DARK if self.theme_name == "dark" else CARD_HAIRLINE
        for column, (label, value, subtext) in enumerate(metrics):
            frame = tk.Frame(self.summary, bg=PANEL, highlightbackground=hairline, highlightthickness=1, padx=12, pady=10)
            frame.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 5, 0 if column == len(metrics) - 1 else 5))
            tk.Label(frame, text=label, bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
            tk.Label(frame, text=value, bg=PANEL, fg=INK, font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(3, 0))
            tk.Label(frame, text=clip_text(subtext, 30), bg=PANEL, fg=MUTED, font=("Segoe UI", 7), wraplength=120, justify="left").pack(anchor="w", pady=(3, 0))

    def _render_calendar(self) -> None:
        self._clear(self.calendar_panel)
        weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        header_bg = CALENDAR_HEADER_DARK if self.theme_name == "dark" else CALENDAR_HEADER
        for column, day in enumerate(weekdays):
            tk.Label(self.calendar_panel, text=day, bg=header_bg, fg=MUTED, font=("Segoe UI", 8, "bold"), anchor="w", padx=10, pady=9).grid(row=0, column=column, sticky="nsew")

        days = self.calendar_days()
        cell_height = 350 if self.mode_var.get() == "week" else 112
        for index, day in enumerate(days):
            row = 1 + index // 7
            column = index % 7
            self.calendar_panel.grid_rowconfigure(row, weight=1)
            self._day_cell(self.calendar_panel, day, row, column, cell_height)

    def calendar_days(self) -> list[dt.date]:
        if self.mode_var.get() == "week":
            start = self.week_start_date()
            return [start + dt.timedelta(days=index) for index in range(7)]
        return month_days(self.calendar_year, self.calendar_month)

    def week_start_date(self) -> dt.date:
        selected = dt.date.fromisoformat(self.selected_date)
        return selected - dt.timedelta(days=(selected.weekday() + 1) % 7)

    def _day_cell(self, master: tk.Misc, day: dt.date, row: int, column: int, height: int) -> None:
        iso = day.isoformat()
        in_month = day.month == self.calendar_month
        selected = iso == self.selected_date
        today = iso == dt.date.today().isoformat()
        selected_bg = CARD_SELECTED_DARK if self.theme_name == "dark" else CARD_SELECTED
        outside_bg = CALENDAR_OUTSIDE_DARK if self.theme_name == "dark" else CALENDAR_OUTSIDE
        hairline = CARD_HAIRLINE_DARK if self.theme_name == "dark" else CARD_HAIRLINE
        bg = selected_bg if selected else (PANEL if in_month else outside_bg)
        frame = tk.Frame(master, bg=bg, highlightbackground=PRIMARY if selected else (LINE_STRONG if today else hairline), highlightthickness=1, cursor="hand2", height=height)
        frame.grid(row=row, column=column, sticky="nsew")
        frame.grid_propagate(False)
        frame.grid_columnconfigure(0, weight=1)

        entries = self.usage_entries_for_day(iso, filter_selected=False)
        total_tokens = sum(int(entry["tokens"]) for entry in entries)
        markers = self.reset_markers().get(iso, [])
        minutes_values = [entry["minutes"] for entry in entries if entry["minutes"] is not None]
        total_minutes = sum(minutes_values) if minutes_values else None

        head = tk.Frame(frame, bg=bg)
        head.grid(row=0, column=0, sticky="ew", padx=8, pady=(7, 4))
        head.grid_columnconfigure(1, weight=1)
        tk.Label(head, text=str(day.day), bg=bg, fg=INK if in_month else MUTED, font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(head, text=f"{compact_number(total_tokens)} tok" if total_tokens else "", bg=bg, fg=MUTED, font=("Segoe UI", 7)).grid(row=0, column=1, sticky="e")

        entries_box = tk.Frame(frame, bg=bg)
        entries_box.grid(row=1, column=0, sticky="nsew", padx=7)
        max_items = 7 if self.mode_var.get() == "week" else 2
        for entry in entries[:max_items]:
            profile = entry["profile"]
            row_bg = PANEL_ALT if self.mode_var.get() == "week" else bg
            pill = tk.Frame(entries_box, bg=row_bg)
            pill.pack(fill="x", pady=(0, 1))
            pill.grid_columnconfigure(1, weight=1)
            provider = provider_key(profile)
            tk.Frame(pill, bg=PROVIDER_COLORS.get(provider, "#666"), width=4, height=12).grid(row=0, column=0, sticky="nsw", padx=(0, 4), pady=1)
            account_name = str(profile.get("name", "Account"))
            compact_name = account_name.split()[0][:8]
            tk.Label(pill, text=compact_name, bg=row_bg, fg=INK, font=("Segoe UI", 7), anchor="w").grid(row=0, column=1, sticky="ew")
            tk.Label(pill, text=compact_number(entry["tokens"]), bg=row_bg, fg=MUTED, font=("Segoe UI", 7)).grid(row=0, column=2, sticky="e", padx=(4, 0))

        if len(entries) > max_items:
            tk.Label(entries_box, text=f"+{len(entries) - max_items} more", bg=bg, fg=MUTED, font=("Segoe UI", 7), anchor="w").pack(anchor="w")

        foot = tk.Frame(frame, bg=bg)
        foot.grid(row=2, column=0, sticky="ew", padx=8, pady=(4, 7))
        if markers:
            max_marker_lines = 4 if self.mode_var.get() == "week" else 2
            for marker in markers[:max_marker_lines]:
                tk.Label(foot, text=f"* {marker}", bg=bg, fg=GREEN, font=("Segoe UI", 7, "bold"), anchor="w", wraplength=130, justify="left").pack(anchor="w")
            if len(markers) > max_marker_lines:
                tk.Label(foot, text=f"+{len(markers) - max_marker_lines} more resets", bg=bg, fg=MUTED, font=("Segoe UI", 7), anchor="w").pack(anchor="w")
        elif total_minutes is not None:
            tk.Label(foot, text=f"{format_minutes(total_minutes)} active", bg=bg, fg=MUTED, font=("Segoe UI", 7), anchor="w").pack(anchor="w")

        self._bind_recursive(frame, lambda _event, selected_iso=iso: self.select_date(selected_iso))

    def _render_details(self) -> None:
        selected_account = self.selected_profile_obj()
        filter_selected = selected_account is not None
        entries = self.usage_entries_for_day(self.selected_date, filter_selected=filter_selected)
        markers = self.reset_markers(filter_selected=filter_selected).get(self.selected_date, [])
        total_tokens, total_minutes = self.usage_totals(entries)
        date_obj = dt.date.fromisoformat(self.selected_date)
        if selected_account is None:
            self.detail_title.configure(text=f"{date_obj.strftime('%A, %B')} {date_obj.day}")
            self.detail_subtitle.configure(text=f"{self.selected_date} | All visible accounts")
            day_state = "ready" if entries or markers else "idle"
            status_text = "Activity + Reset" if entries and markers else ("Activity" if entries else ("Reset" if markers else "Quiet"))
        else:
            day_state = self.profile_state(selected_account)
            status_text = status_badge_text(selected_account, day_state)
            self.detail_title.configure(text=clip_text(str(selected_account.get("name", "Account")), 34))
            self.detail_subtitle.configure(text=f"{self.selected_date} | {provider_label(selected_account)}")
        self.detail_status.configure(text=status_text, bg=status_colors(day_state)[1], fg=status_colors(day_state)[0])

        self._clear(self.detail_metrics)
        metrics = [
            ("Day tokens", compact_number(total_tokens)),
            ("Day active", format_minutes(total_minutes)),
            ("Usage records", str(len(entries))),
            ("Reset events", str(len(markers))),
        ]
        for index, (label, value) in enumerate(metrics):
            self._detail_card(self.detail_metrics, label, value).grid(row=index // 2, column=index % 2, sticky="ew", padx=4, pady=4)

        self._clear(self.breakdown_scroll.inner)
        self.selected_status_badge = None
        if selected_account is None:
            self._render_all_visible_stats_card()
        else:
            self._render_selected_profile_card()
        self._render_day_breakdown_table(entries)
        for marker in markers:
            self._reset_marker_card(marker).pack(fill="x", pady=5)
        if not entries and not markers:
            self._empty_card("No usage or reset on this day", "Click another calendar day, or use Refresh All to load the latest provider usage buckets.").pack(fill="x", pady=5)
        for entry in entries:
            self._usage_card(entry).pack(fill="x", pady=5)

    def _render_all_visible_stats_card(self) -> None:
        visible = self.visible_profiles()
        pool_entries = self.all_visible_usage_entries()
        pool_tokens, pool_minutes = self.usage_totals(pool_entries)
        ready = sum(1 for profile in visible if self.profile_state(profile) == "ready")
        not_ready = sum(1 for profile in visible if self.profile_state(profile) in {"not_ready", "error"})
        login = sum(1 for profile in visible if self.profile_state(profile) == "login")
        provider_counts: dict[str, int] = {}
        for profile in visible:
            provider_counts[provider_label(profile)] = provider_counts.get(provider_label(profile), 0) + 1
        provider_text = ", ".join(f"{name} {count}" for name, count in sorted(provider_counts.items())) or "-"
        combined_weekly = combined_limit_left_text(visible, "weeklyLimitUsedPercent")
        combined_5h = combined_limit_left_text(visible, "shortLimitUsedPercent")
        weekly_values = [
            (percent_left(profile.get("weeklyLimitUsedPercent")), str(profile.get("name", "Account")))
            for profile in visible
            if percent_left(profile.get("weeklyLimitUsedPercent")) is not None
        ]
        lowest_weekly = "-"
        if weekly_values:
            value, name = min((value, name) for value, name in weekly_values if value is not None)
            lowest_weekly = f"{format_percent(value)} - {clip_text(name, 18)}"
        reset_credits = 0
        for profile in visible:
            try:
                reset_credits += int(str(profile.get("resetCreditsAvailable") or "0"))
            except ValueError:
                pass

        marker_map = self.reset_markers()
        upcoming = sorted(day for day in marker_map if day >= self.selected_date)[:4]
        upcoming_text = "; ".join(f"{day}: {clip_text(marker_map[day][0], 24)}" for day in upcoming) or "-"

        hairline = CARD_HAIRLINE_DARK if self.theme_name == "dark" else CARD_HAIRLINE
        card = tk.Frame(self.breakdown_scroll.inner, bg=PANEL, highlightbackground=hairline, highlightthickness=1, padx=10, pady=10)
        card.pack(fill="x", pady=5)
        top = tk.Frame(card, bg=PANEL)
        top.pack(fill="x")
        self._mini_dot(top, "api").pack(side="left", padx=(0, 8))
        title = tk.Frame(top, bg=PANEL)
        title.pack(side="left", fill="x", expand=True)
        tk.Label(title, text="All visible accounts", bg=PANEL, fg=INK, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(title, text="Pooled dashboard stats from visible profile history", bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=180, justify="left").pack(anchor="w", pady=(2, 0))
        self._status_badge(top, f"{ready}/{len(visible)} Ready" if visible else "No Profiles", "ready" if not not_ready else "not_ready").pack(side="right")

        details = [
            ("Profiles", str(len(visible))),
            ("Ready", str(ready)),
            ("Not Ready", str(not_ready)),
            ("Login", str(login)),
            ("Pooled tokens", compact_number(pool_tokens)),
            ("Pooled active", format_minutes(pool_minutes)),
            ("Combined weekly", combined_weekly),
            ("Combined 5h", combined_5h),
            ("History records", str(len(pool_entries))),
            ("Limit snapshots", str(history_limit_count())),
            ("Lowest weekly", lowest_weekly),
            ("Reset credits", str(reset_credits)),
        ]
        grid = tk.Frame(card, bg=PANEL)
        grid.pack(fill="x", pady=(8, 0))
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)
        for index, (label, value) in enumerate(details):
            self._detail_card(grid, label, value).grid(row=index // 2, column=index % 2, sticky="ew", padx=4, pady=4)

        tk.Label(card, text=f"Providers: {provider_text}", bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=340, justify="left").pack(anchor="w", pady=(8, 0))
        tk.Label(card, text=f"Upcoming resets: {upcoming_text}", bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=340, justify="left").pack(anchor="w", pady=(4, 0))

        capability_box = tk.Frame(card, bg=PANEL)
        capability_box.pack(fill="x", pady=(8, 0))
        for profile in visible:
            capability = provider_capability(profile)
            row = tk.Frame(capability_box, bg=PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=clip_text(str(profile.get("name", "Account")), 20), bg=PANEL, fg=INK, font=("Segoe UI", 8, "bold"), anchor="w").pack(side="left", fill="x", expand=True)
            self._status_badge(row, clip_text(capability["label"], 22), capability["state"]).pack(side="right")

    def _render_day_breakdown_table(self, entries: list[dict]) -> None:
        if not entries:
            return
        rows: dict[str, dict] = {}
        for entry in entries:
            pid = str(entry["profileId"])
            row = rows.setdefault(
                pid,
                {
                    "profile": entry["profile"],
                    "tokens": 0,
                    "minutes": 0,
                    "hasMinutes": False,
                    "records": 0,
                    "messages": 0,
                    "hasMessages": False,
                },
            )
            row["tokens"] += int(entry["tokens"])
            if entry["minutes"] is not None:
                row["minutes"] += int(entry["minutes"])
                row["hasMinutes"] = True
            message_count = entry.get("messageCount")
            if message_count is None and isinstance(entry.get("bucket"), dict):
                message_count = history_message_count(entry["bucket"])
            if message_count is not None:
                row["messages"] += int(message_count)
                row["hasMessages"] = True
            row["records"] += 1

        hairline = CARD_HAIRLINE_DARK if self.theme_name == "dark" else CARD_HAIRLINE
        card = tk.Frame(self.breakdown_scroll.inner, bg=PANEL, highlightbackground=hairline, highlightthickness=1, padx=10, pady=10)
        card.pack(fill="x", pady=5)
        tk.Label(card, text="Selected day breakdown", bg=PANEL, fg=INK, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(card, text=f"{self.selected_date} | {len(rows)} accounts | {len(entries)} records", bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(2, 8))

        table = tk.Frame(card, bg=PANEL)
        table.pack(fill="x")
        widths = [16, 9, 9, 8, 5]
        headers = ["Account", "Provider", "Tokens", "Active", "Rows"]
        for column, header in enumerate(headers):
            tk.Label(table, text=header, bg=PANEL_ALT, fg=MUTED, font=("Segoe UI", 7, "bold"), anchor="w", padx=4, pady=4).grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 1, 0), pady=(0, 1))
            table.grid_columnconfigure(column, weight=1 if column == 0 else 0, minsize=widths[column] * 7)

        for row_index, row in enumerate(sorted(rows.values(), key=lambda item: str(item["profile"].get("name", ""))), start=1):
            profile = row["profile"]
            values = [
                clip_text(str(profile.get("name", "Account")), 18),
                clip_text(provider_label(profile), 12),
                compact_number(row["tokens"]),
                format_minutes(row["minutes"] if row["hasMinutes"] else None),
                str(row["records"]),
            ]
            for column, value in enumerate(values):
                tk.Label(table, text=value, bg=PANEL, fg=INK if column != 1 else MUTED, font=("Segoe UI", 7), anchor="w", padx=4, pady=3).grid(row=row_index, column=column, sticky="ew", padx=(0 if column == 0 else 1, 0), pady=(0, 1))

    def _render_selected_profile_card(self) -> None:
        profile = self.selected_profile_obj()
        if profile is None:
            return
        state = self.profile_state(profile)
        hairline = CARD_HAIRLINE_DARK if self.theme_name == "dark" else CARD_HAIRLINE
        card = tk.Frame(self.breakdown_scroll.inner, bg=PANEL, highlightbackground=hairline, highlightthickness=1, padx=10, pady=10)
        card.pack(fill="x", pady=5)
        top = tk.Frame(card, bg=PANEL)
        top.pack(fill="x")
        self._mini_dot(top, profile).pack(side="left", padx=(0, 8))
        title = tk.Frame(top, bg=PANEL)
        title.pack(side="left", fill="x", expand=True)
        provider = provider_key(profile)
        home_text = str(claude_profile_home(profile)) if provider == "claude" else str(profile.get("codexHome", ""))
        tk.Label(title, text=str(profile.get("name", "Account")), bg=PANEL, fg=INK, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(title, text=home_text, bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=180, justify="left").pack(anchor="w", pady=(2, 0))
        self.selected_status_badge = self._status_badge(top, clip_text(status_badge_text(profile, state), 18), state)
        self.selected_status_badge.pack(side="right")
        identity_text = account_identity_label(profile)
        plan_text = account_plan_label(profile)
        capability = provider_capability(profile)

        if provider == "claude":
            summary = profile.get("usageSummary") if isinstance(profile.get("usageSummary"), dict) else {}
            desktop_ready = bool(summary.get("desktopReady")) or cached_claude_desktop_login_status().get("ready")
            short_left = percent_left(profile.get("shortLimitUsedPercent"))
            weekly_left = percent_left(profile.get("weeklyLimitUsedPercent"))
            details = [
                ("Account", identity_text),
                ("Plan", plan_text),
                ("Capability", capability["label"]),
                ("Weekly left", format_percent(weekly_left)),
                ("Weekly reset", format_countdown(profile.get("weeklyResetEstimateUtc") or profile.get("weeklyLimitResetUtc"))),
                ("Session left", "0%" if state == "not_ready" and is_limit_exhausted(profile.get("shortLimitUsedPercent")) else format_percent(short_left)),
                ("Session reset", format_countdown(profile.get("shortLimitResetUtc")) if profile.get("shortLimitResetUtc") else "-"),
                ("7d tokens", compact_number(sanitize_float(summary.get("last7dTokens")))),
                ("7d messages", compact_number(sanitize_float(summary.get("last7dMessages")))),
                ("Desktop", "Ready" if desktop_ready else "Login needed"),
                ("Claude Code", "Found" if self.claude_code_path else "Missing"),
                ("Last refresh", local_datetime_label(profile.get("lastLimitsRefreshUtc"))),
            ]
        elif provider == "codex":
            short_left = percent_left(profile.get("shortLimitUsedPercent"))
            weekly_left = percent_left(profile.get("weeklyLimitUsedPercent"))
            details = [
                ("Account", identity_text),
                ("Plan", plan_text),
                ("Capability", capability["label"]),
                ("Weekly left", format_percent(weekly_left)),
                ("Weekly reset", format_countdown(profile.get("weeklyResetEstimateUtc") or profile.get("weeklyLimitResetUtc"))),
                ("Session left", "0%" if state == "not_ready" and is_limit_exhausted(profile.get("shortLimitUsedPercent")) else format_percent(short_left)),
                ("Session reset", format_countdown(profile.get("shortLimitResetUtc")) if is_limit_exhausted(profile.get("shortLimitUsedPercent")) else "0"),
                ("Reset credits", str(profile.get("resetCreditsAvailable") or "-")),
                ("Last refresh", local_datetime_label(profile.get("lastLimitsRefreshUtc"))),
            ]
        else:
            summary = profile.get("usageSummary") if isinstance(profile.get("usageSummary"), dict) else {}
            desktop_label = {
                "cursor": "Found" if self.cursor_desktop_path else "Missing",
                "antigravity": "Found" if self.antigravity_desktop_path else "Missing",
            }.get(provider, "-")
            cli_label = {
                "cursor": "Agent found" if self.cursor_agent_path else "Agent missing",
                "antigravity": "Found" if self.antigravity_cli_path else "Missing",
            }.get(provider, "-")
            details = [
                ("Account", identity_text),
                ("Plan", plan_text),
                ("Capability", capability["label"]),
                ("Desktop", desktop_label),
                ("CLI", cli_label),
                ("Version", str(profile.get("providerVersion") or summary.get("providerVersion") or "-")),
                ("Last refresh", local_datetime_label(profile.get("lastLimitsRefreshUtc"))),
            ]
        grid = tk.Frame(card, bg=PANEL)
        grid.pack(fill="x", pady=(8, 0))
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)
        for index, (label, value) in enumerate(details):
            self._detail_card(grid, label, value).grid(row=index // 2, column=index % 2, sticky="ew", padx=4, pady=4)

        self._render_online_links_card(card, profile)

        if provider == "claude":
            summary = profile.get("usageSummary") if isinstance(profile.get("usageSummary"), dict) else {}
            desktop_summary = str(summary.get("desktopSummary") or "").strip()
            if desktop_summary:
                tk.Label(card, text=desktop_summary, bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=330, justify="left").pack(anchor="w", pady=(6, 0))
            usage_error = str(summary.get("claudeUsageError") or "").strip()
            if usage_error:
                tk.Label(card, text=f"Usage probe: {usage_error}", bg=PANEL, fg=AMBER, font=("Segoe UI", 8), wraplength=330, justify="left").pack(anchor="w", pady=(6, 0))

        if profile.get("lastLimitsError"):
            tk.Label(card, text=str(profile.get("lastLimitsError")), bg=PANEL, fg=RED, font=("Segoe UI", 8), wraplength=330, justify="left").pack(anchor="w", pady=(6, 0))
        tk.Label(card, text=capability["detail"], bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=330, justify="left").pack(anchor="w", pady=(6, 0))

    def _render_online_links_card(self, master: tk.Misc, profile: dict) -> None:
        links = online_links_for_profile(profile)
        if not links:
            return
        hairline = CARD_HAIRLINE_DARK if self.theme_name == "dark" else CARD_HAIRLINE
        panel = tk.Frame(master, bg=PANEL, highlightbackground=hairline, highlightthickness=1, padx=9, pady=8)
        panel.pack(fill="x", pady=(8, 0))
        heading = tk.Frame(panel, bg=PANEL)
        heading.pack(fill="x")
        tk.Label(heading, text="Online links", bg=PANEL, fg=INK, font=("Segoe UI", 8, "bold")).pack(side="left")
        web_login_label = browser_profile_web_login_label(profile, links)
        if str(profile.get("browserCommand") or "").strip():
            self._status_badge(heading, web_login_label, "ready").pack(side="right")
        elif uses_isolated_browser_profile(profile):
            login_state = "ready" if web_login_label == "Web login saved" else "login"
            self._status_badge(heading, web_login_label, login_state).pack(side="right")
        else:
            self._status_badge(heading, web_login_label, "ready").pack(side="right")

        if uses_isolated_browser_profile(profile):
            tk.Label(panel, text=clip_text(str(browser_profile_dir_for_profile(profile)), 58), bg=PANEL, fg=MUTED, font=("Segoe UI", 7), anchor="w").pack(anchor="w", pady=(4, 0))

        grid = tk.Frame(panel, bg=PANEL)
        grid.pack(fill="x", pady=(7, 0))
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)
        for index, link in enumerate(links):
            button = tk.Button(
                grid,
                text=f">  {clip_text(str(link.get('label') or 'Online'), 20)}",
                command=lambda item=link: self.open_online_link(profile, item),
                bg=PANEL_ALT,
                fg=INK,
                activebackground=PANEL_ALT,
                activeforeground=INK,
                relief="solid",
                bd=1,
                highlightbackground=hairline,
                font=("Segoe UI", 8, "bold"),
                anchor="w",
                padx=8,
                pady=5,
            )
            button.grid(row=index // 2, column=index % 2, sticky="ew", padx=3, pady=3)

    def _detail_card(self, master: tk.Misc, label: str, value: str) -> tk.Frame:
        hairline = CARD_HAIRLINE_DARK if self.theme_name == "dark" else CARD_HAIRLINE
        card = tk.Frame(master, bg=PANEL, highlightbackground=hairline, highlightthickness=1, padx=9, pady=8)
        tk.Label(card, text=label, bg=PANEL, fg=MUTED, font=("Segoe UI", 7)).pack(anchor="w")
        tk.Label(card, text=value, bg=PANEL, fg=INK, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(3, 0))
        return card

    def _reset_marker_card(self, marker: str) -> tk.Frame:
        hairline = CARD_HAIRLINE_DARK if self.theme_name == "dark" else CARD_HAIRLINE
        card = tk.Frame(self.breakdown_scroll.inner, bg=PANEL, highlightbackground=hairline, highlightthickness=1, padx=10, pady=10)
        row = tk.Frame(card, bg=PANEL)
        row.pack(fill="x")
        tk.Label(row, text="R", bg=PROVIDER_COLORS["api"], fg="white", font=("Segoe UI", 7, "bold"), width=4, height=2).pack(side="left", padx=(0, 8))
        tk.Label(row, text=marker, bg=PANEL, fg=INK, font=("Segoe UI", 9, "bold"), wraplength=250, justify="left").pack(side="left", anchor="w", fill="x", expand=True)
        self._status_badge(row, "Reset", "ready").pack(side="right")
        return card

    def _empty_card(self, title: str, body: str) -> tk.Frame:
        hairline = CARD_HAIRLINE_DARK if self.theme_name == "dark" else CARD_HAIRLINE
        card = tk.Frame(self.breakdown_scroll.inner, bg=PANEL, highlightbackground=hairline, highlightthickness=1, padx=10, pady=10)
        tk.Label(card, text=title, bg=PANEL, fg=INK, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(card, text=body, bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=330, justify="left").pack(anchor="w", pady=(4, 0))
        return card

    def _usage_card(self, entry: dict) -> tk.Frame:
        profile = entry["profile"]
        hairline = CARD_HAIRLINE_DARK if self.theme_name == "dark" else CARD_HAIRLINE
        card = tk.Frame(self.breakdown_scroll.inner, bg=PANEL, highlightbackground=hairline, highlightthickness=1, padx=10, pady=10)
        top = tk.Frame(card, bg=PANEL)
        top.pack(fill="x")
        self._mini_dot(top, profile).pack(side="left", padx=(0, 8))
        title = tk.Frame(top, bg=PANEL)
        title.pack(side="left", fill="x", expand=True)
        tk.Label(title, text=str(profile.get("name", "Account")), bg=PANEL, fg=INK, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(title, text=f"{compact_number(entry['tokens'])} tokens", bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=260, justify="left").pack(anchor="w", pady=(2, 0))
        self._status_badge(top, provider_label(profile), "ready").pack(side="right")

        stats = tk.Frame(card, bg=PANEL)
        stats.pack(fill="x", pady=(8, 0))
        stats.grid_columnconfigure(0, weight=1)
        stats.grid_columnconfigure(1, weight=1)
        bucket = entry.get("bucket") if isinstance(entry.get("bucket"), dict) else {}
        usage_details = [
            ("Tokens", compact_number(entry["tokens"])),
            ("Active minutes", format_minutes(entry["minutes"])),
        ]
        message_count = sanitize_float(bucket.get("messageCount") or bucket.get("messages") or bucket.get("requestCount"))
        if message_count is not None:
            usage_details.append(("Messages", compact_number(message_count)))
        source = str(bucket.get("source") or provider_label(profile)).strip()
        usage_details.append(("Source", clip_text(source, 24)))
        for index, (label, value) in enumerate(usage_details):
            self._detail_card(stats, label, value).grid(row=index // 2, column=index % 2, sticky="ew", padx=4, pady=4)

        if entry["minutes"] is None:
            tk.Label(card, text="Hourly/minute detail is not exposed by this provider response.", bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=330, justify="left").pack(anchor="w", pady=(8, 0))
        return card

    def select_profile(self, pid: str) -> None:
        return self.account_manager.select_profile(pid)

    def select_date(self, iso: str) -> None:
        self.selected_date = iso
        selected = dt.date.fromisoformat(iso)
        if self.mode_var.get() == "month":
            self.calendar_year = selected.year
            self.calendar_month = selected.month
        self.status_var.set(f"Selected date: {iso}")
        self._update_buttons()
        self._render_summary()
        self._render_calendar()
        self._render_details()

    def previous_month(self) -> None:
        if self.calendar_month == 1:
            self.calendar_year -= 1
            self.calendar_month = 12
        else:
            self.calendar_month -= 1
        self.render()

    def next_month(self) -> None:
        if self.calendar_month == 12:
            self.calendar_year += 1
            self.calendar_month = 1
        else:
            self.calendar_month += 1
        self.render()

    def go_today(self) -> None:
        today = dt.date.today()
        self.selected_date = today.isoformat()
        self.calendar_year = today.year
        self.calendar_month = today.month
        self.render()

    def set_mode(self, mode: str) -> None:
        self.mode_var.set(mode)
        self.render()

    def reload_profiles(self) -> None:
        return self.account_manager.reload_profiles()

    def next_account_index(self) -> int:
        return self.account_manager.next_account_index()

    def next_account_home(self) -> Path:
        return self.account_manager.next_account_home()

    def profile_index(self, profile: dict | None) -> int:
        return self.account_manager.profile_index(profile)

    def on_sort_changed(self) -> None:
        return self.account_manager.on_sort_changed()

    def on_card_template_changed(self) -> None:
        return self.account_manager.on_card_template_changed()

    def edit_selected_account(self) -> None:
        return self.account_manager.edit_selected_account()

    def rename_selected_account(self) -> None:
        return self.account_manager.rename_selected_account()

    def delete_selected_account(self) -> None:
        return self.account_manager.delete_selected_account()

    def move_selected_account(self, delta: int) -> None:
        return self.account_manager.move_selected_account(delta)

    def add_account_dialog(self, existing: dict | None = None) -> None:
        return self.account_manager.add_account_dialog(existing)

    def toggle_theme(self) -> None:
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.settings["theme"] = self.theme_name
        save_settings(self.settings)
        apply_theme(self.theme_name)
        for child in self.winfo_children():
            child.destroy()
        self.buttons = []
        self.account_cards = {}
        self.account_badges = {}
        self.selected_status_badge = None
        self.icon_images = {}
        self._profile_state_cache = {}
        self._live_account_states = {}
        self._setup_style()
        self._build()
        self.render()
        configure_windows_titlebar(self, self.theme_name)
        self.log(f"Theme changed to {self.theme_name}.")

    def toggle_auto_refresh(self) -> None:
        self.auto_refresh_enabled = not self.auto_refresh_enabled
        self.settings["autoRefreshEnabled"] = self.auto_refresh_enabled
        save_settings(self.settings)
        if self.auto_refresh_enabled:
            self.next_auto_refresh_at = dt.datetime.now() + dt.timedelta(seconds=30)
            self.log(f"Auto-refresh enabled. Next refresh at {self.next_auto_refresh_at.strftime('%H:%M:%S')}.")
        else:
            self.log("Auto-refresh paused.")
        self.render()

    def save_current_profiles(self) -> None:
        save_profiles(self.profiles)

    def log(self, text: str) -> None:
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{stamp}] {text}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        self.status_var.set(text.splitlines()[0] if text else "Ready")

    def log_threadsafe(self, text: str) -> None:
        self.after(0, lambda: self.log(text))

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        for button in self.buttons:
            button.configure(state=("disabled" if busy else "normal"))
        self._update_buttons()

    def run_task(self, title: str, func) -> None:
        if self.busy:
            return
        self.set_busy(True)
        self.log(title)

        def worker() -> None:
            try:
                func()
            except Exception as error:
                self.log_threadsafe(str(error))
            finally:
                self.after(0, self.finish_task)

        threading.Thread(target=worker, daemon=True).start()

    def finish_task(self) -> None:
        self.set_busy(False)
        self.render()

    def selected_required(self) -> dict | None:
        profile = self.selected_profile_obj()
        if profile is None:
            messagebox.showinfo("Select account", "Select one account first.")
        return profile

    def refresh_profile_limits(self, profile: dict, action: str = "read") -> dict:
        provider = provider_key(profile)
        if provider == "claude":
            return self.refresh_claude_profile(profile)
        if provider == "cursor":
            return self.refresh_cursor_profile(profile)
        if provider == "antigravity":
            return self.refresh_antigravity_profile(profile)
        if provider != "codex":
            profile["lastLimitsRefreshUtc"] = iso_utc_now()
            profile["lastLimitsError"] = f"{provider_label(profile)} refresh is not wired yet."
            return {"ok": False, "error": profile["lastLimitsError"]}
        if not self.node_path:
            raise RuntimeError(self.node_error or "Node.js was not found.")
        if not self.codex_cli_path:
            raise RuntimeError(self.codex_cli_error or "codex.exe was not found.")
        if not HELPER_PATH.exists():
            raise RuntimeError(f"Missing limits helper: {HELPER_PATH}")
        ensure_file_credential_store(profile)
        workspace = Path(str(profile.get("workspace") or DEFAULT_WORKSPACE))
        workspace.mkdir(parents=True, exist_ok=True)
        process = run_capture(
            self.node_path,
            [str(HELPER_PATH), self.codex_cli_path, str(profile.get("codexHome")), str(workspace), action],
            workspace,
            timeout=45,
        )
        stdout = process.stdout.strip()
        if not stdout:
            raise RuntimeError(process.stderr.strip() or "No output from limits helper.")
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Could not parse limits helper output: {error}\n{stdout[:500]}") from error
        set_profile_limits_from_result(profile, result)
        return result

    def refresh_claude_profile(self, profile: dict) -> dict:
        desktop = claude_desktop_login_status()
        _CLAUDE_STATUS_CACHE["at"] = dt.datetime.now(dt.timezone.utc)
        _CLAUDE_STATUS_CACHE["value"] = desktop
        cli_status = self.run_claude_auth_status(profile, redacted=True)
        auth_info = parse_claude_auth_status_text(cli_status)
        usage_probe = self.run_claude_usage_probe(profile)
        usage_parsed = usage_probe.get("parsed") if usage_probe.get("ok") and isinstance(usage_probe.get("parsed"), dict) else {}
        daily_buckets = build_claude_usage_buckets(claude_profile_home(profile) / "projects")
        profile["lastLimitsRefreshUtc"] = iso_utc_now()
        profile["accountPlan"] = str(auth_info.get("subscriptionType") or "")
        profile["accountPlanStatus"] = ""
        profile["accountType"] = str(auth_info.get("authMethod") or auth_info.get("apiProvider") or "")
        profile["accountName"] = str(auth_info.get("orgName") or "")
        profile["accountEmail"] = str(auth_info.get("email") or "")
        session_used = usage_parsed.get("sessionUsedPercent") if isinstance(usage_parsed, dict) else None
        weekly_used = usage_parsed.get("weeklyUsedPercent") if isinstance(usage_parsed, dict) else None
        profile["shortLimitLabel"] = "5h"
        profile["weeklyLimitLabel"] = "Weekly"
        profile["shortLimitUsedPercent"] = "" if session_used is None else str(session_used)
        profile["weeklyLimitUsedPercent"] = "" if weekly_used is None else str(weekly_used)
        profile["resetCreditsAvailable"] = ""
        profile["limitReachedType"] = ""
        if session_used is not None and float(session_used) >= 100:
            profile["limitReachedType"] = "Claude session limit"
        if weekly_used is not None and float(weekly_used) >= 100:
            profile["limitReachedType"] = "Claude weekly limit"
        profile["shortLimitResetUtc"] = str(usage_parsed.get("sessionResetUtc") or "") if isinstance(usage_parsed, dict) else ""
        profile["weeklyLimitResetUtc"] = str(usage_parsed.get("weeklyResetUtc") or "") if isinstance(usage_parsed, dict) else ""
        profile["weeklyResetEstimateUtc"] = profile["weeklyLimitResetUtc"]
        profile["weeklyResetEstimateSource"] = "claude-usage" if profile["weeklyLimitResetUtc"] else ""
        profile["usageDailyBuckets"] = daily_buckets
        recent_tokens = sum(int(bucket.get("tokens") or 0) for bucket in daily_buckets[-7:])
        recent_messages = sum(int(bucket.get("messageCount") or 0) for bucket in daily_buckets[-7:])
        profile["usageSummary"] = {
            "desktopReady": desktop.get("ready"),
            "desktopSummary": desktop.get("summary"),
            "cliStatus": cli_status.strip(),
            "claudeAuthStatus": auth_info,
            "subscriptionType": auth_info.get("subscriptionType") or "",
            "authMethod": auth_info.get("authMethod") or "",
            "apiProvider": auth_info.get("apiProvider") or "",
            "sessionExpires": desktop.get("sessionExpires"),
            "claudeUsageStatus": usage_probe.get("raw") or "",
            "claudeUsageError": usage_probe.get("error") or "",
            "last7dTokens": recent_tokens,
            "last7dMessages": recent_messages,
        }
        profile["lastUsageError"] = str(usage_probe.get("error") or "")
        if desktop.get("ready") or bool(auth_info.get("loggedIn")):
            profile["lastLimitsError"] = ""
            return {"ok": True, "provider": "claude", "desktop": desktop, "cliStatus": cli_status, "usage": usage_probe}
        profile["lastLimitsError"] = str(desktop.get("summary") or "Claude Desktop login not detected.")
        return {"ok": False, "provider": "claude", "error": profile["lastLimitsError"], "desktop": desktop, "cliStatus": cli_status, "usage": usage_probe}

    def refresh_cursor_profile(self, profile: dict) -> dict:
        desktop_status = cursor_local_account_status()
        agent_status = self.cursor_agent_status()
        about = self.cursor_agent_about()
        version = (
            str(about.get("cliVersion") or "")
            or windows_file_version(self.cursor_desktop_path)
            or self.cursor_cli_version()
        )
        ready = bool(agent_status.get("isAuthenticated") or desktop_status.get("ready"))
        email = str(agent_status.get("email") or agent_status.get("userEmail") or desktop_status.get("email") or "")
        plan = str(about.get("subscriptionTier") or desktop_status.get("plan") or "")
        state_text = str(agent_status.get("status") or desktop_status.get("status") or "")
        profile["lastLimitsRefreshUtc"] = iso_utc_now()
        profile["accountName"] = str(desktop_status.get("name") or "")
        profile["accountEmail"] = email
        profile["accountPlan"] = plan
        profile["accountPlanStatus"] = state_text
        profile["accountType"] = str(desktop_status.get("accountType") or "cursor-agent")
        profile["providerVersion"] = version
        profile["usageSummary"] = {
            "providerVersion": version,
            "desktopPath": self.cursor_desktop_path,
            "cliPath": self.cursor_cli_path,
            "agentPath": self.cursor_agent_path,
            "accountSummary": desktop_status.get("summary") or agent_status.get("message") or "",
            "membershipType": plan,
            "subscriptionStatus": state_text,
            "cursorAgentStatus": agent_status,
            "cursorAgentAbout": about,
        }
        profile["lastUsageError"] = "Cursor usage/limits are not exposed through local state."
        if not self.cursor_desktop_path and not self.cursor_cli_path and not self.cursor_agent_path:
            profile["lastLimitsError"] = "Cursor is not installed."
            return {"ok": False, "provider": "cursor", "error": profile["lastLimitsError"], "status": desktop_status}
        if not ready:
            profile["lastLimitsError"] = str(agent_status.get("message") or desktop_status.get("summary") or "Cursor login not detected.")
            return {
                "ok": False,
                "provider": "cursor",
                "error": profile["lastLimitsError"],
                "status": desktop_status,
                "agentStatus": agent_status,
            }
        profile["lastLimitsError"] = ""
        return {"ok": True, "provider": "cursor", "status": desktop_status, "agentStatus": agent_status}

    def refresh_antigravity_profile(self, profile: dict) -> dict:
        status = antigravity_local_account_status()
        version = windows_file_version(self.antigravity_desktop_path)
        profile["lastLimitsRefreshUtc"] = iso_utc_now()
        profile["accountName"] = str(status.get("name") or "")
        profile["accountEmail"] = str(status.get("email") or "")
        profile["accountPlan"] = str(status.get("plan") or "")
        profile["accountPlanStatus"] = str(status.get("status") or "")
        profile["accountType"] = str(status.get("accountType") or "")
        profile["providerVersion"] = version
        profile["usageSummary"] = {
            "providerVersion": version,
            "desktopPath": self.antigravity_desktop_path,
            "cliPath": self.antigravity_cli_path,
            "accountSummary": status.get("summary") or "",
            "profileUrl": status.get("profileUrl") or "",
        }
        profile["lastUsageError"] = "Antigravity usage/limits are not exposed through local state."
        if not self.antigravity_desktop_path:
            profile["lastLimitsError"] = "Antigravity is not installed."
            return {"ok": False, "provider": "antigravity", "error": profile["lastLimitsError"], "status": status}
        if not status.get("ready"):
            profile["lastLimitsError"] = str(status.get("summary") or "Antigravity login not detected.")
            return {"ok": False, "provider": "antigravity", "error": profile["lastLimitsError"], "status": status}
        profile["lastLimitsError"] = ""
        return {"ok": True, "provider": "antigravity", "status": status}

    def refresh_selected_limits(self) -> None:
        profile = self.selected_required()
        if profile is None:
            return

        def task() -> None:
            result = self.refresh_profile_limits(profile)
            record_profile_history(profile, refresh_reason="manual")
            self.save_current_profiles()
            name = profile.get("name", "Account")
            if result.get("ok"):
                if provider_key(profile) == "claude":
                    self.log_threadsafe(f"Refreshed Claude Desktop and Claude Code status for {name}.")
                elif provider_key(profile) in {"cursor", "antigravity"}:
                    self.log_threadsafe(f"Refreshed {provider_label(profile)} local account metadata for {name}.")
                else:
                    self.log_threadsafe(f"Refreshed real limits and usage for {name}.")
            else:
                self.log_threadsafe(f"Could not refresh {name}: {result.get('error')}")

        self.run_task(f"Refreshing {profile.get('name', 'Account')}...", task)

    def refresh_all_limits(self) -> None:
        def task() -> None:
            for profile in self.profiles:
                name = profile.get("name", "Account")
                self.log_threadsafe(f"Refreshing {name}...")
                try:
                    result = self.refresh_profile_limits(profile)
                    record_profile_history(profile, refresh_reason="refresh-all")
                    if result.get("ok"):
                        self.log_threadsafe(f"Refreshed {name}.")
                    else:
                        self.log_threadsafe(f"Could not refresh {name}: {result.get('error')}")
                except Exception as error:
                    profile["lastLimitsRefreshUtc"] = iso_utc_now()
                    profile["lastLimitsError"] = str(error)
                    record_profile_history(profile, refresh_reason="refresh-all-error")
                    self.log_threadsafe(f"Could not refresh {name}: {error}")
            self.save_current_profiles()
            self.next_auto_refresh_at = dt.datetime.now() + dt.timedelta(minutes=self.auto_refresh_minutes)

        self.run_task("Refreshing all accounts...", task)

    def auto_refresh_all_limits(self) -> None:
        if self.busy:
            return

        def task() -> None:
            failures: list[str] = []
            refreshed = 0
            for profile in self.profiles:
                name = profile.get("name", "Account")
                try:
                    result = self.refresh_profile_limits(profile)
                    record_profile_history(profile, refresh_reason="auto")
                    if result.get("ok"):
                        refreshed += 1
                    else:
                        failures.append(f"{name}: {result.get('error')}")
                except Exception as error:
                    profile["lastLimitsRefreshUtc"] = iso_utc_now()
                    profile["lastLimitsError"] = str(error)
                    record_profile_history(profile, refresh_reason="auto-error")
                    failures.append(f"{name}: {error}")
            self.save_current_profiles()
            self.next_auto_refresh_at = dt.datetime.now() + dt.timedelta(minutes=self.auto_refresh_minutes)
            if failures:
                self.log_threadsafe(f"Auto-refresh finished: {refreshed} refreshed, {len(failures)} issue(s). " + " | ".join(failures[:3]))
            else:
                self.log_threadsafe(f"Auto-refresh finished: {refreshed} account(s) updated. Next at {self.next_auto_refresh_at.strftime('%H:%M')}.")

        self.run_task("Auto-refreshing usage and limits...", task)

    def run_codex_capture(self, profile: dict, args: list[str], timeout: int = 90) -> str:
        if not self.codex_cli_path:
            raise RuntimeError(self.codex_cli_error or "codex.exe was not found.")
        ensure_file_credential_store(profile)
        workspace = Path(str(profile.get("workspace") or DEFAULT_WORKSPACE))
        workspace.mkdir(parents=True, exist_ok=True)
        process = run_capture(self.codex_cli_path, args, workspace, env={"CODEX_HOME": str(profile.get("codexHome"))}, timeout=timeout)
        parts = [f"Exit code: {process.returncode}"]
        if process.stdout.strip():
            parts.append(process.stdout.strip())
        if process.stderr.strip():
            parts.append(process.stderr.strip())
        return "\n\n".join(parts)

    def run_claude_capture(self, profile: dict, args: list[str], timeout: int = 90, redacted: bool = True) -> str:
        if not self.claude_code_path:
            return "Claude Code CLI not found."
        workspace = Path(str(profile.get("workspace") or DEFAULT_WORKSPACE))
        workspace.mkdir(parents=True, exist_ok=True)
        process = run_capture(
            self.claude_code_path,
            args,
            workspace,
            env={"CLAUDE_CONFIG_DIR": str(claude_profile_home(profile))},
            timeout=timeout,
        )
        parts = [f"Exit code: {process.returncode}"]
        if process.stdout.strip():
            parts.append(process.stdout.strip())
        if process.stderr.strip():
            parts.append(process.stderr.strip())
        output = "\n\n".join(parts)
        return redact_auth_output(output) if redacted else output

    def run_claude_auth_status(self, profile: dict | None = None, redacted: bool = True) -> str:
        target = profile or {"workspace": str(DEFAULT_WORKSPACE)}
        return self.run_claude_capture(target, ["auth", "status"], timeout=45, redacted=redacted)

    def cursor_cli_version(self) -> str:
        if not self.cursor_cli_path:
            return ""
        DEFAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)
        try:
            process = run_capture(self.cursor_cli_path, ["--version"], DEFAULT_WORKSPACE, timeout=15)
        except Exception:
            return ""
        first = next((line.strip() for line in process.stdout.splitlines() if line.strip()), "")
        return first

    def cursor_agent_json(self, args: list[str], timeout: int = 15) -> dict:
        if not self.cursor_agent_path:
            return {}
        DEFAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)
        try:
            process = run_capture(self.cursor_agent_path, args, DEFAULT_WORKSPACE, timeout=timeout)
        except Exception as error:
            return {"error": str(error)}
        output = process.stdout.strip() or process.stderr.strip()
        if not output:
            return {"exitCode": process.returncode}
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return {"exitCode": process.returncode, "raw": redact_auth_output(output)}
        return payload if isinstance(payload, dict) else {"value": payload}

    def cursor_agent_status(self) -> dict:
        return self.cursor_agent_json(["status", "--format", "json"], timeout=20)

    def cursor_agent_about(self) -> dict:
        return self.cursor_agent_json(["about", "--format", "json"], timeout=20)

    def run_claude_usage_probe(self, profile: dict) -> dict:
        if not self.claude_code_path:
            return {"ok": False, "error": "Claude Code CLI not found."}
        workspace = Path(str(profile.get("workspace") or DEFAULT_WORKSPACE))
        workspace.mkdir(parents=True, exist_ok=True)
        process = run_capture(
            self.claude_code_path,
            ["-p", "/usage", "--output-format", "json"],
            workspace,
            env={"CLAUDE_CONFIG_DIR": str(claude_profile_home(profile))},
            timeout=45,
        )
        stdout = process.stdout.strip()
        stderr = process.stderr.strip()
        if process.returncode != 0:
            return {"ok": False, "error": redact_auth_output(stderr or stdout or f"Claude usage probe failed with exit code {process.returncode}.")}
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return {"ok": False, "error": redact_auth_output(stdout or stderr or "Claude usage probe returned non-JSON output.")}
        usage_text = str(payload.get("result") or "")
        parsed = parse_claude_usage_text(usage_text)
        return {"ok": True, "raw": redact_auth_output(usage_text), "parsed": parsed}

    def open_claude_cli(self, profile: dict) -> None:
        if not self.claude_code_path:
            messagebox.showerror("Claude Code not found", "Claude Code CLI was not found under the Claude roaming profile.")
            return
        workspace = Path(str(profile.get("workspace") or DEFAULT_WORKSPACE))
        workspace.mkdir(parents=True, exist_ok=True)
        script = (
            'Write-Host "Claude Code"\n'
            f"$env:CLAUDE_CONFIG_DIR = {quote_ps(claude_profile_home(profile))}\n"
            f"& {quote_ps(self.claude_code_path)}\n"
        )
        self.start_visible_powershell(f"Claude Code - {profile.get('name', 'Account')}", script, workspace)
        self.log(f"Opened Claude Code CLI for {profile.get('name', 'Account')}.")

    def start_claude_login(self, profile: dict) -> None:
        if not self.claude_code_path:
            messagebox.showerror("Claude Code not found", "Claude Code CLI was not found under the Claude roaming profile.")
            return
        workspace = Path(str(profile.get("workspace") or DEFAULT_WORKSPACE))
        workspace.mkdir(parents=True, exist_ok=True)
        script = (
            'Write-Host "Claude Code auth login"\n'
            f"$env:CLAUDE_CONFIG_DIR = {quote_ps(claude_profile_home(profile))}\n"
            f"& {quote_ps(self.claude_code_path)} auth login\n"
            'Write-Host ""\n'
            'Write-Host "Login command finished. Use Refresh or Status to verify."\n'
        )
        self.start_visible_powershell(f"Claude Code login - {profile.get('name', 'Account')}", script, workspace)
        self.log(f"Opened Claude Code login window for {profile.get('name', 'Account')}.")

    def open_claude_desktop(self, profile: dict) -> None:
        if not self.claude_desktop_path:
            messagebox.showerror("Claude Desktop not found", "Claude Desktop was not found in WindowsApps.")
            return
        subprocess.Popen([self.claude_desktop_path], cwd=str(Path(self.claude_desktop_path).parent))
        self.log(f"Opened Claude Desktop for {profile.get('name', 'Account')}.")

    def open_cursor_desktop(self, profile: dict) -> None:
        workspace = Path(str(profile.get("workspace") or DEFAULT_WORKSPACE))
        workspace.mkdir(parents=True, exist_ok=True)
        if self.cursor_desktop_path:
            subprocess.Popen([self.cursor_desktop_path, str(workspace)], cwd=str(workspace))
        elif self.cursor_cli_path:
            subprocess.Popen([self.cursor_cli_path, str(workspace)], cwd=str(workspace))
        else:
            messagebox.showerror("Cursor not found", "Cursor desktop/CLI was not found.")
            return
        self.log(f"Opened Cursor for {profile.get('name', 'Account')}.")

    def open_cursor_cli(self, profile: dict) -> None:
        workspace = Path(str(profile.get("workspace") or DEFAULT_WORKSPACE))
        workspace.mkdir(parents=True, exist_ok=True)
        if self.cursor_agent_path:
            script = (
                'Write-Host "Cursor Agent"\n'
                f"& {quote_ps(self.cursor_agent_path)} --workspace {quote_ps(workspace)}\n"
            )
            self.start_visible_powershell(f"Cursor Agent - {profile.get('name', 'Account')}", script, workspace)
            self.log(f"Opened Cursor Agent for {profile.get('name', 'Account')}.")
            return
        if self.cursor_cli_path:
            script = (
                'Write-Host "Cursor desktop launcher"\n'
                f"& {quote_ps(self.cursor_cli_path)} {quote_ps(workspace)}\n"
            )
            self.start_visible_powershell(f"Cursor CLI - {profile.get('name', 'Account')}", script, workspace)
            self.log(f"Opened Cursor CLI for {profile.get('name', 'Account')}.")
            return
        messagebox.showerror("Cursor CLI not found", "Cursor Agent and cursor.cmd were not found.")

    def open_antigravity_desktop(self, profile: dict) -> None:
        if not self.antigravity_desktop_path:
            messagebox.showerror("Antigravity not found", "Antigravity.exe was not found.")
            return
        subprocess.Popen([self.antigravity_desktop_path], cwd=str(Path(self.antigravity_desktop_path).parent))
        self.log(f"Opened Antigravity desktop home for {profile.get('name', 'Account')}.")

    def open_antigravity_cli(self, profile: dict) -> None:
        workspace = Path(str(profile.get("workspace") or DEFAULT_WORKSPACE))
        workspace.mkdir(parents=True, exist_ok=True)
        if self.antigravity_cli_path and Path(self.antigravity_cli_path).name.lower() != "agy-node.cmd":
            script = (
                'Write-Host "Antigravity CLI"\n'
                f"& {quote_ps(self.antigravity_cli_path)}\n"
            )
            self.start_visible_powershell(f"Antigravity CLI - {profile.get('name', 'Account')}", script, workspace)
            self.log(f"Opened Antigravity CLI for {profile.get('name', 'Account')}.")
            return
        if self.antigravity_desktop_path:
            script = (
                'Write-Host "Antigravity CLI shim is not exposed as a healthy standalone agy command on this install."\n'
                'Write-Host "Launching Antigravity desktop home instead."\n'
                f"& {quote_ps(self.antigravity_desktop_path)}\n"
            )
            self.start_visible_powershell(f"Antigravity - {profile.get('name', 'Account')}", script, workspace)
            self.log("Antigravity CLI shim unavailable; opened desktop fallback.")
            return
        messagebox.showerror("Antigravity not found", "Antigravity desktop/CLI was not found.")

    def start_cursor_login(self, profile: dict) -> None:
        workspace = Path(str(profile.get("workspace") or DEFAULT_WORKSPACE))
        workspace.mkdir(parents=True, exist_ok=True)
        if self.cursor_agent_path:
            script = (
                'Write-Host "Cursor Agent login"\n'
                f"& {quote_ps(self.cursor_agent_path)} login\n"
                'Write-Host ""\n'
                'Write-Host "Login command finished. Use Refresh or Status to verify."\n'
            )
            self.start_visible_powershell(f"Cursor Agent login - {profile.get('name', 'Account')}", script, workspace)
            self.log(f"Opened Cursor Agent login window for {profile.get('name', 'Account')}.")
            return
        self.open_cursor_desktop(profile)
        messagebox.showinfo("Cursor login", "Sign in inside Cursor, then use Refresh or Status in AI Account Hub.")

    def start_antigravity_login(self, profile: dict) -> None:
        self.open_antigravity_desktop(profile)
        messagebox.showinfo("Antigravity login", "Sign in inside Antigravity, then use Refresh or Status in AI Account Hub.")

    def auth_blocking_reason(self, profile: dict, status_output: str) -> str:
        if is_revoked_token_message(status_output):
            message = "Your Codex refresh token was revoked. Use Login for this profile, then try Switch Desktop again."
            mark_auth_error(profile, message)
            self.save_current_profiles()
            return message
        if is_not_logged_in_message(status_output):
            message = "This profile is not logged in. Use Login for this profile, then try Switch Desktop again."
            mark_auth_error(profile, message)
            self.save_current_profiles()
            return message
        if re.search(r"Exit code:\s*[1-9]\d*", status_output):
            message = "Codex login status failed for this profile. Use Status or Login before switching Desktop."
            profile["lastLimitsError"] = message
            self.save_current_profiles()
            return message
        return ""

    def verify_profile_auth_for_switch(self, profile: dict) -> None:
        if not has_profile_auth(profile):
            raise RuntimeError(f"No auth.json found for {profile.get('name', 'Account')}. Use Login first.")
        status_output = self.run_codex_capture(profile, ["login", "status"], timeout=60)
        reason = self.auth_blocking_reason(profile, status_output)
        if reason:
            raise RuntimeError(reason)

    def status_selected(self) -> None:
        profile = self.selected_required()
        if profile is None:
            return
        if provider_key(profile) == "claude":
            def task() -> None:
                result = self.refresh_claude_profile(profile)
                record_profile_history(profile, refresh_reason="status")
                self.save_current_profiles()
                desktop = result.get("desktop") or {}
                cli_status = str(result.get("cliStatus") or "")
                usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
                usage_text = str(usage.get("raw") or usage.get("error") or "No usage output.")
                self.log_threadsafe(
                    f"Claude status for {profile.get('name', 'Account')}:\n"
                    f"Desktop: {desktop.get('summary', 'Unknown')}\n\n"
                    f"Usage:\n{usage_text}\n\n"
                    f"CLI:\n{cli_status}"
                )

            self.run_task(f"Checking Claude status for {profile.get('name', 'Account')}...", task)
            return

        if provider_key(profile) in {"cursor", "antigravity"}:
            def task() -> None:
                result = self.refresh_profile_limits(profile)
                record_profile_history(profile, refresh_reason="status")
                self.save_current_profiles()
                summary = profile.get("usageSummary") if isinstance(profile.get("usageSummary"), dict) else {}
                self.log_threadsafe(
                    f"{provider_label(profile)} status for {profile.get('name', 'Account')}:\n"
                    f"Account: {account_identity_label(profile)}\n"
                    f"Plan: {account_plan_label(profile)}\n"
                    f"Desktop: {summary.get('desktopPath') or 'not applicable'}\n"
                    f"CLI: {summary.get('cliPath') or 'not found'}\n"
                    f"Version: {profile.get('providerVersion') or '-'}\n"
                    f"State: {'Ready' if result.get('ok') else profile.get('lastLimitsError', 'Not Ready')}"
                )

            self.run_task(f"Checking {provider_label(profile)} status for {profile.get('name', 'Account')}...", task)
            return

        def task() -> None:
            output = self.run_codex_capture(profile, ["login", "status"])
            reason = self.auth_blocking_reason(profile, output)
            if reason:
                self.log_threadsafe(reason)
            self.log_threadsafe(f"Status for {profile.get('name', 'Account')}:\n{output}")

        self.run_task(f"Checking status for {profile.get('name', 'Account')}...", task)

    def doctor_selected(self) -> None:
        profile = self.selected_required()
        if profile is None:
            return
        if provider_key(profile) == "claude":
            def task() -> None:
                output = self.run_claude_capture(profile, ["doctor"], timeout=60)
                self.log_threadsafe(f"Claude doctor for {profile.get('name', 'Account')}:\n{output}")

            self.run_task(f"Running Claude doctor for {profile.get('name', 'Account')}...", task)
            return

        if provider_key(profile) in {"cursor", "antigravity"}:
            def task() -> None:
                result = self.refresh_profile_limits(profile)
                summary = profile.get("usageSummary") if isinstance(profile.get("usageSummary"), dict) else {}
                record_profile_history(profile, refresh_reason="doctor")
                self.save_current_profiles()
                self.log_threadsafe(
                    f"{provider_label(profile)} doctor for {profile.get('name', 'Account')}:\n"
                    f"Refresh ok: {bool(result.get('ok'))}\n"
                    f"Account state: {profile.get('lastLimitsError') or 'Ready'}\n"
                    f"Plan source: local app state\n"
                    f"Desktop path: {summary.get('desktopPath') or 'not found'}\n"
                    f"CLI path: {summary.get('cliPath') or 'not found'}\n"
                    f"Version: {profile.get('providerVersion') or '-'}"
                )

            self.run_task(f"Running {provider_label(profile)} doctor for {profile.get('name', 'Account')}...", task)
            return

        def task() -> None:
            output = self.run_codex_capture(profile, ["doctor", "--summary"])
            self.log_threadsafe(f"Doctor for {profile.get('name', 'Account')}:\n{output}")

        self.run_task(f"Running doctor for {profile.get('name', 'Account')}...", task)

    def start_visible_powershell(self, title: str, script_text: str, working_directory: str | Path) -> None:
        full_script = f"$Host.UI.RawUI.WindowTitle = {quote_ps(title)}\nSet-Location -LiteralPath {quote_ps(working_directory)}\n{script_text}"
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", full_script],
            cwd=str(working_directory),
            creationflags=CREATE_NEW_CONSOLE,
        )

    def login_selected(self) -> None:
        self.start_login(device=False)

    def device_login_selected(self) -> None:
        self.start_login(device=True)

    def open_online_link(self, profile: dict, link: dict) -> None:
        url = str(link.get("url") or "").strip()
        label = str(link.get("label") or "Online").strip()
        if not is_safe_online_url(url):
            messagebox.showerror("Invalid URL", f"{label} does not have a safe http/https URL.")
            return
        command = browser_command_for_url(profile, url)
        try:
            if command:
                subprocess.Popen(command, shell=True)
                self.log(f"Opened {label} for {profile.get('name', 'Account')} using custom browser command.")
            elif uses_isolated_browser_profile(profile):
                browser_path = locate_account_browser_path()
                if not browser_path:
                    webbrowser.open(url, new=2)
                    self.log(f"Opened {label} in the system browser because Chrome/Edge/Brave was not found: {url}")
                    return
                had_cookie = browser_profile_has_cookie_for_url(profile, url)
                profile_dir = browser_profile_dir_for_profile(profile)
                profile_dir.mkdir(parents=True, exist_ok=True)
                subprocess.Popen(browser_profile_launch_args(profile, url, browser_path), cwd=str(profile_dir))
                if had_cookie:
                    self.log(f"Opened {label} for {profile.get('name', 'Account')} with saved browser login: {profile_dir}")
                else:
                    self.log(f"Opened {label} for {profile.get('name', 'Account')} in a separate browser profile. Log in once there; future Online clicks reuse those cookies. Profile: {profile_dir}")
            else:
                webbrowser.open(url, new=2)
                self.log(f"Opened {label} for {profile.get('name', 'Account')}: {url}")
        except Exception as error:
            self.log(f"Could not open {label}: {error}")

    def online_selected(self) -> None:
        profile = self.selected_required()
        if profile is None:
            return
        links = online_links_for_profile(profile)
        if not links:
            messagebox.showinfo("No online links", f"No online links are configured for {profile.get('name', 'Account')}.")
            return
        menu = tk.Menu(self, tearoff=0, bg=PANEL, fg=INK, activebackground=PANEL_ALT, activeforeground=INK)
        if uses_isolated_browser_profile(profile) and browser_profile_web_login_label(profile, links) == "Web login needed":
            menu.add_command(label="Log in once in this account browser profile", state="disabled")
            menu.add_separator()
        for link in links:
            menu.add_command(label=str(link["label"]), command=lambda item=link: self.open_online_link(profile, item))
        button = getattr(self, "account_action_buttons", {}).get("online")
        try:
            if button is not None:
                menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
            else:
                menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    def start_login(self, device: bool) -> None:
        profile = self.selected_required()
        if profile is None:
            return
        if provider_key(profile) == "claude":
            if device:
                messagebox.showinfo("Claude login", "Claude Code does not use this Codex device-login button. Use Login for Claude Code auth.")
                return
            self.start_claude_login(profile)
            return
        if provider_key(profile) == "cursor":
            if device:
                messagebox.showinfo("Cursor login", "Cursor does not use this Codex device-login button. Use Login to open Cursor and sign in.")
                return
            self.start_cursor_login(profile)
            return
        if provider_key(profile) == "antigravity":
            if device:
                messagebox.showinfo("Antigravity login", "Antigravity does not use this Codex device-login button. Use Login to open Antigravity and sign in.")
                return
            self.start_antigravity_login(profile)
            return
        if not self.codex_cli_path:
            messagebox.showerror("Codex not found", self.codex_cli_error or "codex.exe was not found.")
            return
        ensure_file_credential_store(profile)
        self.save_current_profiles()
        workspace = Path(str(profile.get("workspace") or DEFAULT_WORKSPACE))
        device_arg = " --device-auth" if device else ""
        script = (
            f"$env:CODEX_HOME = {quote_ps(profile.get('codexHome'))}\n"
            'Write-Host "CODEX_HOME=$env:CODEX_HOME"\n'
            f"& {quote_ps(self.codex_cli_path)} login{device_arg}\n"
            'Write-Host ""\n'
            'Write-Host "Login command finished. Use Refresh Selected or Status to verify."\n'
        )
        self.start_visible_powershell(f"Codex login - {profile.get('name', 'Account')}", script, workspace)
        self.log(f"Opened login window for {profile.get('name', 'Account')}.")

    def open_selected_cli(self) -> None:
        profile = self.selected_required()
        if profile is None:
            return
        if provider_key(profile) == "claude":
            self.open_claude_cli(profile)
            return
        if provider_key(profile) == "cursor":
            self.open_cursor_cli(profile)
            return
        if provider_key(profile) == "antigravity":
            self.open_antigravity_cli(profile)
            return
        if not self.confirm_open_if_not_ready(profile, "CLI"):
            return
        if not self.codex_cli_path:
            messagebox.showerror("Codex not found", self.codex_cli_error or "codex.exe was not found.")
            return
        ensure_file_credential_store(profile)
        self.save_current_profiles()
        workspace = Path(str(profile.get("workspace") or DEFAULT_WORKSPACE))
        script = (
            f"$env:CODEX_HOME = {quote_ps(profile.get('codexHome'))}\n"
            'Write-Host "CODEX_HOME=$env:CODEX_HOME"\n'
            f"& {quote_ps(self.codex_cli_path)}\n"
        )
        self.start_visible_powershell(f"Codex CLI - {profile.get('name', 'Account')}", script, workspace)
        self.log(f"Opened CLI for {profile.get('name', 'Account')}.")

    def confirm_open_if_not_ready(self, profile: dict, surface: str) -> bool:
        state = effective_state(profile)
        if state == "ready":
            return True
        details = []
        if state == "login":
            if provider_key(profile) == "codex":
                details.append("this profile does not have auth.json yet")
            else:
                details.append(f"{provider_label(profile)} login was not detected")
        if provider_key(profile) == "codex" and cooldown_remaining(profile).total_seconds() > 0:
            details.append(f"local timer has {format_local_timer(profile)} remaining")
        if provider_key(profile) == "codex" and str(profile.get("limitReachedType", "")).strip():
            details.append(str(profile.get("limitReachedType")))
        if provider_key(profile) == "codex" and is_limit_exhausted(profile.get("shortLimitUsedPercent")):
            details.append("5h limit is exhausted")
        if provider_key(profile) == "codex" and is_limit_exhausted(profile.get("weeklyLimitUsedPercent")):
            details.append("weekly limit is exhausted")
        if str(profile.get("lastLimitsError", "")).strip():
            details.append(str(profile.get("lastLimitsError")))
        reason = "; ".join(details) or "it is not marked Ready"
        return messagebox.askokcancel("Account is not ready", f"{profile.get('name', 'Account')} is Not Ready because {reason}.\n\nOpen {surface} anyway?")

    def seed_selected_config(self) -> None:
        profile = self.selected_required()
        if profile is None:
            return
        try:
            message = ensure_file_credential_store(profile)
            self.log(message)
        except Exception as error:
            self.log(str(error))

    def open_selected_home(self) -> None:
        profile = self.selected_required()
        if profile is None:
            return
        try:
            if provider_key(profile) == "claude":
                home = claude_profile_home(profile)
                home.mkdir(parents=True, exist_ok=True)
                os.startfile(str(home))  # type: ignore[attr-defined]
                self.log(f"Opened Claude Code profile home: {home}.")
                return
            if provider_key(profile) == "cursor":
                CURSOR_ROAMING_HOME.mkdir(parents=True, exist_ok=True)
                os.startfile(str(CURSOR_ROAMING_HOME))  # type: ignore[attr-defined]
                self.log(f"Opened Cursor profile home: {CURSOR_ROAMING_HOME}.")
                return
            if provider_key(profile) == "antigravity":
                ANTIGRAVITY_ROAMING_HOME.mkdir(parents=True, exist_ok=True)
                os.startfile(str(ANTIGRAVITY_ROAMING_HOME))  # type: ignore[attr-defined]
                self.log(f"Opened Antigravity profile home: {ANTIGRAVITY_ROAMING_HOME}.")
                return
            ensure_profile_home(profile)
            os.startfile(str(profile.get("codexHome")))  # type: ignore[attr-defined]
            self.log(f"Opened {profile.get('codexHome')}.")
        except Exception as error:
            self.log(str(error))

    def set_selected_cooldown(self) -> None:
        profile = self.selected_required()
        if profile is None:
            return
        until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5)
        profile["cooldownUntilUtc"] = until.isoformat()
        self.save_current_profiles()
        self.log(f"Started 5-hour local timer for {profile.get('name', 'Account')}.")
        self.render()

    def clear_selected_cooldown(self) -> None:
        profile = self.selected_required()
        if profile is None:
            return
        profile["cooldownUntilUtc"] = ""
        self.save_current_profiles()
        self.log(f"Cleared local timer for {profile.get('name', 'Account')}.")
        self.render()

    def use_reset_credit(self) -> None:
        profile = self.selected_required()
        if profile is None:
            return
        available_raw = str(profile.get("resetCreditsAvailable") or "").strip()
        try:
            available = int(available_raw)
        except ValueError:
            messagebox.showinfo("Refresh limits first", "Refresh Selected first so availability can be confirmed.")
            self.log(f"Did not use reset credit for {profile.get('name', 'Account')}: availability is unknown.")
            return
        if available < 1:
            messagebox.showinfo("No reset credit", f"{profile.get('name', 'Account')} does not currently report any available reset credits.")
            self.log(f"Did not use reset credit for {profile.get('name', 'Account')}: none available.")
            return
        ok = messagebox.askokcancel(
            "Use reset credit",
            f"Use one real Codex rate-limit reset credit for {profile.get('name', 'Account')}?\n\nReset credits available: {available}\n\nThis consumes an account reset credit if a window is eligible.",
            icon="warning",
        )
        if not ok:
            self.log(f"Cancelled reset-credit use for {profile.get('name', 'Account')}.")
            return

        def task() -> None:
            result = self.refresh_profile_limits(profile, action="consume-reset")
            record_profile_history(profile, refresh_reason="reset-credit")
            self.save_current_profiles()
            if not result.get("ok"):
                self.log_threadsafe(f"Could not use reset credit for {profile.get('name', 'Account')}: {result.get('error')}")
                return
            outcome = str(result.get("resetOutcome") or "")
            messages = {
                "reset": "Reset credit consumed. Eligible rate-limit windows were reset.",
                "nothingToReset": "No reset credit consumed: no current rate-limit window is eligible.",
                "noCredit": "No reset credit consumed: no earned reset credits are available.",
                "alreadyRedeemed": "Reset request was already redeemed. Limits refreshed.",
            }
            self.log_threadsafe(f"{profile.get('name', 'Account')}: {messages.get(outcome, f'Reset request completed. Outcome: {outcome}')}")

        self.run_task(f"Requesting reset credit use for {profile.get('name', 'Account')}...", task)

    def codex_desktop_process_report(self) -> str:
        script = r"""
$matches = @()
foreach ($process in @(Get-Process -ErrorAction SilentlyContinue)) {
    $path = ""
    try { $path = [string]$process.Path } catch { $path = "" }
    if ($path -match '\\WindowsApps\\OpenAI\.Codex_' -and $path -match '\\app\\(Codex|resources\\codex)\.exe$') {
        $matches += $process
    }
}
if ($matches.Count -eq 0) {
    Write-Output "No Codex Desktop background processes detected."
} else {
    Write-Output "$($matches.Count) Codex Desktop process(es) detected: $($matches.ProcessName -join ', ')."
}
"""
        process = run_capture("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], DEFAULT_WORKSPACE, timeout=15)
        return process.stdout.strip() or process.stderr.strip() or "Codex Desktop process check completed."

    def build_codex_switch_dry_run_report(self, profile: dict) -> str:
        workspace = Path(str(profile.get("workspace") or DEFAULT_WORKSPACE))
        auth_path = profile_auth_path(profile)
        default_auth = default_auth_path()
        marker = self.active_desktop_marker()
        lines = [
            f"Dry run for Codex Desktop switch to {profile.get('name', 'Account')}",
            f"Selected CODEX_HOME: {profile.get('codexHome')}",
            f"Workspace: {workspace}",
            f"Profile auth exists: {auth_path.exists()} ({auth_path})",
            f"Default desktop auth exists: {default_auth.exists()} ({default_auth})",
            f"Active marker: {marker.get('name') or 'none'}",
            f"Backup root: {DESKTOP_BACKUP_ROOT}",
            f"Codex CLI: {self.codex_cli_path or self.codex_cli_error or 'not found'}",
            self.codex_desktop_process_report(),
            "",
            "Planned real switch steps:",
            "1. Verify selected profile login status.",
            "2. Stop lingering Codex Desktop processes.",
            "3. Save current default desktop auth back to the previous active profile.",
            "4. Backup and clear the default desktop auth locally.",
            "5. Copy selected profile auth.json into the default Codex home.",
            "6. Relaunch Codex Desktop for the selected workspace.",
        ]
        if auth_path.exists() and self.codex_cli_path:
            try:
                status = self.run_codex_capture(profile, ["login", "status"], timeout=60)
                lines.extend(["", "Login status check:", status])
            except Exception as error:
                lines.extend(["", f"Login status check failed: {error}"])
        return "\n".join(lines)

    def dry_run_selected_desktop_switch(self) -> None:
        profile = self.selected_required()
        if profile is None:
            return
        if provider_key(profile) != "codex":
            messagebox.showinfo("Codex only", "Desktop switch dry-run is only for Codex profiles.")
            return

        def task() -> None:
            self.log_threadsafe(self.build_codex_switch_dry_run_report(profile))

        self.run_task(f"Dry-running Codex Desktop switch for {profile.get('name', 'Account')}...", task)

    def restore_default_desktop_backup(self) -> None:
        profile = self.selected_required()
        if profile is None:
            return
        if provider_key(profile) != "codex":
            messagebox.showinfo("Codex only", "Default desktop auth restore is only for Codex.")
            return
        primary_backup = DESKTOP_BACKUP_ROOT / "auth.json"
        backups = [primary_backup] if primary_backup.exists() else []
        backups.extend(sorted(DESKTOP_BACKUP_ROOT.glob("auth-before-local-clear-*.json"), key=lambda path: path.stat().st_mtime, reverse=True))
        backup = next((path for path in backups if path.exists()), None)
        if backup is None:
            messagebox.showinfo("No backup", f"No default desktop auth backup was found under {DESKTOP_BACKUP_ROOT}.")
            return
        ok = messagebox.askokcancel(
            "Restore Codex desktop auth",
            f"Restore this backup into the default Codex desktop auth file?\n\n{backup}\n\nThis does not revoke any account; it only copies a local backup file.",
            icon="warning",
        )
        if not ok:
            self.log("Cancelled default Codex desktop auth restore.")
            return

        def task() -> None:
            ensure_file_credential_store({"codexHome": str(DEFAULT_CODEX_HOME), "workspace": str(DEFAULT_WORKSPACE)})
            DEFAULT_CODEX_HOME.mkdir(parents=True, exist_ok=True)
            current_auth = default_auth_path()
            if current_auth.exists():
                DESKTOP_BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
                stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                shutil.copy2(current_auth, DESKTOP_BACKUP_ROOT / f"auth-before-restore-{stamp}.json")
            shutil.copy2(backup, current_auth)
            if DESKTOP_ACTIVE_PROFILE_PATH.exists():
                DESKTOP_ACTIVE_PROFILE_PATH.unlink()
            self.log_threadsafe(f"Restored default Codex desktop auth from {backup}. Active marker cleared.")

        self.run_task("Restoring default Codex desktop auth backup...", task)

    def switch_selected_desktop(self) -> None:
        profile = self.selected_required()
        if profile is None:
            return
        if provider_key(profile) == "claude":
            self.open_claude_desktop(profile)
            return
        if provider_key(profile) == "cursor":
            self.open_cursor_desktop(profile)
            return
        if provider_key(profile) == "antigravity":
            self.open_antigravity_desktop(profile)
            return
        if provider_key(profile) != "codex":
            messagebox.showinfo("Provider not wired", f"Desktop launch is not wired for {provider_label(profile)} yet.")
            return
        if not self.confirm_open_if_not_ready(profile, "Codex Desktop"):
            self.log(f"Cancelled desktop switch for {profile.get('name', 'Account')}.")
            return
        if not self.codex_cli_path:
            messagebox.showerror("Codex not found", self.codex_cli_error or "codex.exe was not found.")
            return

        def task() -> None:
            self.verify_profile_auth_for_switch(profile)
            self.log_threadsafe(f"Verified login for {profile.get('name', 'Account')}.")
            self.save_current_profiles()
            self.log_threadsafe(self.stop_codex_desktop_processes())
            save_back = self.sync_active_desktop_auth_back_to_profile()
            if save_back:
                self.log_threadsafe(save_back)
            self.log_threadsafe(self.clear_default_desktop_auth_local_only())
            self.log_threadsafe(self.sync_profile_auth_to_desktop_default(profile))
            self.start_codex_desktop(profile)
            self.log_threadsafe(f"Switched Codex Desktop to {profile.get('name', 'Account')} and requested relaunch.")

        self.run_task(f"Switching Codex Desktop to {profile.get('name', 'Account')}...", task)

    def stop_codex_desktop_processes(self) -> str:
        script = r"""
function Get-CodexDesktopProcess {
    $items = @()
    foreach ($process in @(Get-Process -ErrorAction SilentlyContinue)) {
        $path = ""
        try { $path = [string]$process.Path } catch { $path = "" }
        if ($path -match '\\WindowsApps\\OpenAI\.Codex_' -and $path -match '\\app\\(Codex|resources\\codex)\.exe$') {
            $items += $process
        }
    }
    return @($items)
}

$matches = @()
$matches = @(Get-CodexDesktopProcess)
if ($matches.Count -eq 0) {
    Write-Output "No Codex Desktop background processes were running."
    exit 0
}
$closed = 0
foreach ($process in $matches) {
    try {
        if ($process.ProcessName -eq "Codex" -and $process.MainWindowHandle -ne [IntPtr]::Zero) {
            if ($process.CloseMainWindow()) { $closed++ }
        }
    } catch {}
}

$deadline = [DateTime]::Now.AddSeconds(12)
do {
    Start-Sleep -Milliseconds 250
    $remaining = @(Get-CodexDesktopProcess)
} while ($remaining.Count -gt 0 -and [DateTime]::Now -lt $deadline)

$killed = 0
foreach ($process in $remaining) {
    try { $process.Kill(); $killed++ } catch {}
}

$finalDeadline = [DateTime]::Now.AddSeconds(5)
do {
    Start-Sleep -Milliseconds 250
    $final = @(Get-CodexDesktopProcess)
} while ($final.Count -gt 0 -and [DateTime]::Now -lt $finalDeadline)

if ($final.Count -gt 0) {
    Write-Output "Tried to stop $($matches.Count) Codex Desktop process(es). Graceful close requested: $closed. Force-stopped: $killed. Still running: $($final.Count)."
} else {
    Write-Output "Stopped $($matches.Count) Codex Desktop process(es). Graceful close requested: $closed. Force-stopped: $killed."
}
"""
        process = run_capture("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], DEFAULT_WORKSPACE, timeout=25)
        return (process.stdout.strip() or process.stderr.strip() or "Desktop process check completed.")

    def sync_active_desktop_auth_back_to_profile(self) -> str:
        if not DESKTOP_ACTIVE_PROFILE_PATH.exists():
            return ""
        default_auth = default_auth_path()
        if not default_auth.exists():
            return "No default desktop auth was available to save back to the active profile."
        try:
            marker = json.loads(DESKTOP_ACTIVE_PROFILE_PATH.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return "Could not read the previous desktop active-profile marker; skipping auth save-back."
        active_home = str(marker.get("codexHome") or "").strip()
        active_name = str(marker.get("name") or "previous profile")
        if not active_home:
            return "Previous desktop active-profile marker did not include a CODEX_HOME; skipping auth save-back."
        target_home = Path(active_home)
        target_home.mkdir(parents=True, exist_ok=True)
        shutil.copy2(default_auth, target_home / "auth.json")
        return f"Saved current desktop auth back to {active_name}."

    def clear_default_desktop_auth_local_only(self) -> str:
        ensure_file_credential_store({"codexHome": str(DEFAULT_CODEX_HOME), "workspace": str(DEFAULT_WORKSPACE)})
        auth_path = default_auth_path()

        backed_up = ""
        if auth_path.exists():
            DESKTOP_BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = DESKTOP_BACKUP_ROOT / f"auth-before-local-clear-{stamp}.json"
            shutil.copy2(auth_path, backup_path)
            backed_up = str(backup_path)

        removed = []
        if auth_path.exists():
            auth_path.unlink()
            removed.append(str(auth_path))
        if DESKTOP_ACTIVE_PROFILE_PATH.exists():
            DESKTOP_ACTIVE_PROFILE_PATH.unlink()

        suffix = f" Removed: {', '.join(removed)}." if removed else ""
        backup_note = f" Backup: {backed_up}." if backed_up else ""
        return f"Cleared default Codex desktop auth locally without revoking the refresh token.{suffix}{backup_note}"

    def sync_profile_auth_to_desktop_default(self, profile: dict) -> str:
        ensure_file_credential_store(profile)
        ensure_file_credential_store({"codexHome": str(DEFAULT_CODEX_HOME), "workspace": str(profile.get("workspace") or DEFAULT_WORKSPACE)})
        profile_auth = profile_auth_path(profile)
        if not profile_auth.exists():
            raise RuntimeError(f"No auth.json found for {profile.get('name', 'Account')}. Run Login for this profile first.")
        DEFAULT_CODEX_HOME.mkdir(parents=True, exist_ok=True)
        DESKTOP_BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        backup_auth = DESKTOP_BACKUP_ROOT / "auth.json"
        current_default_auth = default_auth_path()
        if current_default_auth.exists() and not backup_auth.exists():
            shutil.copy2(current_default_auth, backup_auth)
        shutil.copy2(profile_auth, current_default_auth)
        marker = {"name": str(profile.get("name", "Account")), "codexHome": str(profile.get("codexHome")), "syncedAtUtc": iso_utc_now()}
        DESKTOP_ACTIVE_PROFILE_PATH.write_text(json.dumps(marker, indent=2), encoding="utf-8")
        return f"Synced {profile.get('name', 'Account')} auth into default Codex desktop home. Original default auth backup: {backup_auth}"

    def start_codex_desktop(self, profile: dict) -> None:
        workspace = Path(str(profile.get("workspace") or DEFAULT_WORKSPACE))
        workspace.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["CODEX_HOME"] = str(profile.get("codexHome"))
        subprocess.Popen(
            [self.codex_cli_path, "app", str(workspace)],
            cwd=str(workspace),
            env=env,
            creationflags=CREATE_NO_WINDOW,
        )

    def _tick(self) -> None:
        self._tick_after_id = None
        if self._closing:
            return
        now = dt.datetime.now()
        if self.auto_refresh_enabled and not self.busy and now >= self.next_auto_refresh_at:
            self.next_auto_refresh_at = now + dt.timedelta(minutes=self.auto_refresh_minutes)
            self.auto_refresh_all_limits()
        if not self.busy:
            self._refresh_live_status_labels()
            if self.active_section == "coding":
                self._render_coding_short_limit()
        if self._last_periodic_render_minute != now.minute and not self.busy:
            self._last_periodic_render_minute = now.minute
            self._update_buttons()
        if not self._closing:
            self._tick_after_id = self.after(1000, self._tick)

    def _bind_recursive(self, widget: tk.Widget, callback) -> None:
        widget.bind("<Button-1>", callback)
        for child in widget.winfo_children():
            self._bind_recursive(child, callback)

    def _clear(self, widget: tk.Misc) -> None:
        for child in widget.winfo_children():
            child.destroy()


if __name__ == "__main__":
    app = AccountCalendarApp()
    if "--self-test" in sys.argv:
        app.update_idletasks()
        app.destroy()
        print("AI Account Hub self-test passed")
    else:
        app.mainloop()
