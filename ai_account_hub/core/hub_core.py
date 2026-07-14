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

if sys.version_info >= (3, 11):
    import tomllib
else:  # Keep the backport out of Python 3.12 frozen builds.
    import importlib

    tomllib = importlib.import_module("tomli")

MODULE_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = MODULE_DIR.parent  # the ai_account_hub package root

from .provider_discovery import (  # noqa: E402
    default_report_path,
    discover_provider_tools,
    load_fresh_report,
    write_discovery_report,
)

_logger = logging.getLogger(__name__)


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
    "rateLimitDiagnostics": {},
    "lastRateLimitGuardUtc": "",
    "lastRateLimitGuardReason": "",
    "codexRolloverCandidates": {},
    "codexLimitVerificationState": "",
    "codexRolloverPollDueUtc": "",
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


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write ``text`` so ``path`` is never left partially written.

    Writes to a temp file in the same directory, flushes + fsyncs it, then
    atomically ``os.replace``s it onto the target. If the process dies mid-write
    only the temp file is affected; the existing file stays intact. This is what
    keeps a user's profiles.json from being truncated on a crash/power loss.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        with tmp.open("w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass  # already renamed away on success, or never created


def _preserve_corrupt_file(path: Path) -> None:
    """Move an unreadable file aside instead of silently destroying it.

    If profiles.json ever fails to parse we would otherwise overwrite it with an
    empty default, losing the user's accounts. Renaming it to a timestamped
    ``.corrupt-*`` sibling keeps their data recoverable.
    """
    try:
        if path.exists() and path.stat().st_size > 0:
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = path.with_name(f"{path.name}.corrupt-{stamp}")
            path.replace(backup)
            _logger.warning("Preserved unreadable %s as %s", path.name, backup.name)
    except OSError:
        _logger.debug("Could not preserve corrupt file %s", path, exc_info=True)


def load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {"theme": "Midnight Slate", "autoRefreshEnabled": True, "autoRefreshMinutes": 10, "sortMode": "Manual", "cardTemplate": "Balanced", "communitySharingEnabled": False, "communityConsentVersion": 0, "communityApiMode": "cloudflare-staging"}
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
    raw.setdefault("communitySharingEnabled", False)
    raw.setdefault("communityConsentVersion", 0)
    raw.setdefault("communityApiMode", "cloudflare-staging")
    if raw.get("sortMode") == "5h left":
        raw["sortMode"] = "Session left"
    if raw.get("cardTemplate") not in CARD_TEMPLATE_CHOICES:
        raw["cardTemplate"] = "Balanced"
    return raw


def save_settings(settings: dict) -> None:
    _atomic_write_text(SETTINGS_FILE, json.dumps(settings, indent=2))


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


def limit_window_exhausted(profile: dict, used_key: str, reset_key: str) -> bool:
    """Return true only while an exhausted provider window is still active."""

    if not is_limit_exhausted(profile.get(used_key)):
        return False
    reset = parse_iso_datetime(profile.get(reset_key))
    return reset is None or reset > dt.datetime.now(dt.timezone.utc)


def codex_limit_blocked(profile: dict) -> bool:
    """Interpret Codex's global reached flag against its two actual windows."""

    short_blocked = limit_window_exhausted(
        profile, "shortLimitUsedPercent", "shortLimitResetUtc"
    )
    weekly_blocked = limit_window_exhausted(
        profile, "weeklyLimitUsedPercent", "weeklyLimitResetUtc"
    )
    if short_blocked or weekly_blocked:
        return True

    limit_type = str(profile.get("limitReachedType") or "").strip().lower()
    now = dt.datetime.now(dt.timezone.utc)
    short_reset = parse_iso_datetime(profile.get("shortLimitResetUtc"))
    weekly_reset = parse_iso_datetime(
        profile.get("weeklyResetEstimateUtc") or profile.get("weeklyLimitResetUtc")
    )
    if "week" in limit_type:
        return weekly_reset is None or weekly_reset > now
    if any(token in limit_type for token in ("primary", "session", "short", "5h")):
        return short_reset is None or short_reset > now
    # A generic rate_limit_reached flag is not enough to keep an account blocked
    # after both measured windows say otherwise or their reset has elapsed.
    return False


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
                    "Claude Desktop session captured. Claude Code CLI, limits, and usage "
                    "probes are unavailable for this Desktop-only profile."
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
    if state in {"login", "checking"}:
        return AMBER, AMBER_SOFT
    return BLUE, BLUE_SOFT


def status_label(state: str) -> str:
    return {
        "ready": "Ready",
        "not_ready": "Not Ready",
        "error": "Error",
        "login": "Login",
        "checking": "Checking",
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
        # A Claude Code profile is usable through its own CLI login. Desktop
        # login state is separate and must not gate cards or background usage
        # polling when the optional Desktop application is closed.
        return "ready" if cli_ready else "login"
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
    if str(profile.get("codexLimitVerificationState") or "") == "pending":
        return "checking"
    if codex_limit_blocked(profile):
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

    weekly_blocked = (
        limit_window_exhausted(profile, "weeklyLimitUsedPercent", "weeklyLimitResetUtc")
        or "week" in limit_type
    )
    session_blocked = (
        limit_window_exhausted(profile, "shortLimitUsedPercent", "shortLimitResetUtc")
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
        # Don't silently destroy a file we can't parse — keep it recoverable.
        _preserve_corrupt_file(PROFILES_FILE)
        profiles = default_profiles()
        save_profiles(profiles)
        return profiles
    items = raw if isinstance(raw, list) else [raw]
    return [normalize_profile(item, index) for index, item in enumerate(items)]


def save_profiles(profiles: list[dict]) -> None:
    cleaned = [normalize_profile(profile, index) for index, profile in enumerate(profiles)]
    _atomic_write_text(PROFILES_FILE, json.dumps(cleaned, indent=2))


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

    # Trust the provider's actual weekly-window reset time whenever it reports one
    # — that is the authoritative rolling-window reset (Codex sends it on the
    # 10080-minute window). Only fall back to a usage-based estimate (earliest
    # used day in the current window + 7 days) when the probe returned no reset.
    if api_reset:
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
    return "", "none"


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


_CODEX_LIMIT_SNAPSHOT_FIELDS = (
    "limitReachedType",
    "shortLimitLabel",
    "shortLimitUsedPercent",
    "shortLimitResetUtc",
    "weeklyLimitLabel",
    "weeklyLimitUsedPercent",
    "weeklyLimitResetUtc",
    "weeklyResetEstimateUtc",
    "weeklyResetEstimateSource",
)
_CODEX_ROLLOVER_POLL_SECONDS = 2 * 60
_CODEX_ROLLOVER_CONFIRM_SECONDS = 8 * 60
_CODEX_ROLLOVER_MAX_HOLD_SECONDS = 20 * 60
_CODEX_ROLLOVER_MIN_OBSERVATION_SECONDS = 60
_CODEX_ROLLOVER_STABLE_RESET_SECONDS = 45
_CODEX_ROLLOVER_ROLLING_RESET_SECONDS = 75


def _clear_codex_rollover_verification(profile: dict) -> None:
    candidates = profile.get("codexRolloverCandidates")
    if isinstance(candidates, dict):
        candidates.clear()
    else:
        profile["codexRolloverCandidates"] = {}
    profile["codexLimitVerificationState"] = ""
    profile["codexRolloverPollDueUtc"] = ""


def _codex_clean_diagnostics(result: dict) -> bool:
    diagnostics = result.get("rateLimitDiagnostics")
    if not isinstance(diagnostics, dict):
        return False
    return (
        int(sanitize_float(diagnostics.get("sampleCount")) or 0) >= 3
        and int(sanitize_float(diagnostics.get("usableSamples")) or 0) >= 3
        and int(sanitize_float(diagnostics.get("blockedSamples")) or 0) == 0
        and not bool(diagnostics.get("selectedBlocked"))
        and not bool(diagnostics.get("disagreement"))
    )


def _codex_window_signature(window_name: str, incoming_window: object) -> str:
    if not isinstance(incoming_window, dict):
        return f"{window_name}:missing"
    duration = sanitize_float(incoming_window.get("windowDurationMins"))
    if duration is not None:
        return f"{window_name}:duration:{int(duration)}"
    label = str(incoming_window.get("label") or window_name).strip().lower()
    return f"{window_name}:label:{label}"


def _codex_phantom_short_window(profile: dict, rate_limits: dict) -> bool:
    """Recognize short-window data created by the old weekly fallback bug."""
    if rate_limits.get("shortWindow") is not None or not isinstance(rate_limits.get("weeklyWindow"), dict):
        return False
    short_label = str(profile.get("shortLimitLabel") or "").strip().lower()
    same_usage = str(profile.get("shortLimitUsedPercent") or "") == str(
        profile.get("weeklyLimitUsedPercent") or ""
    )
    same_reset = str(profile.get("shortLimitResetUtc") or "") == str(
        profile.get("weeklyLimitResetUtc") or ""
    )
    return short_label == "weekly" or (same_usage and same_reset)


def codex_rollover_poll_due(profile: dict, now: dt.datetime | None = None) -> bool:
    """Return whether a pending Codex rollover needs its targeted follow-up read."""
    if provider_key(profile) != "codex":
        return False
    if str(profile.get("codexLimitVerificationState") or "") != "pending":
        return False
    due = parse_iso_datetime(profile.get("codexRolloverPollDueUtc"))
    if due is None:
        return True
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return due <= current.astimezone(dt.timezone.utc)


def defer_codex_rollover_poll(profile: dict, now: dt.datetime | None = None) -> None:
    """Back off a failed pending verification so the UI timer cannot hot-loop."""
    if str(profile.get("codexLimitVerificationState") or "") != "pending":
        return
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    due = current.astimezone(dt.timezone.utc) + dt.timedelta(seconds=_CODEX_ROLLOVER_POLL_SECONDS)
    profile["codexRolloverPollDueUtc"] = due.isoformat()
    candidates = profile.get("codexRolloverCandidates")
    if isinstance(candidates, dict):
        for candidate in candidates.values():
            if isinstance(candidate, dict):
                candidate["nextPollUtc"] = due.isoformat()


def _codex_snapshot_guard_windows(profile: dict, result: dict) -> dict[str, dt.datetime]:
    """Return exhausted windows whose apparent early rollover needs confirmation.

    Codex app-server can return a newly initialized 0%-used window whose reset is
    recalculated as ``now + duration`` every time a process starts. A real active
    window instead develops a stable absolute reset anchor (and may accumulate
    usage). Persist observations across refresh processes so those shapes can be
    distinguished, while bounding the hold so a stale exhausted state cannot live
    forever. Explicit reset-credit results bypass verification entirely.
    """
    candidates = profile.get("codexRolloverCandidates")
    if not isinstance(candidates, dict):
        candidates = {}
    profile["codexRolloverCandidates"] = candidates

    if provider_key(profile) != "codex":
        return {}
    if str(result.get("resetOutcome") or "") == "reset":
        _clear_codex_rollover_verification(profile)
        return {}
    rate_limits = (
        result.get("rateLimits")
        if isinstance(result.get("rateLimits"), dict)
        else {}
    )
    if str(rate_limits.get("rateLimitReachedType") or "").strip():
        _clear_codex_rollover_verification(profile)
        return {}

    now = parse_iso_datetime(result.get("refreshedAtIso")) or dt.datetime.now(dt.timezone.utc)
    clean_observation = _codex_clean_diagnostics(result)
    guarded: dict[str, dt.datetime] = {}
    for window_name, used_key, reset_key, incoming_key in (
        ("short", "shortLimitUsedPercent", "shortLimitResetUtc", "shortWindow"),
        ("weekly", "weeklyLimitUsedPercent", "weeklyLimitResetUtc", "weeklyWindow"),
    ):
        reset = parse_iso_datetime(profile.get(reset_key))
        incoming_window = rate_limits.get(incoming_key)
        phantom_short = window_name == "short" and _codex_phantom_short_window(profile, rate_limits)
        if reset is None or reset <= now or phantom_short:
            candidates.pop(window_name, None)
            continue
        previous_used = sanitize_float(profile.get(used_key))
        incoming_used = (
            sanitize_float(incoming_window.get("usedPercent"))
            if isinstance(incoming_window, dict)
            else None
        )
        if not is_limit_exhausted(previous_used) or is_limit_exhausted(incoming_used):
            candidates.pop(window_name, None)
            continue

        incoming_reset = iso_from_value(
            incoming_window.get("resetsAtIso")
            if isinstance(incoming_window, dict)
            else None
        )
        incoming_reset_dt = parse_iso_datetime(incoming_reset)
        signature = _codex_window_signature(window_name, incoming_window)
        candidate = candidates.get(window_name)
        if not isinstance(candidate, dict) or candidate.get("windowSignature") != signature:
            candidate = {
                "status": "pending",
                "firstSeenUtc": now.isoformat(),
                "lastSeenUtc": now.isoformat(),
                "lastObservationUtc": now.isoformat(),
                "firstResetUtc": incoming_reset,
                "latestResetUtc": incoming_reset,
                "observations": 1,
                "cleanStreak": 1 if clean_observation else 0,
                "stableResetObservations": 0,
                "rollingResetObservations": 0,
                "usageAdvanced": False,
                "usedPercent": incoming_used,
                "windowSignature": signature,
            }
        else:
            last_observation = parse_iso_datetime(candidate.get("lastObservationUtc"))
            gap = (now - last_observation).total_seconds() if last_observation else None
            candidate["lastSeenUtc"] = now.isoformat()
            if gap is None or gap >= _CODEX_ROLLOVER_MIN_OBSERVATION_SECONDS:
                previous_reset = parse_iso_datetime(candidate.get("latestResetUtc"))
                if previous_reset is not None and incoming_reset_dt is not None:
                    reset_shift = abs((incoming_reset_dt - previous_reset).total_seconds())
                    if reset_shift <= _CODEX_ROLLOVER_STABLE_RESET_SECONDS:
                        candidate["stableResetObservations"] = int(candidate.get("stableResetObservations") or 0) + 1
                    elif gap is not None and abs(reset_shift - gap) <= _CODEX_ROLLOVER_ROLLING_RESET_SECONDS:
                        candidate["rollingResetObservations"] = int(candidate.get("rollingResetObservations") or 0) + 1
                previous_usage = sanitize_float(candidate.get("usedPercent"))
                if previous_usage is not None and incoming_used is not None and incoming_used > previous_usage + 0.5:
                    candidate["usageAdvanced"] = True
                candidate["observations"] = int(candidate.get("observations") or 1) + 1
                candidate["cleanStreak"] = (
                    int(candidate.get("cleanStreak") or 0) + 1 if clean_observation else 0
                )
                candidate["lastObservationUtc"] = now.isoformat()
                candidate["latestResetUtc"] = incoming_reset
                candidate["usedPercent"] = incoming_used

        first_seen = parse_iso_datetime(candidate.get("firstSeenUtc")) or now
        age = max(0.0, (now - first_seen).total_seconds())
        observations = int(candidate.get("observations") or 1)
        clean_streak = int(candidate.get("cleanStreak") or 0)
        stable_resets = int(candidate.get("stableResetObservations") or 0)
        confirmed = (
            age >= 2 * 60
            and observations >= 2
            and clean_streak >= 2
            and stable_resets >= 1
            and bool(candidate.get("usageAdvanced"))
        ) or (
            age >= _CODEX_ROLLOVER_CONFIRM_SECONDS
            and observations >= 3
            and clean_streak >= 3
            and stable_resets >= 2
        ) or (
            age >= _CODEX_ROLLOVER_MAX_HOLD_SECONDS
            and observations >= 5
            and clean_streak >= 3
        )
        if confirmed:
            candidates.pop(window_name, None)
            continue

        candidate["nextPollUtc"] = (now + dt.timedelta(seconds=_CODEX_ROLLOVER_POLL_SECONDS)).isoformat()
        candidate["maxHoldUtc"] = (first_seen + dt.timedelta(seconds=_CODEX_ROLLOVER_MAX_HOLD_SECONDS)).isoformat()
        candidates[window_name] = candidate
        guarded[window_name] = reset

    if guarded:
        profile["codexLimitVerificationState"] = "pending"
        due_values = [
            parse_iso_datetime(item.get("nextPollUtc"))
            for item in candidates.values()
            if isinstance(item, dict)
        ]
        due_values = [value for value in due_values if value is not None]
        profile["codexRolloverPollDueUtc"] = min(due_values).isoformat() if due_values else now.isoformat()
    else:
        profile["codexLimitVerificationState"] = ""
        profile["codexRolloverPollDueUtc"] = ""
    return guarded


def set_profile_limits_from_result(profile: dict, result: dict) -> None:
    profile["lastLimitsRefreshUtc"] = iso_utc_now()
    if not result.get("ok"):
        message = str(result.get("error") or "Unknown limits refresh error")
        if is_revoked_token_message(message) or is_not_logged_in_message(message):
            mark_auth_error(profile, message)
        else:
            profile["lastLimitsError"] = message
        defer_codex_rollover_poll(profile)
        return

    profile["lastLimitsError"] = ""
    rate_limits = result.get("rateLimits") or {}
    previous_limit_snapshot = {
        key: profile.get(key)
        for key in _CODEX_LIMIT_SNAPSHOT_FIELDS
    }
    guarded_windows = _codex_snapshot_guard_windows(profile, result)
    diagnostics = result.get("rateLimitDiagnostics")
    profile["rateLimitDiagnostics"] = (
        diagnostics if isinstance(diagnostics, dict) else {}
    )
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
    elif "shortWindow" in rate_limits and "short" not in guarded_windows:
        profile["shortLimitLabel"] = "5h"
        profile["shortLimitUsedPercent"] = ""
        profile["shortLimitResetUtc"] = ""

    weekly_window = rate_limits.get("weeklyWindow")
    if isinstance(weekly_window, dict):
        profile["weeklyLimitLabel"] = str(weekly_window.get("label") or "Weekly")
        profile["weeklyLimitUsedPercent"] = "" if weekly_window.get("usedPercent") is None else str(weekly_window.get("usedPercent"))
        profile["weeklyLimitResetUtc"] = iso_from_value(weekly_window.get("resetsAtIso"))
    elif "weeklyWindow" in rate_limits and "weekly" not in guarded_windows:
        profile["weeklyLimitLabel"] = "Weekly"
        profile["weeklyLimitUsedPercent"] = ""
        profile["weeklyLimitResetUtc"] = ""

    usage = result.get("usage") or {}
    profile["usageSummary"] = usage.get("summary") if isinstance(usage.get("summary"), dict) else {}
    buckets = usage.get("dailyUsageBuckets")
    if isinstance(buckets, list):
        profile["usageDailyBuckets"] = [bucket for bucket in buckets if isinstance(bucket, dict)]
    profile["lastUsageError"] = str(result.get("usageError") or "")

    estimate, source = get_weekly_reset_estimate(profile, result)
    profile["weeklyResetEstimateUtc"] = estimate
    profile["weeklyResetEstimateSource"] = source

    if guarded_windows:
        guarded_fields = {
            "short": (
                "shortLimitLabel", "shortLimitUsedPercent", "shortLimitResetUtc",
            ),
            "weekly": (
                "weeklyLimitLabel", "weeklyLimitUsedPercent", "weeklyLimitResetUtc",
                "weeklyResetEstimateUtc", "weeklyResetEstimateSource",
            ),
        }
        for window_name in guarded_windows:
            for key in guarded_fields[window_name]:
                profile[key] = previous_limit_snapshot[key]

        previous_type = str(previous_limit_snapshot.get("limitReachedType") or "")
        previous_type_lower = previous_type.lower()
        preserve_reached = (
            "short" in guarded_windows
            and (
                is_limit_exhausted(previous_limit_snapshot.get("shortLimitUsedPercent"))
                or any(token in previous_type_lower for token in ("primary", "session", "short", "5h"))
            )
        ) or (
            "weekly" in guarded_windows
            and (
                is_limit_exhausted(previous_limit_snapshot.get("weeklyLimitUsedPercent"))
                or "week" in previous_type_lower
            )
        )
        if preserve_reached:
            profile["limitReachedType"] = previous_type

        guard_until = max(guarded_windows.values())
        window_text = " and ".join(sorted(guarded_windows))
        reason = (
            f"Ignored an early Codex {window_text} rollover snapshot before "
            f"{guard_until.isoformat()}."
        )
        profile["lastRateLimitGuardUtc"] = iso_utc_now()
        profile["lastRateLimitGuardReason"] = reason
        result["rateLimitGuard"] = {
            "preservedPreviousSnapshot": True,
            "preservedWindows": sorted(guarded_windows),
            "untilUtc": guard_until.isoformat(),
        }
    else:
        profile["lastRateLimitGuardReason"] = ""
