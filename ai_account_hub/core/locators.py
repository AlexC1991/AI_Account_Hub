"""Executable + icon discovery: locate provider CLIs/desktops, AppX lookups,
icon caching/extraction, and per-provider local account status probes.

A domain extracted from hub_core (locate_claude_desktop_path stays in hub_core;
pulled in here via ``from hub_core import *``)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from ai_account_hub.core import hub_core
from ai_account_hub.core.hub_core import *  # noqa: F401,F403

_logger = logging.getLogger(__name__)


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



def locate_claude_code_path() -> str:
    root = hub_core.CLAUDE_ROAMING_HOME / "claude-code"
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


