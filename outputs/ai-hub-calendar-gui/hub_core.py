from __future__ import annotations

"""hub_core: the Tk-free backend for AI Account Hub.

Extracted from the original ai_hub_calendar_gui.py. Contains the constants and
pure logic -- profiles, provider probes/refresh, limit parsing, usage history,
discovery reuse, launch/browser helpers -- with no tkinter dependency. The
PySide6 app imports its shared provider logic from here.
"""


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
from urllib.parse import urlparse

MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))
CLAUDE_PERMISSION_BRIDGE_PATH = MODULE_DIR / "claude_permission_bridge.py"

from provider_discovery import (  # noqa: E402
    default_report_path,
    discover_provider_tools,
    load_fresh_report,
    write_discovery_report,
)

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
LAUNCHER_ROOT = Path(os.environ.get("AI_HUB_LAUNCHER_ROOT", str(Path.home() / ".codex-account-launcher"))).expanduser()
PROFILES_FILE = LAUNCHER_ROOT / "profiles.json"
DESKTOP_BACKUP_ROOT = LAUNCHER_ROOT / "desktop-default-backup"
DESKTOP_ACTIVE_PROFILE_PATH = LAUNCHER_ROOT / "desktop-active-profile.json"
SETTINGS_FILE = LAUNCHER_ROOT / "ai-hub-calendar-settings.json"
HISTORY_DB_FILE = LAUNCHER_ROOT / "ai-hub-history.sqlite3"
NATIVE_THREADS_FILE = LAUNCHER_ROOT / "native-threads.json"
BROWSER_PROFILES_ROOT = LAUNCHER_ROOT / "browser-profiles"
ICON_CACHE_ROOT = LAUNCHER_ROOT / "icon-cache"
DISCOVERY_REPORT_FILE = default_report_path()
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
    "codex": "#4d92d6",
    "claude": "#c17c4e",
    "cursor": "#a065c9",
    "antigravity": "#a86bd6",
    "api": "#19706b",
}

PROVIDER_INITIALS = {
    "codex": "CX",
    "claude": "CC",
    "cursor": "CU",
    "antigravity": "AG",
    "api": "ALL",
}

PROJECT_DOT_COLORS = ["#e8698f", "#4fb37a", "#9d5fd6", "#4a9fd6", "#dcb04a", "#d6614a"]

PROVIDER_CHOICES = [
    ("Codex", "codex"),
    ("Claude Code", "claude"),
    ("Cursor", "cursor"),
    ("Antigravity", "antigravity"),
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
    "claudeProfileType": "code",
    "claudeDesktopAccountUuid": "",
    "claudeDesktopCaptured": False,
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
    "antigravityPrintTimeout": "5m",
    "usageSummary": {},
    "usageDailyBuckets": [],
}

DESIGN_THEME_TOKENS = {
    "Midnight Slate": {
        "bg": "#070c11", "panel": "#0e1319", "panel2": "#171c23", "panelHover": "#21272e",
        "border": "#282f36", "borderStrong": "#404952",
        "text": "#eceff1", "text2": "#9fa5ac", "text3": "#666d74",
        "accent": "#2c90e8", "accentText": "#fcfcfc",
        "success": "#45b164", "warn": "#e3ae28", "danger": "#e1514e",
    },
    "Emerald Graphite": {
        "bg": "#080d09", "panel": "#0f1410", "panel2": "#171e18", "panelHover": "#212923",
        "border": "#29302a", "borderStrong": "#414b43",
        "text": "#edefed", "text2": "#a0a6a1", "text3": "#676e68",
        "accent": "#3eb268", "accentText": "#040b06",
        "success": "#3eb268", "warn": "#e3ae28", "danger": "#e1514e",
    },
    "Indigo Night": {
        "bg": "#0b0a12", "panel": "#12121a", "panel2": "#1b1b24", "panelHover": "#262530",
        "border": "#302f3a", "borderStrong": "#474654",
        "text": "#eeeef2", "text2": "#a4a3ad", "text3": "#6b6a75",
        "accent": "#9776fb", "accentText": "#fcfcfc",
        "success": "#45b164", "warn": "#e3ae28", "danger": "#e84d66",
    },
    "Warm Carbon": {
        "bg": "#110c08", "panel": "#1a130f", "panel2": "#231c18", "panelHover": "#2f2721",
        "border": "#372e29", "borderStrong": "#51453e",
        "text": "#f2eeea", "text2": "#aba39c", "text3": "#736a63",
        "accent": "#e78b30", "accentText": "#0f0703",
        "success": "#45b164", "warn": "#f2a618", "danger": "#e24947",
    },
    "Crimson Black": {
        "bg": "#060404", "panel": "#0f0808", "panel2": "#1a0f10", "panelHover": "#2a1b1b",
        "border": "#2e2021", "borderStrong": "#4c3738",
        "text": "#f6f0ef", "text2": "#aea1a0", "text3": "#736565",
        "accent": "#e62b34", "accentText": "#fcfcfc",
        "accentGradA": "#f93440", "accentGradB": "#55101d",
        "success": "#3eab5e", "warn": "#f09c17", "danger": "#f8495a",
    },
    "Neon Aurora": {
        "bg": "#07060f", "panel": "#0f0e19", "panel2": "#171724", "panelHover": "#242535",
        "border": "#2a2939", "borderStrong": "#434258",
        "text": "#f1f1f6", "text2": "#aaa9b7", "text3": "#6d6d7b",
        "accent": "#ac77fa", "accentText": "#fcfcfc",
        "accentGradA": "#bc77ff", "accentGradB": "#00bfdf", "accentGradC": "#f35cbc",
        "success": "#2bbb71", "warn": "#e6ad00", "danger": "#f34e6a",
    },
    "Sunset Ember": {
        "bg": "#0f0907", "panel": "#19100e", "panel2": "#241915", "panelHover": "#342521",
        "border": "#382a26", "borderStrong": "#57423e",
        "text": "#f7f0ed", "text2": "#b5a7a2", "text3": "#7a6b66",
        "accent": "#f27636", "accentText": "#120805",
        "accentGradA": "#ff8300", "accentGradB": "#e62845",
        "success": "#45b164", "warn": "#f5a400", "danger": "#ea3b48",
    },
    "Cobalt Chrome": {
        "bg": "#050b0f", "panel": "#0c1419", "panel2": "#131d24", "panelHover": "#1d2b33",
        "border": "#233037", "borderStrong": "#394a55",
        "text": "#eef2f5", "text2": "#a3acb3", "text3": "#667077",
        "accent": "#00a1db", "accentText": "#fcfcfc",
        "accentGradA": "#00c6d8", "accentGradB": "#2e5bda",
        "success": "#45b164", "warn": "#e3ae28", "danger": "#e1514e",
    },
}


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = str(color).strip().lstrip("#")
    if len(value) != 6:
        return (0, 0, 0)
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _blend_hex(foreground: str, background: str, alpha: float = 0.18) -> str:
    fg = _hex_to_rgb(foreground)
    bg = _hex_to_rgb(background)
    mixed = tuple(round(bg[index] + (fg[index] - bg[index]) * alpha) for index in range(3))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def _theme_palette(tokens: dict[str, str]) -> dict[str, str]:
    panel = tokens["panel"]
    panel2 = tokens["panel2"]
    accent = tokens["accent"]
    return {
        "BG": tokens["bg"],
        "PANEL": panel,
        "PANEL_ALT": panel2,
        "PANEL_HOVER": tokens["panelHover"],
        "INK": tokens["text"],
        "MUTED": tokens["text2"],
        "TEXT_FAINT": tokens["text3"],
        "LINE": tokens["border"],
        "LINE_STRONG": tokens["borderStrong"],
        "PRIMARY": accent,
        "PRIMARY_HOVER": tokens.get("accentGradB", accent),
        "PRIMARY_TEXT": tokens["accentText"],
        "GREEN": tokens["success"],
        "GREEN_SOFT": _blend_hex(tokens["success"], panel, 0.18),
        "RED": tokens["danger"],
        "RED_SOFT": _blend_hex(tokens["danger"], panel, 0.18),
        "AMBER": tokens["warn"],
        "AMBER_SOFT": _blend_hex(tokens["warn"], panel, 0.18),
        "BLUE": accent,
        "BLUE_SOFT": _blend_hex(accent, panel, 0.18),
        "DARK": tokens["bg"],
        "ACCENT_GRAD_A": tokens.get("accentGradA", accent),
        "ACCENT_GRAD_B": tokens.get("accentGradB", accent),
        "ACCENT_GRAD_C": tokens.get("accentGradC", ""),
        "METER_BG": _blend_hex(tokens["borderStrong"], panel2, 0.34),
        "CALENDAR_OUTSIDE": _blend_hex(tokens["border"], tokens["bg"], 0.26),
        "CARD_SELECTED": _blend_hex(accent, panel, 0.22),
        "CARD_HAIRLINE": tokens["border"],
    }


LEGACY_LIGHT_PALETTE = {
    "BG": "#edf2ef",
    "PANEL": "#ffffff",
    "PANEL_ALT": "#f8faf8",
    "PANEL_HOVER": "#eef3ef",
    "INK": "#17211c",
    "MUTED": "#647269",
    "TEXT_FAINT": "#8a9690",
    "LINE": "#d8e0da",
    "LINE_STRONG": "#b8c7bf",
    "PRIMARY": "#2b7c4b",
    "PRIMARY_HOVER": "#256d42",
    "PRIMARY_TEXT": "#ffffff",
    "GREEN": "#2b7c4b",
    "GREEN_SOFT": "#e0f3e7",
    "RED": "#b42318",
    "RED_SOFT": "#ffe5e3",
    "AMBER": "#9a5d00",
    "AMBER_SOFT": "#fff0cd",
    "BLUE": "#236f95",
    "BLUE_SOFT": "#e2f1f7",
    "DARK": "#1c2922",
    "ACCENT_GRAD_A": "#2b7c4b",
    "ACCENT_GRAD_B": "#256d42",
    "ACCENT_GRAD_C": "",
    "METER_BG": "#e8eee9",
    "CALENDAR_OUTSIDE": "#f7faf8",
    "CARD_SELECTED": "#f4fbf7",
    "CARD_HAIRLINE": "#e2e9e4",
}


MOCK_THEME_PALETTES = {"Light": LEGACY_LIGHT_PALETTE, **{name: _theme_palette(tokens) for name, tokens in DESIGN_THEME_TOKENS.items()}}
THEME_CHOICES = tuple(MOCK_THEME_PALETTES.keys())
THEME_ALIASES = {"light": "Light", "dark": "Midnight Slate"}


def normalize_theme_name(theme_name: object) -> str:
    text = str(theme_name or "").strip()
    return THEME_ALIASES.get(text.lower(), text if text in MOCK_THEME_PALETTES else "Midnight Slate")


def is_dark_theme(theme_name: object) -> bool:
    return normalize_theme_name(theme_name) != "Light"


PRIMARY = "#2995ff"
PRIMARY_HOVER = "#1d7bd6"
PRIMARY_TEXT = "#fcfcfc"
CARD_SELECTED = "#f4fbf7"
CARD_SELECTED_DARK = "#172c41"
CARD_HAIRLINE = "#e2e9e4"
CARD_HAIRLINE_DARK = "#334047"
CALENDAR_OUTSIDE = "#f7faf8"
CALENDAR_OUTSIDE_DARK = "#171c21"
CALENDAR_HEADER = "#f8faf8"
CALENDAR_HEADER_DARK = "#192127"
METER_BG = "#e8eee9"
METER_BG_DARK = "#2b353b"


def coding_palette(theme_name: str) -> dict[str, str]:
    if is_dark_theme(theme_name):
        theme = MOCK_THEME_PALETTES[normalize_theme_name(theme_name)]
        return {
            "bg": theme["BG"],
            "rail": theme["PANEL"],
            "panel": theme["PANEL_ALT"],
            "panel_alt": theme["PANEL"],
            "active": theme["PANEL_ALT"],
            "field": theme["PANEL_ALT"],
            "composer": theme["PANEL"],
            "ink": theme["INK"],
            "muted": theme["MUTED"],
            "faint": theme["LINE_STRONG"],
            "line": theme["LINE"],
            "line_strong": theme["LINE_STRONG"],
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
    theme = MOCK_THEME_PALETTES[normalize_theme_name(theme_name)]
    globals().update(theme)
    globals().update(
        {
            "CARD_SELECTED_DARK": theme["CARD_SELECTED"],
            "CARD_HAIRLINE_DARK": theme["CARD_HAIRLINE"],
            "CALENDAR_OUTSIDE_DARK": theme["CALENDAR_OUTSIDE"],
            "CALENDAR_HEADER_DARK": theme["PANEL_ALT"],
            "METER_BG_DARK": theme["METER_BG"],
        }
    )


def configure_windows_titlebar(window, theme_name: str) -> None:
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
        enabled = ctypes.c_int(1 if is_dark_theme(theme_name) else 0)
        if is_dark_theme(theme_name):
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
        _logger.debug("configure_windows_titlebar failed", exc_info=True)
        return


def load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {"theme": "Midnight Slate", "autoRefreshEnabled": True, "autoRefreshMinutes": 10, "sortMode": "Manual", "cardTemplate": "Balanced"}
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"theme": "Midnight Slate", "autoRefreshEnabled": True, "autoRefreshMinutes": 10, "sortMode": "Manual", "cardTemplate": "Balanced"}
    if not isinstance(raw, dict):
        return {"theme": "Midnight Slate", "autoRefreshEnabled": True, "autoRefreshMinutes": 10, "sortMode": "Manual", "cardTemplate": "Balanced"}
    raw["theme"] = normalize_theme_name(raw.get("theme", "Midnight Slate"))
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
    if number >= 1_000_000_000:
        amount = number / 1_000_000_000
        if amount >= 100:
            text = f"{amount:.0f}B"
        elif amount >= 10:
            text = f"{amount:.1f}B"
        else:
            text = f"{amount:.2f}B"
        return f"{sign}{text}"
    if number >= 1_000_000:
        amount = number / 1_000_000
        if amount >= 100:
            text = f"{amount:.0f}M"
        elif amount >= 10:
            text = f"{amount:.1f}M"
        else:
            text = f"{amount:.1f}M"
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


def claude_profile_type(profile: dict) -> str:
    """Return whether a Claude profile owns Code CLI auth or Desktop state only."""
    if provider_key(profile) != "claude":
        return ""
    raw = str(profile.get("claudeProfileType") or "code").strip().lower().replace("_", "-")
    return "desktop" if raw in {"desktop", "desktop-only", "free"} else "code"


def claude_code_enabled(profile: dict) -> bool:
    return provider_key(profile) == "claude" and claude_profile_type(profile) == "code"


def claude_desktop_only(profile: dict) -> bool:
    # Used For Testing Claude Account Switching
    # Kept for existing machine-local fixtures and switch regression coverage;
    # the production Add/Edit UI does not offer this profile type.
    return provider_key(profile) == "claude" and claude_profile_type(profile) == "desktop"


def coding_capable(profile: dict) -> bool:
    """Whether the profile can be handed to a native coding harness."""
    return not claude_desktop_only(profile)


def provider_label(profile: dict) -> str:
    provider = provider_key(profile)
    if provider == "claude" and claude_desktop_only(profile):
        return "Claude Desktop"
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
        if claude_desktop_only(profile):
            captured = bool(profile.get("claudeDesktopCaptured"))
            return {
                "label": "Desktop session" if captured else "Desktop login needed",
                "state": "ready" if captured else "login",
                "detail": (
                    "Claude Desktop session captured. Claude Code CLI, coding transport, "
                    "limits, and usage probes are unavailable for this Desktop-only profile."
                    if captured else
                    "Use Desktop Login once. AI Account Hub will bind and save the resulting "
                    "Claude Desktop identity without requiring Claude Code."
                ),
            }
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
    # Pin the on-disk profile ("Default") so seeded cookies + the persisted login
    # always land in and load from the same place, and quiet the first-run/default
    # -browser prompts that otherwise interrupt the dedicated blank-canvas window.
    return [
        browser_path,
        f"--user-data-dir={profile_dir}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
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
    # A real *session* cookie, not merely any consent/analytics cookie on the
    # domain (which was falsely reading as "logged in").
    return "Web login saved" if browser_profile_has_session_cookie(profile) else "Web login needed"


# Per-provider web *session* cookie: (domain root, cookie names that mean
# "signed in"). Used both to report login state truthfully and to decide whether
# a dedicated profile still needs a login.
WEB_SESSION_COOKIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "claude": ("claude.ai", ("sessionKey", "sessionKeyLC")),
    "codex": ("chatgpt.com", ("__Secure-next-auth.session-token", "__Secure-next-auth.session-token.0")),
    "cursor": ("cursor.com", ("WorkosCursorSessionToken",)),
}


def browser_profile_has_session_cookie(profile: dict) -> bool:
    """True only when the dedicated profile holds an actual auth/session cookie
    for the provider — not just a consent/analytics cookie."""
    spec = WEB_SESSION_COOKIES.get(provider_key(profile))
    if spec is None:
        # Unknown provider (e.g. antigravity/Google SSO): best we can do is treat
        # any cookie on the primary link domain as a signed-in signal.
        links = online_links_for_profile(profile)
        return any(browser_profile_has_cookie_for_url(profile, str(link.get("url") or "")) for link in links[:1])
    root_domain, names = spec
    placeholders = ",".join("?" for _ in names)
    for db_path in browser_profile_cookie_db_paths(profile):
        if not db_path.exists():
            continue
        try:
            connection = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True, timeout=1)
            try:
                row = connection.execute(
                    f"select 1 from cookies where host_key like ? and name in ({placeholders}) limit 1",
                    (f"%{root_domain}", *names),
                ).fetchone()
            finally:
                connection.close()
            if row:
                return True
        except sqlite3.Error:
            continue
    return False


def desktop_cookie_source(profile: dict) -> dict | None:
    """The provider desktop app's Chromium cookie store that could seed the web
    login, or None. Only Claude Desktop reliably keeps a web session cookie
    (claude.ai sessionKey); Codex/ChatGPT and Antigravity have no local web
    cookie to reuse (CLI auth is an API token / Google SSO, not a web session)."""
    if provider_key(profile) == "claude":
        cookies = CLAUDE_ROAMING_HOME / "Network" / "Cookies"
        local_state = CLAUDE_ROAMING_HOME / "Local State"
        if cookies.exists() and local_state.exists():
            return {"cookies": cookies, "local_state": local_state, "app": "Claude Desktop"}
    return None


def _shared_read_copy(src: Path, dst: Path) -> bool:
    """Copy a file even if another process holds a normal lock. Returns False if
    the source is opened deny-all (e.g. the desktop app is running and locks its
    live cookie DB) so the caller can fall back gracefully."""
    try:
        shutil.copy2(src, dst)
        return True
    except OSError:
        pass
    if os.name != "nt":
        return False
    try:
        import ctypes
        import ctypes.wintypes as wt

        GENERIC_READ = 0x80000000
        FILE_SHARE_ALL = 0x1 | 0x2 | 0x4
        OPEN_EXISTING = 3
        kernel32 = ctypes.windll.kernel32
        create = kernel32.CreateFileW
        create.restype = wt.HANDLE
        create.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
        handle = create(str(src), GENERIC_READ, FILE_SHARE_ALL, None, OPEN_EXISTING, 0x80, None)
        if not handle or handle == wt.HANDLE(-1).value:
            return False
        try:
            buf = ctypes.create_string_buffer(1 << 16)
            nread = wt.DWORD()
            with open(dst, "wb") as out:
                while kernel32.ReadFile(handle, buf, len(buf), ctypes.byref(nread), None) and nread.value:
                    out.write(buf.raw[: nread.value])
            return True
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        _logger.debug("shared-read copy failed", exc_info=True)
        return False


def seed_browser_profile_from_desktop(profile: dict) -> str:
    """Best-effort: copy the provider desktop app's cookie store + its encryption
    key into a *fresh* dedicated browser profile so the web dashboard opens
    already signed in with the same login the desktop app uses. Safe — worst case
    it leaves the profile as-is (a normal manual sign-in). Returns a short status
    string ('' when nothing to do)."""
    source = desktop_cookie_source(profile)
    if source is None:
        return ""
    if browser_profile_has_session_cookie(profile):
        return ""  # already signed in in the dedicated profile; never clobber it
    app_name = source.get("app") or f"{provider_label(profile)} Desktop"
    label = provider_label(profile)
    try:
        os_crypt = json.loads(source["local_state"].read_text(encoding="utf-8-sig")).get("os_crypt")
    except (OSError, json.JSONDecodeError):
        os_crypt = None
    if not os_crypt:
        return ""
    profile_dir = browser_profile_dir_for_profile(profile)
    network_dir = profile_dir / "Default" / "Network"
    try:
        network_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return f"Could not prepare browser profile: {error}"
    # The live cookie DB is locked deny-all while the desktop app runs.
    if not _shared_read_copy(source["cookies"], network_dir / "Cookies"):
        return f"Close {app_name} once so its saved login can be imported into the browser, then click Online again."
    # Give Chrome exactly the key it needs (only the os_crypt section) so it can
    # decrypt the copied cookies; a full Local State copy could confuse Chrome.
    try:
        (profile_dir / "Local State").write_text(json.dumps({"os_crypt": os_crypt}), encoding="utf-8")
    except OSError as error:
        return f"Could not write browser encryption key: {error}"
    return f"Imported the {app_name} login into this account's browser profile."


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
        "subscriptionType": (
            payload.get("subscriptionType")
            or payload.get("planType")
            or payload.get("plan")
            or payload.get("tier")
            or payload.get("subscription")
            or payload.get("subscription_tier")
            or payload.get("membershipStatus")
            or ""
        ),
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


def clip_middle_text(value: object, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if max_chars <= 1 or len(text) <= max_chars:
        return text
    if max_chars <= 4:
        return text[:max_chars]
    available = max_chars - 3
    left = max(1, available // 2)
    right = max(1, available - left)
    return f"{text[:left].rstrip()}...{text[-right:].lstrip()}"


def calendar_reset_chip_label(marker: object) -> str:
    text = str(marker or "").strip()
    estimated = "estimate" in text.lower()
    account = re.split(r"\s+weekly\s+reset", text, maxsplit=1, flags=re.IGNORECASE)[0]
    account = re.sub(r"\b(claude\s+code|antigravity|codex|cursor)\b", "", account, flags=re.IGNORECASE)
    account = re.sub(r"\baccount\b", "", account, flags=re.IGNORECASE)
    account = re.sub(r"\s+", " ", account).strip()
    if not account:
        lowered = text.lower()
        account = next(
            (
                label
                for key, label in (
                    ("claude", "Claude"),
                    ("codex", "Codex"),
                    ("cursor", "Cursor"),
                    ("antigravity", "Antigravity"),
                )
                if key in lowered
            ),
            "Account",
        )
    suffix = "reset estimate" if estimated else "reset"
    return f"{clip_text(account, 12)} {suffix}"


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
    "claude": [("Default", ""), ("Claude Opus 4.5", "opus"), ("Claude Sonnet 4.5", "sonnet"), ("Claude Haiku 4.5", "haiku")],
    "cursor": [("Default", ""), ("Cursor Small", "cursor-small"), ("Cursor Large", "cursor-large")],
    "antigravity": [("Default", ""), ("Gemini 2.5 Pro", "gemini-2.5-pro"), ("Gemini 2.5 Flash", "gemini-2.5-flash")],
}

CODING_ACCESS_OPTIONS = {
    "codex": [("Workspace", "workspace"), ("Read only", "read-only"), ("Full access", "full-access")],
    "claude": [
        ("Accept edits", "accept-edits"),
        ("Plan mode", "plan"),
        ("Manual approval", "default"),
        ("Full access", "full-access"),
    ],
    "cursor": [("Agent", "default"), ("Ask", "ask"), ("Manual edit", "plan"), ("Auto-run", "full-access")],
    "antigravity": [("Manual", "default"), ("Supervised", "sandbox"), ("Autonomous", "full-access")],
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
    if claude_desktop_only(profile):
        explicit = display_plan_text(profile.get("accountPlan"))
        return explicit or "Desktop only"
    summary = profile.get("usageSummary") if isinstance(profile.get("usageSummary"), dict) else {}
    for key in (
        "accountPlan", "planType", "plan", "membershipType", "subscriptionTier", "subscriptionType",
        "tier", "subscription", "subscription_tier", "membershipStatus",
    ):
        text = display_plan_text(profile.get(key) or summary.get(key))
        if text:
            status = display_plan_text(profile.get("accountPlanStatus") or summary.get("accountPlanStatus") or summary.get("subscriptionStatus"))
            if status and status.lower() not in text.lower():
                return f"{text} ({status})"
            return text
    # No provider exposed a plan/tier field. `accountType`/`authMethod` at
    # least say HOW the account is billed (subscription vs API key), which
    # providers like Claude Code do reliably expose even when they don't
    # expose which plan tier the user is on. Say that plainly instead of
    # printing a raw, easily-misread auth-method string next to nothing.
    auth_method = display_plan_text(profile.get("accountType") or summary.get("accountType") or summary.get("authMethod"))
    if not auth_method:
        return "Plan not exposed"
    lowered = auth_method.lower().replace(" ", "")
    if "oauth" in lowered or "subscription" in lowered:
        return "Subscription (plan tier not reported by CLI)"
    if "apikey" in lowered or "console" in lowered:
        return "API key billing"
    return f"{auth_method} (plan tier not reported by CLI)"


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
        if claude_desktop_only(profile):
            if last_error:
                return "login" if "login" in last_error.lower() or "captur" in last_error.lower() else "error"
            return "ready" if bool(profile.get("claudeDesktopCaptured")) else "login"
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
        return "" if text in {"-", "now"} else text

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
        normalized["claudeProfileType"] = claude_profile_type(normalized)
        if claude_desktop_only(normalized):
            # Desktop-only profiles represent free Claude accounts. Paid
            # subscriptions belong to the Claude Code profile type.
            normalized["accountPlan"] = "Free"
        normalized["claudeDesktopAccountUuid"] = str(normalized.get("claudeDesktopAccountUuid") or "").strip()
        normalized["claudeDesktopCaptured"] = bool(normalized.get("claudeDesktopCaptured"))
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

    # Each refresh appends a NEW cumulative snapshot for the same day (the bucket
    # hash changes as the day's tokens grow), so a single day can have many rows.
    # Keep only the largest snapshot per (profile, day, source) — summing every
    # snapshot over-counts tokens and minutes massively (e.g. "201h active/day").
    best: dict[tuple, dict] = {}
    for row in rows:
        pid = str(row["profile_id"])
        if pid not in allowed:
            continue
        profile = profiles_by_id.get(pid) or {"id": pid, "name": row["profile_name"], "provider": row["provider"]}
        try:
            bucket = json.loads(str(row["bucket_json"] or "{}"))
        except json.JSONDecodeError:
            bucket = {}
        entry = {
            "profileId": pid,
            "profile": profile,
            "day": str(row["bucket_day"] or ""),
            "tokens": int(row["tokens"] or 0),
            "minutes": None if row["active_minutes"] is None else int(row["active_minutes"]),
            "messageCount": None if row["message_count"] is None else int(row["message_count"]),
            "source": str(row["source"] or ""),
            "bucket": bucket,
        }
        key = (pid, entry["day"], entry["source"])
        current = best.get(key)
        if current is None or entry["tokens"] >= current["tokens"]:
            best[key] = entry
    return list(best.values())


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
        _logger.debug("get_appx_install_location failed for %s", package_name, exc_info=True)
        return ""
    return ""


def normalize_antigravity_print_timeout(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "5m"
    if re.fullmatch(r"\d{1,4}", text):
        return f"{text}s"
    if re.fullmatch(r"\d{1,4}(ms|s|m|h)", text):
        return text
    return "5m"


def icon_cache_path(asset_name: str) -> Path:
    ICON_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    return ICON_CACHE_ROOT / asset_name


def configured_icon_path(provider: str) -> str:
    names = [
        f"AI_HUB_{provider.upper()}_ICON_PATH",
        f"{provider.upper()}_ICON_PATH",
    ]
    for name in names:
        raw = os.environ.get(name, "").strip()
        if raw and Path(raw).is_file():
            return str(Path(raw).resolve())
    return ""


def bundled_icon_path(asset_name: str) -> str:
    candidate = SCRIPT_DIR / "assets" / asset_name
    return str(candidate) if candidate.is_file() else ""


def cached_provider_icon_path(provider: str, asset_name: str) -> str:
    configured = configured_icon_path(provider)
    if configured:
        return configured
    cached = ICON_CACHE_ROOT / asset_name
    if cached.is_file():
        return str(cached)
    return bundled_icon_path(asset_name)


def png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as stream:
            header = stream.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def claude_mark_candidates(package: Path) -> list[Path]:
    asset_root = package / "app" / "resources" / "ion-dist"
    named = [
        asset_root / "images" / "claude_code_icon.png",
        asset_root / "images" / "claude_logo.png",
        asset_root / "images" / "claude-mark.png",
        asset_root / "assets" / "v1" / "ce67964e7-CAX1bqSh.png",
    ]
    matches = [path for path in named if path.is_file()]
    hashed_root = asset_root / "assets" / "v1"
    if hashed_root.is_dir():
        small_square: list[tuple[int, Path]] = []
        preferred_sizes = {32: 0, 48: 1, 64: 2, 24: 3, 16: 4}
        for path in hashed_root.glob("*.png"):
            dimensions = png_dimensions(path)
            if dimensions is None or dimensions[0] != dimensions[1]:
                continue
            size = dimensions[0]
            if size not in preferred_sizes or path.stat().st_size > 20_000:
                continue
            small_square.append((preferred_sizes[size], path))
        matches.extend(path for _score, path in sorted(small_square, key=lambda item: (item[0], item[1].name)))
    seen: set[str] = set()
    unique: list[Path] = []
    for path in matches:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def locate_codex_icon_path() -> str:
    cached = cached_provider_icon_path("codex", "codex-icon.png")
    if cached:
        return cached
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
            local_icon = icon_cache_path("codex-icon.png")
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
    configured = configured_icon_path("claude")
    if configured:
        return configured
    cached = ICON_CACHE_ROOT / "claude-mark.png"
    if cached.is_file():
        return str(cached)
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
        candidates.extend(claude_mark_candidates(package))
    for candidate in candidates:
        if candidate.exists():
            local_icon = icon_cache_path("claude-mark.png")
            try:
                if not local_icon.exists() or local_icon.stat().st_size != candidate.stat().st_size:
                    shutil.copy2(candidate, local_icon)
                return str(local_icon)
            except OSError:
                return str(candidate)
    return ""


def cache_asset_from_candidate(candidate: Path, asset_name: str) -> str:
    local_icon = icon_cache_path(asset_name)
    try:
        if not local_icon.exists() or local_icon.stat().st_size != candidate.stat().st_size:
            shutil.copy2(candidate, local_icon)
        return str(local_icon)
    except OSError:
        return str(candidate)


def extract_associated_icon_png(exe_path: Path, asset_name: str) -> str:
    if not exe_path.exists():
        return ""
    local_icon = icon_cache_path(asset_name)
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
        _logger.debug("extract_associated_icon_png failed for %s", exe_path, exc_info=True)
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
    cached = cached_provider_icon_path("cursor", "cursor-icon.png")
    if cached:
        return cached
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


def antigravity_cli_diagnostics(path: str, timeout: float = 4) -> dict:
    raw_path = str(path or "").strip()
    if not raw_path:
        return {
            "state": "missing",
            "ready": False,
            "label": "CLI not found",
            "detail": "No standalone Antigravity CLI was found.",
            "path": "",
            "version": "",
        }
    candidate = Path(raw_path)
    if not candidate.exists():
        return {
            "state": "missing",
            "ready": False,
            "label": "CLI not found",
            "detail": f"Configured Antigravity CLI path does not exist: {raw_path}",
            "path": raw_path,
            "version": "",
        }
    if candidate.name.lower() == "agy-node.cmd":
        return {
            "state": "broken_shim",
            "ready": False,
            "label": "Broken shim found",
            "detail": "Only agy-node.cmd was found; it is not a healthy standalone agy command.",
            "path": str(candidate),
            "version": "",
        }
    try:
        process = subprocess.run(
            [str(candidate), "--version"],
            cwd=str(Path.cwd()),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return {
            "state": "errored",
            "ready": False,
            "label": "CLI found, probe timed out",
            "detail": "Antigravity CLI exists, but the version probe timed out.",
            "path": str(candidate),
            "version": "",
        }
    except Exception as error:
        return {
            "state": "errored",
            "ready": False,
            "label": "CLI found, probe errored",
            "detail": str(error),
            "path": str(candidate),
            "version": "",
        }
    output = (process.stdout.strip() or process.stderr.strip()).splitlines()
    version = output[0].strip() if output else ""
    if process.returncode != 0:
        detail = version or f"Version probe exited with code {process.returncode}."
        return {
            "state": "errored",
            "ready": False,
            "label": "CLI found, probe errored",
            "detail": detail[:220],
            "path": str(candidate),
            "version": version,
            "exitCode": process.returncode,
        }
    return {
        "state": "ready",
        "ready": True,
        "label": "CLI found",
        "detail": "Standalone Antigravity CLI is available.",
        "path": str(candidate),
        "version": version,
        "exitCode": process.returncode,
    }


def antigravity_cli_label(probe: dict | None) -> str:
    if not isinstance(probe, dict):
        return "CLI not found"
    return str(probe.get("label") or "CLI not found")


def locate_antigravity_icon_path() -> str:
    cached = cached_provider_icon_path("antigravity", "antigravity-icon.png")
    if cached:
        return cached
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
        "hasAccountUuid": False,
        "hasSessionCookie": False,
        "hasLoggedInSignal": False,
        "sessionExpires": "",
        "ready": False,
        "summary": "Claude Desktop login not detected.",
    }

    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8-sig"))
            status["hasOAuthCache"] = bool(data.get("oauth:tokenCache") or data.get("oauth:tokenCacheV2"))
            status["hasAccountUuid"] = bool(str(data.get("lastKnownAccountUuid") or "").strip())
        except (OSError, json.JSONDecodeError):
            pass

    for cookie_db in (
        CLAUDE_ROAMING_HOME / "Network" / "Cookies",
        CLAUDE_ROAMING_HOME / "Cookies",
        CLAUDE_ROAMING_HOME / "Default" / "Network" / "Cookies",
        CLAUDE_ROAMING_HOME / "Default" / "Cookies",
    ):
        if not cookie_db.exists():
            continue
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
                break
        except Exception:
            _logger.debug("Claude Desktop cookie DB read failed", exc_info=True)

    log_path = CLAUDE_ROAMING_HOME / "logs" / "main.log"
    if log_path.exists():
        try:
            tail = log_path.read_text(encoding="utf-8", errors="ignore")[-250_000:]
            status["hasLoggedInSignal"] = "claude.ai account active and logged in" in tail
        except OSError:
            status["hasLoggedInSignal"] = False

    status["ready"] = bool(
        status["desktopInstalled"]
        and status["hasOAuthCache"]
        and status["hasAccountUuid"]
        and (status["hasSessionCookie"] or status["hasLoggedInSignal"])
    )
    if status["ready"]:
        bits = []
        if status["hasOAuthCache"]:
            bits.append("OAuth cache")
        if status["hasAccountUuid"]:
            bits.append("account identity")
        if status["hasSessionCookie"]:
            expiry = local_datetime_label(status["sessionExpires"]) if status["sessionExpires"] else "unknown expiry"
            bits.append(f"session cookie expires {expiry}")
        elif status["hasLoggedInSignal"]:
            bits.append("running app reports logged in")
        status["summary"] = "Claude Desktop login metadata found: " + "; ".join(bits)
    elif status["hasOAuthCache"] and status["hasAccountUuid"]:
        status["summary"] = (
            "Claude Desktop account metadata found, but no Desktop session cookie was found. "
            "The app may still be on the login screen."
        )
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
        "weeklyModelUsedPercent": {},
        "summary": str(text or "").strip(),
    }
    weekly_candidates: list[dict] = []
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
            continue

        weekly_match = re.match(
            r"current week(?:\s*\((?P<label>[^)]*)\))?\s*:\s*(?P<body>.*)$",
            line,
            re.IGNORECASE,
        )
        if not weekly_match:
            continue
        label = str(weekly_match.group("label") or "").strip()
        body = str(weekly_match.group("body") or "")
        used_match = re.search(r"(\d+(?:\.\d+)?)%\s+used", body, re.IGNORECASE)
        reset_match = re.search(r"\bresets\s+(.+)$", body, re.IGNORECASE)
        used_percent = float(used_match.group(1)) if used_match else None
        reset_utc = parse_claude_reset_label(reset_match.group(1)) if reset_match else ""
        normalized_label = re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()
        is_aggregate = not label or normalized_label in {"all", "all model", "all models"}
        weekly_candidates.append(
            {
                "label": label,
                "usedPercent": used_percent,
                "resetUtc": reset_utc,
                "isAggregate": is_aggregate,
            }
        )
        if label and not is_aggregate and used_percent is not None:
            result["weeklyModelUsedPercent"][label] = used_percent

    if weekly_candidates:
        # Claude Code 2.1.197 added model-specific rows such as
        # "Current week (Fable)". Prefer the all-model row so a later
        # model-specific percentage cannot overwrite the account total.
        aggregate = next((item for item in weekly_candidates if item["isAggregate"]), None)
        if aggregate is None:
            aggregate = next((item for item in weekly_candidates if item["resetUtc"]), weekly_candidates[0])
        result["weeklyUsedPercent"] = aggregate["usedPercent"]
        result["weeklyResetUtc"] = aggregate["resetUtc"]
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
        _logger.debug("windows_file_version failed for %s", target, exc_info=True)
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


def estimate_weekly_reset_from_bucket_days(day_strings: list) -> str:
    """Estimate a weekly reset as (earliest used day within the current 7-day
    window) + 7 days, as an ISO-UTC datetime. Empty string if no recent usage."""
    today = dt.datetime.now(dt.timezone.utc).date()
    cutoff = today - dt.timedelta(days=6)
    used: list[dt.date] = []
    for text in day_strings:
        try:
            day = dt.date.fromisoformat(str(text))
        except (ValueError, TypeError):
            continue
        if cutoff <= day <= today:
            used.append(day)
    if not used:
        return ""
    estimate = min(used) + dt.timedelta(days=7)
    return dt.datetime.combine(estimate, dt.time(), tzinfo=dt.timezone.utc).isoformat()


def resolve_claude_weekly_reset(probe_reset: str, daily_buckets: list, stored_estimate: str) -> tuple[str, str]:
    """Decide the weekly reset to store on each Claude refresh so it never keeps
    a stale saved date. Priority: the value the CLI reported → an estimate from
    recent usage → drop a stored estimate that has already elapsed.
    Returns (iso_reset_or_empty, source)."""
    if str(probe_reset or "").strip():
        return str(probe_reset), "claude-usage"
    day_strings = [b.get("date") for b in daily_buckets if isinstance(b, dict)]
    estimate = estimate_weekly_reset_from_bucket_days(day_strings)
    if estimate:
        return estimate, "claude-buckets"
    stored = parse_iso_datetime(stored_estimate)
    if stored is not None and stored < dt.datetime.now(dt.timezone.utc):
        return "", "stale-cleared"
    return str(stored_estimate or ""), "stored"


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
    # Refresh the account type whenever the probe reports one, independent of
    # planType, so it isn't left on an old saved value.
    if rate_limits.get("limitName") or rate_limits.get("limitId"):
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
