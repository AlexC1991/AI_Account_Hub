"""Online links + isolated per-account browser profiles: launch args, cookie
probing, and Windows desktop-cookie seeding.

A domain extracted from hub_core (browser_profile_mode stays in hub_core as it's
referenced by the state machine; pulled in here via ``from hub_core import *``)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from ai_account_hub.core.hub_core import *  # noqa: F401,F403

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

