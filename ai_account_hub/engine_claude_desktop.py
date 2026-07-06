"""Claude Desktop account-switch flow for HubEngine.

Extracted from engine.py as a mixin; ``HubEngine`` inherits it. Handles capturing
and swapping the per-account Claude Desktop session state, identity verification,
and the pending-login rescue."""

from __future__ import annotations

import json
import logging
import re
import datetime as _dt
from pathlib import Path

from ai_account_hub import core as L

_logger = logging.getLogger(__name__)


CLAUDE_DESKTOP_STATE_ITEMS = (
    "config.json",
    "claude_desktop_config.json",
    "buddy-tokens.json",
    "ant-did",
    "Local State",
    "Preferences",
    "DIPS",
    "DIPS-wal",
    "DIPS-shm",
    "Network",
    "Local Storage",
    "IndexedDB",
    "Session Storage",
    "Partitions",
    "WebStorage",
    "SharedStorage",
    "blob_storage",
    "Shared Dictionary",
)


class _ClaudeDesktopMixin:
    # ---------- Claude desktop switch ----------
    # Claude Desktop stores its signed-in web/app state in %APPDATA%\Claude and
    # does not expose a per-profile equivalent to Codex's CODEX_HOME desktop
    # launch flow. The Hub therefore keeps a managed copy of only the account
    # state files for each profile, swaps that into the default Claude Desktop
    # location, and launches with CLAUDE_CONFIG_DIR set so bundled Claude Code
    # operations line up with the selected account too.
    def _claude_desktop_profile_key(self, profile: dict) -> str:
        import hashlib

        raw = str(profile.get("id") or profile.get("claudeConfigDir") or profile.get("codexHome") or profile.get("name") or "claude")
        slug = re.sub(r"[^a-z0-9]+", "-", str(profile.get("name") or "claude").lower()).strip("-") or "claude"
        digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:12]
        return f"{slug[:36].strip('-') or 'claude'}-{digest}"

    def _claude_desktop_state_root(self, profile: dict) -> Path:
        return L.LAUNCHER_ROOT / "claude-desktop-states" / self._claude_desktop_profile_key(profile)

    def _claude_desktop_marker_path(self) -> Path:
        return L.LAUNCHER_ROOT / "claude-desktop-active-profile.json"

    def _claude_desktop_backup_root(self) -> Path:
        return L.LAUNCHER_ROOT / "claude-desktop-default-backup"

    def _active_claude_desktop_marker(self) -> dict:
        try:
            return json.loads(self._claude_desktop_marker_path().read_text(encoding="utf-8-sig"))
        except Exception:
            return {}

    def _copy_claude_desktop_state(self, source: Path, target: Path) -> tuple[int, list[str]]:
        import os
        import shutil

        copied = 0
        errors: list[str] = []
        target.mkdir(parents=True, exist_ok=True)

        def copy_file(src: Path, dst: Path) -> None:
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                if getattr(L, "_shared_read_copy")(src, dst):
                    return
            except Exception:
                pass
            shutil.copy2(src, dst)

        def copy_dir(src: Path, dst: Path) -> int:
            count = 0
            if dst.exists() or dst.is_symlink():
                if dst.is_dir() and not dst.is_symlink():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
            for current, dirnames, filenames in os.walk(src):
                current_path = Path(current)
                rel_root = current_path.relative_to(src)
                out_root = dst / rel_root
                out_root.mkdir(parents=True, exist_ok=True)
                for dirname in dirnames:
                    (out_root / dirname).mkdir(parents=True, exist_ok=True)
                for filename in filenames:
                    copy_file(current_path / filename, out_root / filename)
                    count += 1
            return count

        for rel in CLAUDE_DESKTOP_STATE_ITEMS:
            src = source / rel
            if not src.exists():
                continue
            dst = target / rel
            try:
                if dst.exists() or dst.is_symlink():
                    if dst.is_dir() and not dst.is_symlink():
                        shutil.rmtree(dst)
                    else:
                        dst.unlink()
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir() and not src.is_symlink():
                    copied += copy_dir(src, dst)
                else:
                    # Cookie DBs can still be held by a slow-closing Electron
                    # process; use the shared-read helper before giving up.
                    copy_file(src, dst)
                    copied += 1
            except Exception as error:
                errors.append(f"{rel}: {error}")
        return copied, errors

    def _claude_desktop_state_has_login(self, root: Path) -> bool:
        import sqlite3

        config = root / "config.json"
        has_oauth = False
        account_uuid = ""
        if config.exists():
            try:
                payload = json.loads(config.read_text(encoding="utf-8-sig"))
                has_oauth = bool(payload.get("oauth:tokenCache") or payload.get("oauth:tokenCacheV2"))
                account_uuid = str(payload.get("lastKnownAccountUuid") or "").strip()
            except Exception:
                has_oauth = False
        has_cookie = False
        for db_path in (
            root / "Network" / "Cookies",
            root / "Cookies",
            root / "Default" / "Network" / "Cookies",
            root / "Default" / "Cookies",
        ):
            if not db_path.exists():
                continue
            try:
                con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True, timeout=1)
                try:
                    row = con.execute(
                        """
                        select 1
                        from cookies
                        where host_key like '%claude.ai%'
                          and name in ('sessionKey', 'sessionKeyLC')
                        limit 1
                        """
                    ).fetchone()
                finally:
                    con.close()
                if row:
                    has_cookie = True
                    break
            except Exception:
                continue
        # OAuth cache + account UUID identifies the Claude account, but the
        # Desktop chat UI still needs its web/app session. OAuth-only state can
        # be the login screen, so it is not enough to call the Desktop login
        # captured.
        return bool(has_cookie and has_oauth and account_uuid)

    def _claude_desktop_state_has_identity_metadata(self, root: Path) -> bool:
        config = root / "config.json"
        if not config.exists():
            return False
        try:
            payload = json.loads(config.read_text(encoding="utf-8-sig"))
        except Exception:
            return False
        has_oauth = bool(payload.get("oauth:tokenCache") or payload.get("oauth:tokenCacheV2"))
        has_uuid = bool(str(payload.get("lastKnownAccountUuid") or "").strip())
        return bool(has_oauth and has_uuid)

    def _claude_desktop_recent_logged_in_signal(self, since: _dt.datetime | None = None) -> bool:
        log_path = L.CLAUDE_ROAMING_HOME / "logs" / "main.log"
        if not log_path.exists():
            return False
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")[-250_000:]
        except OSError:
            return False
        cutoff = None
        if since is not None:
            cutoff = since.replace(tzinfo=None) - _dt.timedelta(seconds=10)
        for line in reversed(text.splitlines()):
            if "claude.ai account active and logged in" not in line:
                continue
            if cutoff is None:
                return True
            match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
            if not match:
                return True
            try:
                stamp = _dt.datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return True
            return stamp >= cutoff
        return False

    def _claude_desktop_account_uuid(self, root: Path) -> str:
        config = root / "config.json"
        if not config.exists():
            return ""
        try:
            payload = json.loads(config.read_text(encoding="utf-8-sig"))
        except Exception:
            return ""
        return str(payload.get("lastKnownAccountUuid") or "").strip()

    def _claude_code_account_uuid(self, profile: dict) -> str:
        state = L.claude_profile_home(profile) / ".claude.json"
        if not state.exists():
            return ""
        try:
            payload = json.loads(state.read_text(encoding="utf-8-sig"))
        except Exception:
            return ""
        account = payload.get("oauthAccount") if isinstance(payload.get("oauthAccount"), dict) else {}
        return str(account.get("accountUuid") or "").strip()

    def _claude_expected_desktop_uuid(self, profile: dict) -> str:
        if L.claude_desktop_only(profile):
            return str(profile.get("claudeDesktopAccountUuid") or "").strip()
        return self._claude_code_account_uuid(profile)

    def _identity_hash(self, value: str) -> str:
        import hashlib

        return hashlib.sha256(str(value or "").encode("utf-8", "replace")).hexdigest()[:12] if value else "missing"

    def _claude_identity_error(self, profile: dict, state_dir: Path) -> str:
        expected = self._claude_expected_desktop_uuid(profile)
        actual = self._claude_desktop_account_uuid(state_dir)
        name = str(profile.get("name") or "Claude")
        if not expected:
            if L.claude_desktop_only(profile):
                return (
                    f"{name} has no bound Claude Desktop identity. "
                    "Use Desktop Login once so AI Account Hub can capture and bind this account."
                )
            return (
                f"{name} has no Claude Code account UUID in {L.claude_profile_home(profile) / '.claude.json'}. "
                "Run Claude Code Login/Status first, then save the matching Desktop login."
            )
        if not actual:
            return (
                f"{name} has no captured Claude Desktop account identity yet. "
                "Use Desktop Login and sign into the matching Claude account; capture is automatic."
            )
        if actual != expected:
            return (
                f"{name} captured Claude Desktop account does not match its Claude Code profile. "
                f"Claude Code={self._identity_hash(expected)}, Desktop={self._identity_hash(actual)}. "
                "Use Desktop Login and sign into the correct Claude account; capture is automatic."
            )
        return ""

    def claude_desktop_state_status(self, profile: dict) -> dict:
        """Return a redacted, UI-safe summary of the selected profile's captured
        Claude Desktop state. This deliberately compares identity, not merely
        "has cookies", because cookies from the wrong account are worse than no
        saved state."""
        if L.provider_key(profile) != "claude":
            return {"state": "not_applicable", "label": "—", "detail": ""}
        state_dir = self._claude_desktop_state_root(profile)
        expected = self._claude_expected_desktop_uuid(profile)
        actual = self._claude_desktop_account_uuid(state_dir)
        has_login = state_dir.exists() and self._claude_desktop_state_has_login(state_dir)
        has_identity_metadata = state_dir.exists() and self._claude_desktop_state_has_identity_metadata(state_dir)
        if not expected:
            if L.claude_desktop_only(profile):
                return {
                    "state": "missing",
                    "label": "Not captured",
                    "detail": (
                        "Use Desktop Login. After the official login completes, AI Account Hub "
                        "will bind this profile to Claude Desktop's account UUID."
                    ),
                    "codeHash": "not-required",
                    "desktopHash": self._identity_hash(actual),
                }
            return {
                "state": "unknown",
                "label": "Code identity missing",
                "detail": f"No Claude Code account UUID found in {L.claude_profile_home(profile) / '.claude.json'}.",
                "codeHash": self._identity_hash(expected),
                "desktopHash": self._identity_hash(actual),
            }
        if not has_login:
            if has_identity_metadata:
                return {
                    "state": "missing",
                    "label": "Login page",
                    "detail": (
                        "Claude Desktop identity metadata was captured, but the Desktop chat session was not. "
                        "Use Desktop Login and finish the official Claude Desktop login before capture."
                    ),
                    "codeHash": self._identity_hash(expected),
                    "desktopHash": self._identity_hash(actual),
                }
            return {
                "state": "missing",
                "label": "Not captured",
                "detail": "Use Desktop Login and sign into the matching Claude account; capture is automatic.",
                "codeHash": self._identity_hash(expected),
                "desktopHash": self._identity_hash(actual),
            }
        if not actual:
            return {
                "state": "unknown",
                "label": "Identity missing",
                "detail": "Captured Claude Desktop state has cookies but no account UUID.",
                "codeHash": self._identity_hash(expected),
                "desktopHash": self._identity_hash(actual),
            }
        if actual != expected:
            return {
                "state": "mismatch",
                "label": "Mismatch",
                "detail": (
                    f"Claude Code={self._identity_hash(expected)}, "
                    f"Desktop={self._identity_hash(actual)}. Use Desktop Login to replace it automatically."
                ),
                "codeHash": self._identity_hash(expected),
                "desktopHash": self._identity_hash(actual),
            }
        return {
            "state": "ready",
            "label": "Saved",
            "detail": (
                f"Claude Desktop state matches this Desktop-only profile ({self._identity_hash(expected)})."
                if L.claude_desktop_only(profile) else
                f"Claude Desktop state matches this Claude Code profile ({self._identity_hash(expected)})."
            ),
            "codeHash": self._identity_hash(expected),
            "desktopHash": self._identity_hash(actual),
        }

    def _clear_claude_desktop_default_state(self) -> tuple[int, list[str]]:
        import shutil

        removed = 0
        errors: list[str] = []
        for rel in CLAUDE_DESKTOP_STATE_ITEMS:
            target = L.CLAUDE_ROAMING_HOME / rel
            if not target.exists() and not target.is_symlink():
                continue
            try:
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                removed += 1
            except Exception as error:
                errors.append(f"{rel}: {error}")
        return removed, errors

    def _stop_claude_desktop(self) -> str:
        target = str(Path(self.claude_desktop_path)) if self.claude_desktop_path else ""
        script = f"""
$target = {L.quote_ps(target)}
function Get-ClaudeDesktopProcess {{
    $items = @()
    foreach ($process in @(Get-Process -ErrorAction SilentlyContinue)) {{
        $path = ""
        try {{ $path = [string]$process.Path }} catch {{ $path = "" }}
        $isTarget = $false
        if ($target -and $path -and ([String]::Equals($path, $target, [StringComparison]::OrdinalIgnoreCase))) {{ $isTarget = $true }}
        if ($process.ProcessName -eq "Claude" -or $path -match '\\\\Claude\\\\Claude\\.exe$' -or $path -match '\\\\WindowsApps\\\\.*Claude.*\\\\app\\\\Claude\\.exe$') {{ $isTarget = $true }}
        if ($isTarget) {{ $items += $process }}
    }}
    return @($items)
}}
$matches = @(Get-ClaudeDesktopProcess)
if ($matches.Count -eq 0) {{ Write-Output "No Claude Desktop background processes were running."; exit 0 }}
$closed = 0
foreach ($p in $matches) {{ try {{ if ($p.MainWindowHandle -ne [IntPtr]::Zero) {{ if ($p.CloseMainWindow()) {{ $closed++ }} }} }} catch {{}} }}
$deadline = [DateTime]::Now.AddSeconds(12)
do {{ Start-Sleep -Milliseconds 250; $remaining = @(Get-ClaudeDesktopProcess) }} while ($remaining.Count -gt 0 -and [DateTime]::Now -lt $deadline)
$killed = 0
foreach ($p in $remaining) {{ try {{ $p.Kill(); $killed++ }} catch {{}} }}
Write-Output "Stopped $($matches.Count) Claude Desktop process(es). Graceful: $closed. Force: $killed."
"""
        proc = L.run_capture("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], L.DEFAULT_WORKSPACE, timeout=25)
        return proc.stdout.strip() or proc.stderr.strip() or "Claude Desktop process check completed."

    def _sync_active_claude_desktop_back(self) -> str:
        marker = self._active_claude_desktop_marker()
        if marker.get("pendingCapture"):
            return "Claude Desktop login is pending capture; not saving it into a profile yet."
        state_dir = Path(str(marker.get("stateDir") or ""))
        if not str(state_dir).strip():
            return ""
        if not self._claude_desktop_state_has_login(L.CLAUDE_ROAMING_HOME):
            return "Skipped saving active Claude Desktop state because it is logged out; saved profile login kept."
        active_uuid = self._claude_desktop_account_uuid(L.CLAUDE_ROAMING_HOME)
        state_uuid = self._claude_desktop_account_uuid(state_dir)
        if active_uuid and state_uuid and active_uuid != state_uuid:
            return (
                "Skipped saving active Claude Desktop state because its account identity changed "
                f"(active={self._identity_hash(active_uuid)}, saved={self._identity_hash(state_uuid)})."
            )
        copied, errors = self._copy_claude_desktop_state(L.CLAUDE_ROAMING_HOME, state_dir)
        if errors:
            return f"Saved current Claude Desktop state back with warnings: {'; '.join(errors[:3])}"
        if copied:
            return f"Saved current Claude Desktop state back to {marker.get('name') or 'previous profile'}."
        return "No current Claude Desktop state files were found to save back."

    def _backup_claude_desktop_default_once(self) -> str:
        backup = self._claude_desktop_backup_root() / "default"
        if backup.exists():
            return ""
        copied, errors = self._copy_claude_desktop_state(L.CLAUDE_ROAMING_HOME, backup)
        if errors:
            return f"Default Claude Desktop backup created with warnings: {'; '.join(errors[:3])}"
        return f"Backed up default Claude Desktop state ({copied} item{'s' if copied != 1 else ''})." if copied else ""

    def _write_claude_desktop_marker(self, profile: dict, state_dir: Path, *, pending: bool = False) -> None:
        marker = {
            "provider": "claude",
            "name": str(profile.get("name") or "Claude"),
            "profileKey": self._claude_desktop_profile_key(profile),
            "claudeConfigDir": str(L.claude_profile_home(profile)),
            "stateDir": str(state_dir),
            "expectedAccountHash": self._identity_hash(self._claude_expected_desktop_uuid(profile)),
            "profileType": L.claude_profile_type(profile),
            "pendingCapture": bool(pending),
            "syncedAtUtc": L.iso_utc_now(),
        }
        self._claude_desktop_marker_path().parent.mkdir(parents=True, exist_ok=True)
        self._claude_desktop_marker_path().write_text(json.dumps(marker, indent=2), encoding="utf-8")

    def _start_claude_desktop(self, profile: dict) -> None:
        import os
        import subprocess

        env = os.environ.copy()
        env["CLAUDE_CONFIG_DIR"] = str(L.claude_profile_home(profile))
        subprocess.Popen([self.claude_desktop_path], cwd=str(Path(self.claude_desktop_path).parent), env=env)

    def _pending_claude_desktop_profile(
        self,
        marker: dict,
        profiles: list[dict] | None = None,
    ) -> tuple[dict | None, list[dict], bool]:
        """Resolve the profile whose clean Desktop login is awaiting capture.

        The Accounts UI passes its live profile objects so capture updates the
        card immediately. Other callers fall back to the persisted profile list
        and save it here after a successful rescue.
        """
        candidates = profiles if profiles is not None else list(L.load_profiles())
        wanted = str(marker.get("profileKey") or "")
        profile = next(
            (
                item for item in candidates
                if L.provider_key(item) == "claude"
                and self._claude_desktop_profile_key(item) == wanted
            ),
            None,
        )
        return profile, candidates, profiles is None

    def _capture_pending_claude_desktop(
        self,
        profiles: list[dict] | None = None,
        *,
        stop_desktop: bool = True,
    ) -> tuple[bool, str]:
        """Save a completed clean-login flow before any account state is swapped.

        This is the safety net for a user who signs in and immediately chooses
        another account before the UI timer has completed its normal capture.
        """
        marker = self._active_claude_desktop_marker()
        if not marker.get("pendingCapture"):
            return True, ""
        pending, candidates, persist_here = self._pending_claude_desktop_profile(marker, profiles)
        if pending is None:
            return False, "A Claude Desktop login is pending, but its Hub profile could not be found."
        lines: list[str] = []
        if stop_desktop:
            lines.append(self._stop_claude_desktop())
        ok, message = self.claude_capture_desktop(
            pending,
            stop_desktop=False,
            relaunch_after=False,
        )
        lines.append(message)
        if ok and persist_here:
            L.save_profiles(candidates)
        if ok:
            lines.append(f"Completed pending Desktop Login capture for {pending.get('name', 'Claude')}.")
        return ok, "\n".join(part for part in lines if part)

    def claude_switch_desktop(
        self,
        profile: dict,
        profiles: list[dict] | None = None,
    ) -> tuple[bool, str]:
        if L.provider_key(profile) != "claude":
            return self.action_desktop(profile)
        if not self.claude_desktop_path:
            return False, "Claude Desktop was not found."

        name = str(profile.get("name") or "Claude")
        lines: list[str] = []
        stopped_for_pending = False
        if self._active_claude_desktop_marker().get("pendingCapture"):
            rescued, rescue_message = self._capture_pending_claude_desktop(
                profiles,
                stop_desktop=True,
            )
            if rescue_message:
                lines.append(rescue_message)
            stopped_for_pending = True
            if not rescued:
                return False, "\n".join(
                    lines + [
                        "The pending Claude Desktop login could not be saved, so the account switch was cancelled."
                    ]
                )

        state_dir = self._claude_desktop_state_root(profile)
        state_has_login = self._claude_desktop_state_has_login(state_dir) if state_dir.exists() else False
        if not state_has_login:
            return False, "\n".join(
                lines + [
                    f"No saved Claude Desktop login exists for {name}.",
                    "Use Desktop Login and finish the official login. AI Account Hub will save it automatically.",
                ]
            )
        identity_error = self._claude_identity_error(profile, state_dir)
        if identity_error:
            return False, identity_error

        marker = self._active_claude_desktop_marker()
        expected = self._claude_expected_desktop_uuid(profile)
        active_same_profile = (
            marker.get("profileKey") == self._claude_desktop_profile_key(profile)
            and not marker.get("pendingCapture")
        )
        default_same_identity = (
            bool(expected)
            and self._claude_desktop_state_has_login(L.CLAUDE_ROAMING_HOME)
            and self._claude_desktop_account_uuid(L.CLAUDE_ROAMING_HOME) == expected
        )
        if active_same_profile and default_same_identity:
            self._start_claude_desktop(profile)
            lines.append(f"Claude Desktop is already synced to {name}; requested launch/focus without rewriting its state.")
            return True, "\n".join(lines)

        if not stopped_for_pending:
            lines.append(self._stop_claude_desktop())

        back = self._sync_active_claude_desktop_back()
        if back:
            lines.append(back)

        backup = self._backup_claude_desktop_default_once()
        if backup:
            lines.append(backup)

        removed, clear_errors = self._clear_claude_desktop_default_state()
        if clear_errors:
            lines.append(f"Cleared Claude Desktop default state with warnings: {'; '.join(clear_errors[:3])}")
        elif removed:
            lines.append(f"Cleared Claude Desktop default state ({removed} item{'s' if removed != 1 else ''}).")

        copied, copy_errors = self._copy_claude_desktop_state(state_dir, L.CLAUDE_ROAMING_HOME)
        if copied:
            lines.append(f"Synced {name} into Claude Desktop ({copied} item{'s' if copied != 1 else ''}).")
        elif not state_dir.exists():
            lines.append(f"No saved Claude Desktop state exists for {name} yet. Sign in once in Claude Desktop, then switch away so AI Hub can save it.")
        else:
            lines.append(f"No Claude Desktop state files were available for {name}.")
        if copy_errors:
            lines.append(f"Claude Desktop sync warnings: {'; '.join(copy_errors[:3])}")

        self._write_claude_desktop_marker(profile, state_dir)
        self._start_claude_desktop(profile)
        lines.append(f"Switched Claude Desktop to {name} and requested relaunch.")
        return True, "\n".join(lines)

    def claude_desktop_login(
        self,
        profile: dict,
        profiles: list[dict] | None = None,
    ) -> tuple[bool, str]:
        if L.provider_key(profile) != "claude":
            return False, "Desktop Login is Claude-only."
        if not self.claude_desktop_path:
            return False, "Claude Desktop was not found."
        name = str(profile.get("name") or "Claude")
        expected = self._claude_expected_desktop_uuid(profile)
        if not expected and not L.claude_desktop_only(profile):
            return False, (
                f"{name} needs a Claude Code identity before Desktop login can be captured safely. "
                f"Run Claude Login/Status first for {L.claude_profile_home(profile)}, then try Desktop Login again."
            )
        state_dir = self._claude_desktop_state_root(profile)
        lines = [self._stop_claude_desktop()]
        if self._active_claude_desktop_marker().get("pendingCapture"):
            rescued, rescue_message = self._capture_pending_claude_desktop(
                profiles,
                stop_desktop=False,
            )
            if rescue_message:
                lines.append(rescue_message)
            if not rescued:
                lines.append("The previous pending login had no verified session to preserve.")
        back = self._sync_active_claude_desktop_back()
        if back:
            lines.append(back)
        backup = self._backup_claude_desktop_default_once()
        if backup:
            lines.append(backup)
        removed, clear_errors = self._clear_claude_desktop_default_state()
        if clear_errors:
            lines.append(f"Cleared Claude Desktop default state with warnings: {'; '.join(clear_errors[:3])}")
        else:
            lines.append(f"Cleared Claude Desktop default state ({removed} item{'s' if removed != 1 else ''}).")
        self._write_claude_desktop_marker(profile, state_dir, pending=True)
        self._start_claude_desktop(profile)
        lines.append(
            f"Opened a clean Claude Desktop login for {name}. "
            "AI Hub will watch for the matching login and capture it automatically."
        )
        return True, "\n".join(lines)

    def claude_desktop_login_capture_status(self, profile: dict, since: _dt.datetime | None = None) -> dict:
        if L.provider_key(profile) != "claude":
            return {"state": "error", "done": True, "ok": False, "message": "Desktop capture is Claude-only."}
        name = str(profile.get("name") or "Claude")
        expected = self._claude_expected_desktop_uuid(profile)
        if not expected and not L.claude_desktop_only(profile):
            return {
                "state": "error",
                "done": True,
                "ok": False,
                "message": f"{name} has no Claude Code identity yet; cannot verify the Desktop login.",
            }
        actual = self._claude_desktop_account_uuid(L.CLAUDE_ROAMING_HOME)
        if expected and actual and actual != expected:
            return {
                "state": "mismatch",
                "done": True,
                "ok": False,
                "message": (
                    f"Claude Desktop login was not captured for {name}: account mismatch. "
                    f"Claude Code={self._identity_hash(expected)}, Desktop={self._identity_hash(actual)}."
                ),
            }
        if not self._claude_desktop_state_has_login(L.CLAUDE_ROAMING_HOME):
            if (
                self._claude_desktop_state_has_identity_metadata(L.CLAUDE_ROAMING_HOME)
                and self._claude_desktop_recent_logged_in_signal(since)
            ):
                return {
                    "state": "ready_needs_stop",
                    "done": True,
                    "ok": True,
                    "message": (
                        f"Claude Desktop reports {name} is logged in. "
                        "AI Hub will briefly restart it to capture the unlocked session."
                    ),
                }
            # Desktop Login starts from a state directory that the Hub cleared.
            # Once fresh OAuth metadata + account UUID appear, allow a verified
            # stop-and-copy attempt even if this Claude build omitted/delayed
            # the log signal. A short grace period lets the cookie DB settle.
            elapsed = (
                (_dt.datetime.now() - since).total_seconds()
                if since is not None else 0
            )
            if (
                self._claude_desktop_state_has_identity_metadata(L.CLAUDE_ROAMING_HOME)
                and since is not None
                and elapsed >= 8
            ):
                return {
                    "state": "ready_needs_stop",
                    "done": True,
                    "ok": True,
                    "message": (
                        f"Claude Desktop identity for {name} is ready. "
                        "AI Hub will briefly restart it and verify the saved session."
                    ),
                }
            if self._claude_desktop_state_has_identity_metadata(L.CLAUDE_ROAMING_HOME):
                return {
                    "state": "waiting_session",
                    "done": False,
                    "ok": False,
                    "message": (
                        "Claude Desktop account metadata is present, but the app is still waiting for the "
                        f"Desktop chat session for {name}. Finish the login screen in Claude Desktop."
                    ),
                }
            return {
                "state": "waiting",
                "done": False,
                "ok": False,
                "message": f"Waiting for Claude Desktop login for {name}…",
            }
        if not actual:
            return {
                "state": "waiting_identity",
                "done": False,
                "ok": False,
                "message": "Claude Desktop login detected; waiting for account identity…",
            }
        return {
            "state": "ready",
            "done": True,
            "ok": True,
            "message": f"Matching Claude Desktop login detected for {name}.",
        }

    def claude_capture_desktop(
        self,
        profile: dict,
        *,
        stop_desktop: bool = True,
        relaunch_after: bool = False,
    ) -> tuple[bool, str]:
        if L.provider_key(profile) != "claude":
            return False, "Desktop capture is Claude-only."
        name = str(profile.get("name") or "Claude")
        state_dir = self._claude_desktop_state_root(profile)
        lines = [self._stop_claude_desktop()] if stop_desktop else []
        def fail(message: str) -> tuple[bool, str]:
            if relaunch_after:
                self._start_claude_desktop(profile)
                return False, "\n".join(lines + [message, "Claude Desktop was relaunched."])
            return False, "\n".join(lines + [message])

        if not self._claude_desktop_state_has_login(L.CLAUDE_ROAMING_HOME):
            return fail("Claude Desktop is not logged in. Complete Desktop Login first.")
        expected = self._claude_expected_desktop_uuid(profile)
        actual = self._claude_desktop_account_uuid(L.CLAUDE_ROAMING_HOME)
        if not expected and not L.claude_desktop_only(profile):
            return fail(
                "\n".join([
                    f"{name} has no Claude Code account UUID in {L.claude_profile_home(profile) / '.claude.json'}.",
                    "Run Claude Code Login/Status first so AI Hub can verify the Desktop login belongs to the same account.",
                ])
            )
        if not actual:
            return fail("Claude Desktop account identity was not found after login.")
        if expected and actual != expected:
            identity_label = "Saved Desktop" if L.claude_desktop_only(profile) else "Claude Code"
            return fail(
                "\n".join([
                    f"Refusing to save Claude Desktop into {name}: account mismatch.",
                    f"{identity_label}={self._identity_hash(expected)}, Desktop={self._identity_hash(actual)}.",
                    "Run Desktop Login and sign into the account assigned to this profile.",
                ])
            )
        copied, errors = self._copy_claude_desktop_state(L.CLAUDE_ROAMING_HOME, state_dir)
        if not copied:
            return fail(f"No Claude Desktop state files could be saved for {name}.")
        if not self._claude_desktop_state_has_login(state_dir):
            return fail(
                f"Claude Desktop state for {name} was copied, but the saved session could not be verified."
            )
        if L.claude_desktop_only(profile):
            if not expected:
                profile["claudeDesktopAccountUuid"] = actual
            profile["claudeDesktopCaptured"] = True
            profile["accountType"] = "Claude Desktop"
            if not str(profile.get("accountPlan") or "").strip():
                profile["accountPlan"] = "Free"
            profile["lastLimitsError"] = ""
            profile["lastUsageError"] = (
                "Claude Desktop-only account. Claude Code limits and usage are unavailable."
            )
            summary = profile.get("usageSummary") if isinstance(profile.get("usageSummary"), dict) else {}
            summary.update({"desktopOnly": True, "desktopReady": True})
            profile["usageSummary"] = summary
        self._write_claude_desktop_marker(profile, state_dir)
        suffix = f" Warnings: {'; '.join(errors[:3])}" if errors else ""
        live_note = " Desktop remains open." if not stop_desktop else ""
        if relaunch_after:
            self._start_claude_desktop(profile)
            live_note = " Claude Desktop was relaunched."
        lines.append(f"Saved Claude Desktop login for {name} ({copied} item{'s' if copied != 1 else ''}).{live_note}{suffix}")
        return True, "\n".join(lines)

    def action_home(self, profile: dict) -> tuple[bool, str]:
        import os
        provider = L.provider_key(profile)
        try:
            if provider == "claude":
                home = L.claude_profile_home(profile)
            elif provider == "cursor":
                home = L.CURSOR_ROAMING_HOME
            elif provider == "antigravity":
                home = L.ANTIGRAVITY_ROAMING_HOME
            else:
                L.ensure_profile_home(profile)
                home = Path(str(profile.get("codexHome")))
            Path(home).mkdir(parents=True, exist_ok=True)
            os.startfile(str(home))  # type: ignore[attr-defined]
            return True, f"Opened {home}."
        except Exception as error:
            return False, str(error)

    def action_seed(self, profile: dict) -> tuple[bool, str]:
        try:
            return True, L.ensure_file_credential_store(profile)
        except Exception as error:
            return False, str(error)

    def action_status(self, profile: dict) -> tuple[bool, str]:
        provider = L.provider_key(profile)
        if provider == "codex":
            if not self.codex_cli_path:
                return False, self.codex_cli_error or "codex.exe not found."
            L.ensure_file_credential_store(profile)
            ws = self._workspace(profile)
            proc = L.run_capture(self.codex_cli_path, ["login", "status"], ws, env={"CODEX_HOME": str(profile.get("codexHome"))}, timeout=60)
            out = "\n\n".join(p for p in (f"Exit code: {proc.returncode}", proc.stdout.strip(), proc.stderr.strip()) if p)
            return True, f"Status for {profile.get('name','Account')}:\n{out}"
        result = self.refresh_profile(profile)
        self.record_history(profile, "status")
        return bool(result.get("ok")), f"{L.provider_label(profile)} status for {profile.get('name','Account')}: {'Ready' if result.get('ok') else profile.get('lastLimitsError','Not ready')}"

    def action_doctor(self, profile: dict) -> tuple[bool, str]:
        provider = L.provider_key(profile)
        ws = self._workspace(profile)
        if provider == "codex":
            if not self.codex_cli_path:
                return False, "codex.exe not found."
            L.ensure_file_credential_store(profile)
            proc = L.run_capture(self.codex_cli_path, ["doctor", "--summary"], ws, env={"CODEX_HOME": str(profile.get("codexHome"))}, timeout=90)
            out = "\n\n".join(p for p in (f"Exit code: {proc.returncode}", proc.stdout.strip(), proc.stderr.strip()) if p)
            return True, f"Doctor for {profile.get('name','Account')}:\n{out}"
        if provider == "claude":
            if L.claude_desktop_only(profile):
                return False, "Claude Code Doctor is disabled for Desktop-only accounts. Use Refresh to verify the saved Desktop session."
            if not self.claude_code_path:
                return False, "Claude Code CLI not found."
            proc = L.run_capture(self.claude_code_path, ["doctor"], ws, env={"CLAUDE_CONFIG_DIR": str(L.claude_profile_home(profile))}, timeout=60)
            out = "\n\n".join(p for p in (f"Exit code: {proc.returncode}", proc.stdout.strip(), proc.stderr.strip()) if p)
            return True, f"Claude doctor:\n{L.redact_auth_output(out)}"
        result = self.refresh_profile(profile)
        return bool(result.get("ok")), f"{L.provider_label(profile)} doctor: refresh ok={bool(result.get('ok'))}, state={profile.get('lastLimitsError') or 'Ready'}"

    def online_links(self, profile: dict) -> list[dict]:
        try:
            return L.online_links_for_profile(profile)
        except Exception:
            return []

    def open_online_link(self, profile: dict, link: dict) -> tuple[bool, str]:
        import subprocess
        import webbrowser
        url = str(link.get("url") or "").strip()
        label = str(link.get("label") or "Online").strip()
        if not L.is_safe_online_url(url):
            return False, f"{label} is not a safe http/https URL."
        try:
            command = L.browser_command_for_url(profile, url)
            if command:
                subprocess.Popen(command, shell=True)
                return True, f"Opened {label} for {profile.get('name','Account')}."
            if not L.uses_isolated_browser_profile(profile):
                webbrowser.open(url, new=2)
                return True, f"Opened {label} in the system browser."
            browser_path = L.locate_account_browser_path()
            if not browser_path:
                webbrowser.open(url, new=2)
                return True, f"Opened {label} in the system browser (install Chrome/Edge for a per-account profile)."
            # Dedicated blank-canvas profile per account. Seed it from the desktop
            # app's saved login when possible so the page opens already signed in;
            # otherwise the note tells the user a one-time sign-in is needed (it
            # then persists in this profile). Session state is the source of truth.
            seed_note = ""
            try:
                seed_note = L.seed_browser_profile_from_desktop(profile)
            except Exception:
                _logger.debug("Online seed failed", exc_info=True)
            signed_in = L.browser_profile_has_session_cookie(profile)
            pdir = L.browser_profile_dir_for_profile(profile)
            pdir.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(L.browser_profile_launch_args(profile, url, browser_path), cwd=str(pdir))
            if seed_note:
                status = seed_note
            elif signed_in:
                status = f"Opened {label} — signed in from this account's saved browser login."
            else:
                status = f"Opened {label} — sign in once here; this per-account profile will remember it."
            return True, status
        except Exception as error:
            return False, f"Could not open {label}: {error}"

    def use_reset_credit(self, profile: dict) -> tuple[bool, str]:
        # Codex-only: refresh with consume-reset action via the node helper.
        if L.provider_key(profile) != "codex":
            return False, "Reset credits are a Codex-only feature."
        if not self.node_path or not self.codex_cli_path or not L.HELPER_PATH.exists():
            return False, "Codex CLI, Node.js, and the limits helper are required."
        import json
        L.ensure_file_credential_store(profile)
        ws = self._workspace(profile)
        proc = L.run_capture(self.node_path, [str(L.HELPER_PATH), self.codex_cli_path, str(profile.get("codexHome")), str(ws), "consume-reset"], ws, timeout=45)
        stdout = proc.stdout.strip()
        if not stdout:
            return False, proc.stderr.strip() or "No output from limits helper."
        result = json.loads(stdout)
        L.set_profile_limits_from_result(profile, result)
        self.record_history(profile, "reset-credit")
        outcome = str(result.get("resetOutcome") or "")
        messages = {
            "reset": "Reset credit consumed. Eligible windows were reset.",
            "nothingToReset": "No eligible rate-limit window to reset.",
            "noCredit": "No earned reset credits available.",
            "alreadyRedeemed": "Reset request already redeemed. Limits refreshed.",
        }
        return bool(result.get("ok", True)), f"{profile.get('name','Account')}: {messages.get(outcome, f'Outcome: {outcome}')}"

