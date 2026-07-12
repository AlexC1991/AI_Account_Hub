"""Data layer for the Qt UI: profiles, provider metadata, usage/limits.

Deliberately Tk-free. Bridges the UI to the shared backend
(:mod:`ai_account_hub.core`) and reads the established machine-local
profiles.json. No provider auth token is read or stored here.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from ai_account_hub import core as L
from ai_account_hub import demo_data  # AI_HUB_DEMO=1: fake accounts for screenshots
from ai_account_hub.ui.tokens import PROVIDER_COLORS, PROVIDER_LETTERS, PROVIDER_LABELS, THEMES

# Demo mode (AI_HUB_DEMO=1) shows fake sample accounts so the UI can be shown or
# screenshotted without exposing real data. Redirect the shared usage-history
# reader to the demo generator so the calendar and stat cards match the fake
# profiles. Profile loading and every write path are additionally guarded in the
# functions below, so the real profiles.json is never read or overwritten.
if demo_data.DEMO:
    L.history_usage_entries = demo_data.demo_history_entries

LAUNCHER_ROOT = Path(
    os.environ.get("AI_HUB_LAUNCHER_ROOT", str(Path.home() / ".codex-account-launcher"))
).expanduser()
PROFILES_FILE = LAUNCHER_ROOT / "profiles.json"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_PROVIDER_ICON_CACHE: dict[str, str] = {}


def _num(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def provider_key(profile: dict) -> str:
    key = str(profile.get("provider") or "codex").strip().lower()
    return key if key in PROVIDER_COLORS else "codex"


def provider_label(profile: dict) -> str:
    return L.provider_label(profile)


def provider_color(profile: dict) -> str:
    return PROVIDER_COLORS.get(provider_key(profile), PROVIDER_COLORS["codex"])


def provider_monogram(profile: dict) -> str:
    return PROVIDER_LETTERS.get(provider_key(profile), "CX")


def provider_icon_path(profile: dict) -> str:
    provider = provider_key(profile)
    cached = _PROVIDER_ICON_CACHE.get(provider)
    if cached and Path(cached).is_file():
        return cached
    locators = {
        "codex": ("codex-icon.png", getattr(L, "locate_codex_icon_path", None)),
        "claude": ("claude-icon.png", getattr(L, "locate_claude_icon_path", None)),
        "cursor": ("cursor-icon.png", getattr(L, "locate_cursor_icon_path", None)),
        "antigravity": ("antigravity-icon.png", getattr(L, "locate_antigravity_icon_path", None)),
    }
    asset_name, locator = locators.get(provider, ("", None))
    candidates: list[Path] = []
    if callable(locator):
        try:
            located = str(locator() or "").strip()
            if located:
                candidates.append(Path(located))
        except Exception:
            pass
    if asset_name:
        candidates.append(ASSETS_DIR / asset_name)
        candidates.append(L.ICON_CACHE_ROOT / asset_name)
    for candidate in candidates:
        if candidate.is_file():
            value = str(candidate)
            _PROVIDER_ICON_CACHE[provider] = value
            return value
    return ""


def profile_id(profile: dict) -> str:
    return str(profile.get("id") or f"{provider_key(profile)}:{profile.get('name', '')}")


def account_plan(profile: dict) -> str:
    # Reuse the real, tested plan-label logic from the legacy backend.
    return L.account_plan_label(profile)


def percent_left(used_percent: object) -> float | None:
    return L.percent_left(used_percent)


def account_state(profile: dict) -> str:
    if demo_data.DEMO:
        return str(profile.get("state") or "ready")
    # Reuse the real, tested state machine (ready/not_ready/error/login/idle).
    return L.effective_state(profile)


def claude_desktop_only(profile: dict) -> bool:
    return L.claude_desktop_only(profile)


STATE_PILL = {"ready": "ready", "not_ready": "error", "error": "error", "login": "warn", "idle": "idle"}
STATE_LABEL = {"ready": "Ready", "not_ready": "Not ready", "error": "Error", "login": "Login", "idle": "Idle"}


def load_profiles() -> list[dict]:
    if demo_data.DEMO:
        return demo_data.demo_profiles()
    return list(L.load_profiles())


def load_settings() -> dict:
    settings = dict(L.load_settings())
    # The shared backend's load_settings() runs a legacy theme normalizer that
    # only knows its own palette set and silently resets any Qt-only theme it
    # doesn't recognise (e.g. "Black & White") back to the default. Re-read the
    # raw saved value and keep it whenever it is a valid Qt theme, so the theme
    # the user picked actually persists across launches.
    try:
        raw = json.loads(Path(L.SETTINGS_FILE).read_text(encoding="utf-8-sig"))
        saved_theme = str(raw.get("theme") or "").strip()
        if saved_theme in THEMES:
            settings["theme"] = saved_theme
    except Exception:
        pass
    # Community sharing is explicit opt-in. These defaults live in the UI data
    # layer so older settings files gain the new keys without a migration write.
    settings.setdefault("communitySharingEnabled", False)
    settings.setdefault("communityConsentVersion", 0)
    settings.setdefault("communityApiMode", "cloudflare-staging")
    settings.setdefault("communityLastUploadUtc", "")
    settings.setdefault("communityLastReceipt", {})
    if demo_data.DEMO:
        settings["autoRefreshEnabled"] = False  # never overwrite demo data on a timer
        settings["communitySharingEnabled"] = False
    return settings


def save_settings(settings: dict) -> None:
    if demo_data.DEMO:
        return
    L.save_settings(dict(settings))


def format_tokens(millions: float | int | None) -> str:
    """Design token rule: values are in millions; switch to billions past 1000M."""
    if millions is None:
        return "-"
    try:
        m = float(millions)
    except (TypeError, ValueError):
        return "-"
    if m >= 1000:
        b = m / 1000.0
        return f"{b:.2f}B" if b < 100 else f"{b:.0f}B"
    if m >= 1:
        return f"{m:.0f}M"
    return f"{m:.2f}M"


def compact_number(value: object) -> str:
    """Reuse the legacy token/number formatter (M -> B rule)."""
    return L.compact_number(_num(value))


_ENGINE = None


def engine():
    """Lazily-constructed shared backend engine (does provider discovery once)."""
    global _ENGINE
    if _ENGINE is None:
        from ai_account_hub.engine import HubEngine
        _ENGINE = HubEngine()
    return _ENGINE


def refresh_one(profile: dict, reason: str = "manual") -> dict:
    """Refresh a single profile's limits/usage via the real backend. Blocking;
    call from a worker thread, not the UI thread."""
    try:
        result = engine().refresh_profile(profile)
    except Exception as error:  # mirror the legacy per-account error handling
        profile["lastLimitsRefreshUtc"] = L.iso_utc_now()
        profile["lastLimitsError"] = str(error)
        result = {"ok": False, "error": str(error)}
    engine().record_history(profile, reason)
    return result


def save_profiles(profiles: list[dict]) -> None:
    if demo_data.DEMO:
        return  # demo mode never writes to the real profiles.json
    engine().save(profiles)


def discover_tools() -> dict:
    """Provider discovery via the shared, Tk-free scanner. Never fatal."""
    try:
        from ai_account_hub.core import provider_discovery  # noqa: PLC0415

        return provider_discovery.discover_provider_tools()
    except Exception:  # pragma: no cover - discovery must never block the UI
        return {"providers": {}, "support": {}}
