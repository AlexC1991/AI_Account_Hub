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


def coding_capable(profile: dict) -> bool:
    return L.coding_capable(profile)


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
    if demo_data.DEMO:
        settings["autoRefreshEnabled"] = False  # never overwrite demo data on a timer
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


def native():
    """The Tk-free native_harness module (transports + history discovery)."""
    from ai_account_hub.harness import native_harness
    return native_harness


def _codex_account_id(home: object) -> str:
    """The logged-in account id for a Codex home, read from its auth.json (the
    id_token's subject). Only the id is extracted — never the tokens — purely to
    attribute conversations to the right account."""
    try:
        auth = json.loads((Path(str(home)) / "auth.json").read_text(encoding="utf-8-sig"))
    except Exception:
        return ""
    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    acct = str(auth.get("account_id") or tokens.get("account_id") or "").strip()
    if acct:
        return acct
    id_token = str(tokens.get("id_token") or auth.get("id_token") or "")
    parts = id_token.split(".")
    if len(parts) >= 2:
        try:
            payload = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            return str(claims.get("sub") or claims.get("account_id") or "").strip()
        except Exception:
            return ""
    return ""


def _codex_owns_default_home(profile: dict) -> bool:
    """Whether this account is the one currently signed into the shared default
    Codex home (~/.codex). Only that account surfaces the default home's
    conversations, so the same conversation doesn't appear under every managed
    account. If the owner can't be determined, keep including it (don't hide)."""
    default_id = _codex_account_id(L.DEFAULT_CODEX_HOME)
    if not default_id:
        return True
    return _codex_account_id(profile.get("codexHome")) == default_id


def project_threads(profile: dict) -> list[dict]:
    """Discover this profile's native threads for its workspace, reusing the
    same discovery the Tk app uses. Returns [] on any error."""
    nh = native()
    provider = provider_key(profile)
    workspace = Path(str(profile.get("workspace") or L.DEFAULT_WORKSPACE))
    threads: list[dict] = []
    try:
        if provider == "codex":
            home = Path(str(profile.get("codexHome") or L.DEFAULT_CODEX_HOME))
            # The shared default Codex home (~/.codex) holds the conversations of
            # whichever account is active in Codex Desktop. Only that account pulls
            # it in, so the same conversation doesn't show under every account.
            threads = nh.discover_codex_file_threads(
                home, None, limit=100, include_default=_codex_owns_default_home(profile)
            )
            # Match Codex Desktop's sidebar: hide archived threads and empty
            # (zero-token, never-really-run) probe/aborted sessions. Those empty
            # sessions are why one project showed several throwaway entries.
            threads = [
                t for t in threads
                if not t.get("archived")
                and not (t.get("hasState") and int(t.get("tokensUsed") or 0) <= 0)
            ]
        elif provider == "claude":
            root = L.claude_profile_home(profile) / "projects"
            threads = nh.discover_claude_threads(root, None, limit=100)
        elif provider == "cursor":
            # All Cursor projects, not just the profile's one workspace.
            threads = nh.discover_all_cursor_threads(L.CURSOR_HOME, limit=200)
        elif provider == "antigravity":
            threads = nh.discover_all_antigravity_threads(nh.antigravity_cli_home(), limit=100)
    except Exception:
        threads = []

    seen = {str(item.get("id") or "") for item in threads if item.get("id")}
    for ref in nh.load_thread_refs(L.NATIVE_THREADS_FILE):
        if str(ref.get("provider") or "") != provider or str(ref.get("profileId") or "") != profile_id(profile):
            continue
        session_id = str(ref.get("nativeSessionId") or "")
        if not session_id or session_id in seen:
            continue
        threads.append(
            {
                "id": session_id,
                "cwd": str(ref.get("projectPath") or workspace),
                "preview": str(ref.get("title") or f"{provider_label(profile)} session"),
                "updatedAt": str(ref.get("updatedAt") or ""),
                "source": "hub-ref",
                "path": "",
            }
        )
        seen.add(session_id)
    return sorted(
        threads,
        key=lambda item: str(item.get("updatedAt") or item.get("updated_at") or ""),
        reverse=True,
    )


def workspaces_for(profile: dict, threads: list[dict] | None = None) -> list[str]:
    """Project folders to show for this profile (its workspace + saved Codex homes)."""
    nh = native()
    ws: list[str] = []
    w = str(profile.get("workspace") or L.DEFAULT_WORKSPACE)
    ws.append(w)
    try:
        if provider_key(profile) == "codex" and profile.get("codexHome"):
            for extra in nh.load_codex_saved_workspaces(Path(str(profile.get("codexHome"))), include_default=True):
                if extra not in ws:
                    ws.append(extra)
        for thread in threads if threads is not None else project_threads(profile):
            extra = str(thread.get("cwd") or "").strip()
            if extra and extra not in ws:
                ws.append(extra)
    except Exception:
        pass
    return ws


# The Hub's own repo dir (…/s). Sessions run here while developing the Hub are
# noise, never a real "project", so they are excluded from the coding sidebar.
APP_REPO_ROOT = Path(__file__).resolve().parents[2]


def _norm_path(path: object) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(text))


_APP_REPO_NORM = _norm_path(APP_REPO_ROOT)


def _is_app_repo(path: object) -> bool:
    key = _norm_path(path)
    return bool(key) and key == _APP_REPO_NORM


def _incidental_dirs(profile: dict) -> set[str]:
    """Catch-all dirs that shouldn't count as a *project* for non-Codex providers:
    the Hub's repo and the default workspace (``Documents\\Codex``), where stray
    test sessions land — that's why a "Codex" folder was leaking into Claude,
    Cursor and Antigravity. (Codex keeps it: it's a real entry in Codex's own
    project registry, shown as "No chats" like Codex Desktop.)"""
    dirs = {_APP_REPO_NORM, _norm_path(L.DEFAULT_WORKSPACE), _norm_path(profile.get("workspace"))}
    dirs.discard("")
    return dirs


def project_workspaces(profile: dict, threads: list[dict]) -> list[str]:
    """Real registered project folders for a profile: Codex's saved workspaces,
    or — for providers with no registry (Claude/Cursor/Antigravity) — the
    distinct project dirs its threads actually ran in. Never a catch-all dir."""
    provider = provider_key(profile)
    incidental = {_APP_REPO_NORM} if provider == "codex" else _incidental_dirs(profile)
    out: list[str] = []
    seen: set[str] = set()

    def add(path: object) -> None:
        text = str(path or "").strip()
        key = _norm_path(text)
        if not key or key in seen or key in incidental:
            return
        seen.add(key)
        out.append(text)

    if provider == "codex" and profile.get("codexHome"):
        # include_default reads the *default* codex home's global-state file,
        # which is where the real project registry (project-order /
        # electron-saved-workspace-roots) lives — it does not inject a workspace.
        try:
            for extra in native().load_codex_saved_workspaces(
                Path(str(profile.get("codexHome"))), include_default=True
            ):
                add(extra)
        except Exception:
            pass
    else:
        for thread in threads:
            add(thread.get("cwd"))
    return out


def _codex_global_state(home: Path) -> dict:
    try:
        payload = json.loads((home / ".codex-global-state.json").read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _codex_state_tree() -> tuple[list[dict], list[dict]]:
    """Codex sidebar the way Codex Desktop builds it, from the SHARED default home
    (~/.codex) — identical for every Codex account. Workspaces come from the
    global-state project registry; each shows its ONE main thread (extra rows
    only for forks) from the authoritative `threads` table (archived hidden);
    the "Chats" section is exactly the global-state `projectless-thread-ids`."""
    nh = native()
    home = Path(L.DEFAULT_CODEX_HOME)
    by_id, _ = nh.load_codex_state_threads([home])
    gs = _codex_global_state(home)
    projectless = {str(x) for x in (gs.get("projectless-thread-ids") or []) if x}

    def to_ui(t: dict) -> dict:
        preview = t.get("preview") or t.get("firstUserMessage") or t.get("title") or "Codex session"
        return {
            "id": t.get("id", ""), "provider": "codex", "preview": str(preview),
            "cwd": t.get("cwd", ""), "updatedAt": t.get("updatedAt"),
            "path": t.get("path", ""), "source": "codex-state",
        }

    by_cwd: dict[str, list[dict]] = {}
    loose: list[dict] = []
    for thread in by_id.values():
        if thread.get("archived"):
            continue
        entry = to_ui(thread)
        if str(thread.get("id")) in projectless:
            loose.append(entry)
        else:
            by_cwd.setdefault(_norm_path(thread.get("cwd")), []).append(entry)

    roots: list[str] = []
    seen: set[str] = set()
    for raw in list(gs.get("project-order") or []) + list(gs.get("electron-saved-workspace-roots") or []):
        path = nh.clean_windows_path_text(raw)
        key = _norm_path(path)
        if not key or key in seen or _is_app_repo(path):
            continue
        seen.add(key)
        roots.append(path)

    projects: list[dict] = []
    for root in roots:
        threads = sorted(by_cwd.get(_norm_path(root), []), key=lambda e: str(e.get("updatedAt") or ""), reverse=True)
        projects.append({"path": root, "name": Path(root).name or root, "threads": threads})
    loose.sort(key=lambda e: str(e.get("updatedAt") or ""), reverse=True)
    return projects, loose


def project_tree(profile: dict) -> tuple[list[dict], list[dict]]:
    """Sidebar model: workspaces (folders) each with their thread(s), plus a
    Codex-only "Chats" section. Codex is built from its own authoritative state
    (one main thread per workspace + forks, shared across all Codex accounts);
    the other providers group their discovered threads by the folder they ran in."""
    if provider_key(profile) == "codex":
        return _codex_state_tree()
    threads = project_threads(profile)
    by_cwd: dict[str, list[dict]] = {}
    for thread in threads:
        by_cwd.setdefault(_norm_path(thread.get("cwd")), []).append(thread)
    projects: list[dict] = []
    for ws in project_workspaces(profile, threads):
        key = _norm_path(ws)
        # One main thread per workspace (the most recent). These providers create
        # a separate session per conversation and don't expose Codex's fork graph,
        # so older sessions are collapsed rather than listed as many "threads".
        ws_threads = sorted(
            by_cwd.get(key, []),
            key=lambda t: str(t.get("updatedAt") or t.get("updated_at") or ""),
            reverse=True,
        )[:1]
        projects.append({"path": ws, "name": Path(ws).name or ws, "threads": ws_threads})
    # Only Codex has a project-less "Chats" section; other providers don't.
    return projects, []
