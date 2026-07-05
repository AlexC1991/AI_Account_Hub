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
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 uses the tomli backport.
    import tomli as tomllib

MODULE_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = MODULE_DIR.parent  # the ai_account_hub package root
CLAUDE_PERMISSION_BRIDGE_PATH = PACKAGE_DIR / "harness" / "claude_permission_bridge.py"

from .provider_discovery import (  # noqa: E402
    default_report_path,
    discover_provider_tools,
    load_fresh_report,
    write_discovery_report,
)

_logger = logging.getLogger(__name__)

from ..harness.native_harness import (  # noqa: E402
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


from .palette import *  # noqa: F401,F403 (theme/palette domain)


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

SCRIPT_DIR = PACKAGE_DIR              # ai_account_hub/ package root (holds assets/)
REPO_ROOT = PACKAGE_DIR.parent        # repository root (holds scripts/, main.py)
HELPER_PATH = REPO_ROOT / "scripts" / "codex-account-limits-helper.mjs"
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


from .constants import *  # noqa: F401,F403 (provider/UI constant tables)

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


def browser_profile_mode(profile: dict) -> str:
    mode = str(profile.get("browserProfileMode") or "isolated").strip().lower()
    if mode in {"system", "default", "off", "disabled", "none"}:
        return "system"
    return "isolated"



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
    # First-run should be private and explicit: do not create placeholder Codex
    # accounts until the user adds or imports real profiles.
    return []


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


def locate_claude_desktop_path() -> str:
    from ai_account_hub.core.locators import get_appx_install_location  # avoid import cycle
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


def cached_claude_desktop_login_status(max_age_seconds: int = STATE_CACHE_SECONDS) -> dict:
    cached_at = _CLAUDE_STATUS_CACHE.get("at")
    cached_value = _CLAUDE_STATUS_CACHE.get("value")
    now = dt.datetime.now(dt.timezone.utc)
    if isinstance(cached_at, dt.datetime) and isinstance(cached_value, dict):
        if (now - cached_at).total_seconds() <= max_age_seconds:
            return cached_value
    from ai_account_hub.core.claude_status import claude_desktop_login_status  # avoid import cycle
    value = claude_desktop_login_status()
    _CLAUDE_STATUS_CACHE["at"] = now
    _CLAUDE_STATUS_CACHE["value"] = value
    return value


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
