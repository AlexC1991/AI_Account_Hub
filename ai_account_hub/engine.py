"""Backend engine for the Qt port.

The whole point: reuse the existing, working logic rather than reimplement it.
`hub_core` (imported via ``ai_account_hub.core``) holds a large library of pure, non-UI
functions (limit parsing, profile state, history, discovery, launch helpers,
provider probes). This engine re-ports only the handful of thin *methods* that
were entangled with the old Tk god-class (the subprocess probe + refresh
methods, which just call run_capture + hub_core helpers).

Everything here is Tk-free and runs on a worker thread so the Qt UI never
blocks.
"""

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


class HubEngine:
    def __init__(self) -> None:
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
        self.antigravity_cli_diagnostics: dict = {}
        self.discover_tools()

    # ---------- discovery (mirrors legacy._discover_tools path extraction) ----------
    def discover_tools(self) -> None:
        try:
            from ai_account_hub.core import provider_discovery

            report = provider_discovery.discover_provider_tools()
            providers = report.get("providers") if isinstance(report.get("providers"), dict) else {}
            support = report.get("support") if isinstance(report.get("support"), dict) else {}

            def path_for(provider: str, slot: str) -> str:
                pmap = providers.get(provider) if isinstance(providers.get(provider), dict) else {}
                target = pmap.get(slot) if isinstance(pmap.get(slot), dict) else {}
                return str(target.get("path") or "") if target.get("found") else ""

            self.codex_cli_path = path_for("codex", "cli")
            self.claude_desktop_path = path_for("claude", "desktop")
            self.claude_code_path = path_for("claude", "cli")
            self.cursor_desktop_path = path_for("cursor", "desktop")
            self.cursor_cli_path = path_for("cursor", "cli")
            self.cursor_agent_path = path_for("cursor", "agent")
            self.antigravity_desktop_path = path_for("antigravity", "desktop")
            self.antigravity_cli_path = path_for("antigravity", "cli")
            node = support.get("node") if isinstance(support.get("node"), dict) else {}
            self.node_path = str(node.get("path") or "") if node.get("found") else ""
        except Exception:
            # fall back to legacy's targeted locators
            self.codex_cli_path = _safe(L.locate_codex_cli)
            self.node_path = _safe(L.locate_node)
            self.claude_desktop_path = _safe(L.locate_claude_desktop_path)
            self.claude_code_path = _safe(L.locate_claude_code_path)
            self.cursor_desktop_path = _safe(L.locate_cursor_desktop_path)
            self.cursor_cli_path = _safe(L.locate_cursor_cli_path)
            self.cursor_agent_path = _safe(L.locate_cursor_agent)
            self.antigravity_desktop_path = _safe(L.locate_antigravity_desktop_path)
            self.antigravity_cli_path = _safe(L.locate_antigravity_cli_path)
        self.codex_cli_error = "" if self.codex_cli_path else "Codex CLI was not found."
        self.node_error = "" if self.node_path else "Node.js was not found."
        try:
            self.antigravity_cli_diagnostics = L.antigravity_cli_diagnostics(self.antigravity_cli_path)
        except Exception:
            self.antigravity_cli_diagnostics = {}

    # ---------- ported probe methods (thin, run_capture-based) ----------
    def _run_claude_capture(self, profile: dict, args: list[str], timeout: int = 90, redacted: bool = True) -> str:
        if not self.claude_code_path:
            return "Claude Code CLI not found."
        workspace = Path(str(profile.get("workspace") or L.DEFAULT_WORKSPACE))
        workspace.mkdir(parents=True, exist_ok=True)
        process = L.run_capture(
            self.claude_code_path, args, workspace,
            env={"CLAUDE_CONFIG_DIR": str(L.claude_profile_home(profile))}, timeout=timeout,
        )
        parts = [f"Exit code: {process.returncode}"]
        if process.stdout.strip():
            parts.append(process.stdout.strip())
        if process.stderr.strip():
            parts.append(process.stderr.strip())
        output = "\n\n".join(parts)
        return L.redact_auth_output(output) if redacted else output

    def _run_claude_auth_status(self, profile: dict | None = None) -> str:
        target = profile or {"workspace": str(L.DEFAULT_WORKSPACE)}
        return self._run_claude_capture(target, ["auth", "status"], timeout=45)

    def _run_claude_usage_probe(self, profile: dict) -> dict:
        if not self.claude_code_path:
            return {"ok": False, "error": "Claude Code CLI not found."}
        workspace = Path(str(profile.get("workspace") or L.DEFAULT_WORKSPACE))
        workspace.mkdir(parents=True, exist_ok=True)
        process = L.run_capture(
            self.claude_code_path, ["-p", "/usage", "--output-format", "json"], workspace,
            env={"CLAUDE_CONFIG_DIR": str(L.claude_profile_home(profile))}, timeout=45,
        )
        stdout, stderr = process.stdout.strip(), process.stderr.strip()
        if process.returncode != 0:
            return {"ok": False, "error": L.redact_auth_output(stderr or stdout or "Claude usage probe failed.")}
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return {"ok": False, "error": L.redact_auth_output(stdout or stderr or "non-JSON usage output.")}
        usage_text = str(payload.get("result") or "")
        return {"ok": True, "raw": L.redact_auth_output(usage_text), "parsed": L.parse_claude_usage_text(usage_text)}

    def _cursor_agent_json(self, args: list[str], timeout: int = 15) -> dict:
        if not self.cursor_agent_path:
            return {}
        L.DEFAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)
        try:
            process = L.run_capture(self.cursor_agent_path, args, L.DEFAULT_WORKSPACE, timeout=timeout)
        except Exception as error:
            return {"error": str(error)}
        output = process.stdout.strip() or process.stderr.strip()
        if not output:
            return {"exitCode": process.returncode}
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return {"exitCode": process.returncode, "raw": L.redact_auth_output(output)}
        return payload if isinstance(payload, dict) else {"value": payload}

    def _cursor_cli_version(self) -> str:
        if not self.cursor_cli_path:
            return ""
        try:
            process = L.run_capture(self.cursor_cli_path, ["--version"], L.DEFAULT_WORKSPACE, timeout=15)
        except Exception:
            return ""
        return next((line.strip() for line in process.stdout.splitlines() if line.strip()), "")

    # ---------- refresh dispatch (ported from the god-class methods) ----------
    def refresh_profile(self, profile: dict) -> dict:
        provider = L.provider_key(profile)
        if provider == "claude":
            return self._refresh_claude(profile)
        if provider == "cursor":
            return self._refresh_cursor(profile)
        if provider == "antigravity":
            return self._refresh_antigravity(profile)
        if provider != "codex":
            profile["lastLimitsRefreshUtc"] = L.iso_utc_now()
            profile["lastLimitsError"] = f"{L.provider_label(profile)} refresh is not wired yet."
            return {"ok": False, "error": profile["lastLimitsError"]}
        return self._refresh_codex(profile)

    def _refresh_codex(self, profile: dict) -> dict:
        if not self.node_path:
            raise RuntimeError(self.node_error or "Node.js was not found.")
        if not self.codex_cli_path:
            raise RuntimeError(self.codex_cli_error or "codex.exe was not found.")
        if not L.HELPER_PATH.exists():
            raise RuntimeError(f"Missing limits helper: {L.HELPER_PATH}")
        L.ensure_file_credential_store(profile)
        workspace = Path(str(profile.get("workspace") or L.DEFAULT_WORKSPACE))
        workspace.mkdir(parents=True, exist_ok=True)
        process = L.run_capture(
            self.node_path,
            [str(L.HELPER_PATH), self.codex_cli_path, str(profile.get("codexHome")), str(workspace), "read"],
            workspace, timeout=45,
        )
        stdout = process.stdout.strip()
        if not stdout:
            raise RuntimeError(process.stderr.strip() or "No output from limits helper.")
        result = json.loads(stdout)
        L.set_profile_limits_from_result(profile, result)
        return result

    def _refresh_claude(self, profile: dict) -> dict:
        if L.claude_desktop_only(profile):
            status = self.claude_desktop_state_status(profile)
            ready = status.get("state") == "ready"
            profile["lastLimitsRefreshUtc"] = L.iso_utc_now()
            profile["lastLimitsError"] = "" if ready else str(status.get("detail") or "Claude Desktop login not captured.")
            profile["lastUsageError"] = (
                "Claude Desktop-only account. Claude Code limits and usage are unavailable."
            )
            profile["accountType"] = "Claude Desktop"
            profile["shortLimitUsedPercent"] = ""
            profile["shortLimitResetUtc"] = ""
            profile["weeklyLimitUsedPercent"] = ""
            profile["weeklyLimitResetUtc"] = ""
            profile["weeklyResetEstimateUtc"] = ""
            profile["limitReachedType"] = ""
            profile["usageDailyBuckets"] = []
            profile["usageSummary"] = {
                "desktopOnly": True,
                "desktopReady": ready,
                "desktopSummary": status.get("detail") or status.get("label") or "",
                "claudeAuthStatus": {"loggedIn": False, "source": "desktop-only"},
            }
            return {
                "ok": ready,
                "provider": "claude",
                "desktopOnly": True,
                "error": "" if ready else profile["lastLimitsError"],
            }
        desktop = L.claude_desktop_login_status()
        cli_status = self._run_claude_auth_status(profile)
        auth_info = L.parse_claude_auth_status_text(cli_status)
        usage_probe = self._run_claude_usage_probe(profile)
        usage_parsed = usage_probe.get("parsed") if usage_probe.get("ok") and isinstance(usage_probe.get("parsed"), dict) else {}
        previous_summary = profile.get("usageSummary") if isinstance(profile.get("usageSummary"), dict) else {}
        daily_buckets = L.build_claude_usage_buckets(L.claude_profile_home(profile) / "projects")
        profile["lastLimitsRefreshUtc"] = L.iso_utc_now()
        profile["accountPlan"] = str(auth_info.get("subscriptionType") or "")
        profile["accountPlanStatus"] = ""
        profile["accountType"] = str(auth_info.get("authMethod") or auth_info.get("apiProvider") or "")
        profile["accountName"] = str(auth_info.get("orgName") or "")
        profile["accountEmail"] = str(auth_info.get("email") or "")
        session_used = usage_parsed.get("sessionUsedPercent") if isinstance(usage_parsed, dict) else None
        weekly_used = usage_parsed.get("weeklyUsedPercent") if isinstance(usage_parsed, dict) else None
        profile["shortLimitLabel"] = "5h"
        profile["weeklyLimitLabel"] = "Weekly"
        if session_used is not None:
            profile["shortLimitUsedPercent"] = str(session_used)
        if weekly_used is not None:
            profile["weeklyLimitUsedPercent"] = str(weekly_used)
        profile["resetCreditsAvailable"] = ""
        if session_used is not None or weekly_used is not None:
            reached = ""
            if session_used is not None and float(session_used) >= 100:
                reached = "Claude session limit"
            if weekly_used is not None and float(weekly_used) >= 100:
                reached = "Claude weekly limit"
            profile["limitReachedType"] = reached
        session_reset = str(usage_parsed.get("sessionResetUtc") or "")
        if session_reset:
            profile["shortLimitResetUtc"] = session_reset
        # Always re-resolve the weekly reset on refresh so it never displays a
        # stale saved date: CLI-reported value → estimate from recent usage →
        # cleared if a stored estimate has already elapsed.
        weekly_reset, weekly_source = L.resolve_claude_weekly_reset(
            str(usage_parsed.get("weeklyResetUtc") or ""),
            daily_buckets,
            str(profile.get("weeklyResetEstimateUtc") or ""),
        )
        profile["weeklyLimitResetUtc"] = weekly_reset
        profile["weeklyResetEstimateUtc"] = weekly_reset
        profile["weeklyResetEstimateSource"] = weekly_source
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
            "claudeWeeklyModelUsage": usage_parsed.get("weeklyModelUsedPercent") or {},
            "lastRateLimitEvent": previous_summary.get("lastRateLimitEvent") or {},
            "last7dTokens": recent_tokens,
            "last7dMessages": recent_messages,
        }
        profile["lastUsageError"] = str(usage_probe.get("error") or "")
        if desktop.get("ready") or bool(auth_info.get("loggedIn")):
            profile["lastLimitsError"] = ""
            return {"ok": True, "provider": "claude"}
        profile["lastLimitsError"] = str(desktop.get("summary") or "Claude Desktop login not detected.")
        return {"ok": False, "provider": "claude", "error": profile["lastLimitsError"]}

    def _refresh_cursor(self, profile: dict) -> dict:
        desktop_status = L.cursor_local_account_status()
        agent_status = self._cursor_agent_json(["status", "--format", "json"], timeout=20)
        about = self._cursor_agent_json(["about", "--format", "json"], timeout=20)
        version = str(about.get("cliVersion") or "") or L.windows_file_version(self.cursor_desktop_path) or self._cursor_cli_version()
        ready = bool(agent_status.get("isAuthenticated") or desktop_status.get("ready"))
        profile["lastLimitsRefreshUtc"] = L.iso_utc_now()
        profile["accountName"] = str(desktop_status.get("name") or "")
        profile["accountEmail"] = str(agent_status.get("email") or desktop_status.get("email") or "")
        profile["accountPlan"] = str(about.get("subscriptionTier") or desktop_status.get("plan") or "")
        profile["accountPlanStatus"] = str(agent_status.get("status") or desktop_status.get("status") or "")
        profile["accountType"] = str(desktop_status.get("accountType") or "cursor-agent")
        profile["providerVersion"] = version
        profile["usageSummary"] = {
            "providerVersion": version,
            "desktopPath": self.cursor_desktop_path,
            "cliPath": self.cursor_cli_path,
            "agentPath": self.cursor_agent_path,
            "accountSummary": desktop_status.get("summary") or agent_status.get("message") or "",
            "membershipType": profile["accountPlan"],
            "subscriptionStatus": profile["accountPlanStatus"],
            "cursorAgentStatus": agent_status,
            "cursorAgentAbout": about,
        }
        profile["lastUsageError"] = "Cursor quota is not exposed by Cursor CLI."
        if not self.cursor_desktop_path and not self.cursor_cli_path and not self.cursor_agent_path:
            profile["lastLimitsError"] = "Cursor is not installed."
            return {"ok": False, "provider": "cursor", "error": profile["lastLimitsError"]}
        if not ready:
            probe_failed = bool(agent_status.get("error")) or bool(agent_status.get("raw")) or ("exitCode" in agent_status and "isAuthenticated" not in agent_status)
            profile["lastLimitsError"] = (
                f"Cursor Agent status check failed: {agent_status.get('error') or agent_status.get('raw') or 'unexpected response'}"
                if probe_failed else str(agent_status.get("message") or desktop_status.get("summary") or "Cursor login not detected.")
            )
            return {"ok": False, "provider": "cursor", "error": profile["lastLimitsError"], "probeFailed": probe_failed}
        profile["lastLimitsError"] = ""
        return {"ok": True, "provider": "cursor"}

    def _refresh_antigravity(self, profile: dict) -> dict:
        status = L.antigravity_local_account_status()
        version = L.windows_file_version(self.antigravity_desktop_path)
        cli_probe = self.antigravity_cli_diagnostics or L.antigravity_cli_diagnostics(self.antigravity_cli_path)
        profile["antigravityPrintTimeout"] = L.normalize_antigravity_print_timeout(profile.get("antigravityPrintTimeout"))
        profile["lastLimitsRefreshUtc"] = L.iso_utc_now()
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
            "cliState": str(cli_probe.get("state") or ""),
            "cliLabel": L.antigravity_cli_label(cli_probe),
            "cliDetail": str(cli_probe.get("detail") or ""),
            "cliVersion": str(cli_probe.get("version") or ""),
            "printTimeout": profile["antigravityPrintTimeout"],
            "accountSummary": status.get("summary") or "",
            "profileUrl": status.get("profileUrl") or "",
        }
        profile["lastUsageError"] = "Antigravity quota is not reliably exposed yet."
        if not self.antigravity_desktop_path and not self.antigravity_cli_path:
            profile["lastLimitsError"] = "Antigravity is not installed."
            return {"ok": False, "provider": "antigravity", "error": profile["lastLimitsError"]}
        if not status.get("ready"):
            profile["lastLimitsError"] = str(status.get("summary") or "Antigravity login not detected.")
            return {"ok": False, "provider": "antigravity", "error": profile["lastLimitsError"]}
        profile["lastLimitsError"] = ""
        return {"ok": True, "provider": "antigravity"}

    # ---------- account actions (ported from the Tk god-class methods) ----------
    # Each returns (ok: bool, message: str). The Qt UI logs the message and, on
    # failure, may show it in a dialog. All heavy lifting reuses legacy helpers.
    def _pwsh(self, title: str, script: str, workspace: Path) -> bool:
        import subprocess
        full = f"$Host.UI.RawUI.WindowTitle = {L.quote_ps(title)}\nSet-Location -LiteralPath {L.quote_ps(workspace)}\n{script}"
        try:
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", full],
                cwd=str(workspace), creationflags=getattr(L, "CREATE_NEW_CONSOLE", 0),
            )
            return True
        except OSError:
            return False

    def _workspace(self, profile: dict) -> Path:
        ws = Path(str(profile.get("workspace") or L.DEFAULT_WORKSPACE))
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    def action_login(self, profile: dict, device: bool = False) -> tuple[bool, str]:
        provider = L.provider_key(profile)
        name = profile.get("name", "Account")
        ws = self._workspace(profile)
        if provider == "claude":
            if L.claude_desktop_only(profile):
                return False, (
                    f"{name} is a Claude Desktop-only profile. "
                    "Use Desktop Login; Claude Code login requires a paid Claude Code account."
                )
            if device:
                return True, "Claude Code uses Login (not Device). Use Login."
            if not self.claude_code_path:
                return False, "Claude Code CLI was not found."
            script = ('Write-Host "Claude Code auth login"\n'
                      f"$env:CLAUDE_CONFIG_DIR = {L.quote_ps(L.claude_profile_home(profile))}\n"
                      f"& {L.quote_ps(self.claude_code_path)} auth login\n"
                      'Write-Host ""\nWrite-Host "Login finished. Use Refresh or Status to verify."\n')
            return self._pwsh(f"Claude Code login - {name}", script, ws), f"Opened Claude Code login for {name}."
        if provider == "cursor":
            if self.cursor_agent_path:
                script = ('Write-Host "Cursor Agent login"\n'
                          f"& {L.quote_ps(self.cursor_agent_path)} login\n"
                          'Write-Host ""\nWrite-Host "Login finished. Use Refresh or Status to verify."\n')
                return self._pwsh(f"Cursor Agent login - {name}", script, ws), f"Opened Cursor Agent login for {name}."
            self.action_desktop(profile)
            return True, "Sign in inside Cursor, then Refresh."
        if provider == "antigravity":
            self.action_desktop(profile)
            return True, "Sign in inside Antigravity, then Refresh."
        # codex
        if not self.codex_cli_path:
            return False, self.codex_cli_error or "codex.exe was not found."
        L.ensure_file_credential_store(profile)
        device_arg = " --device-auth" if device else ""
        script = (f"$env:CODEX_HOME = {L.quote_ps(profile.get('codexHome'))}\n"
                  'Write-Host "CODEX_HOME=$env:CODEX_HOME"\n'
                  f"& {L.quote_ps(self.codex_cli_path)} login{device_arg}\n"
                  'Write-Host ""\nWrite-Host "Login finished. Use Refresh or Status to verify."\n')
        return self._pwsh(f"Codex login - {name}", script, ws), f"Opened login window for {name}."

    def action_logout(self, profile: dict) -> tuple[bool, str]:
        if L.provider_key(profile) != "cursor":
            return False, f"{L.provider_label(profile)} has no logout action wired up."
        if not self.cursor_agent_path:
            return False, "Cursor Agent CLI was not found."
        ws = self._workspace(profile)
        script = ('Write-Host "Cursor Agent logout"\n'
                  f"& {L.quote_ps(self.cursor_agent_path)} logout\n"
                  'Write-Host ""\nWrite-Host "Logout finished."\n')
        return self._pwsh(f"Cursor Agent logout - {profile.get('name','Account')}", script, ws), "Opened Cursor logout."

    def action_cli(self, profile: dict) -> tuple[bool, str]:
        provider = L.provider_key(profile)
        name = profile.get("name", "Account")
        ws = self._workspace(profile)
        if provider == "claude":
            if L.claude_desktop_only(profile):
                return False, (
                    f"{name} is a Claude Desktop-only profile. "
                    "Claude Code CLI is disabled for this account."
                )
            if not self.claude_code_path:
                return False, "Claude Code CLI was not found."
            script = ('Write-Host "Claude Code"\n'
                      f"$env:CLAUDE_CONFIG_DIR = {L.quote_ps(L.claude_profile_home(profile))}\n"
                      f"& {L.quote_ps(self.claude_code_path)}\n")
            return self._pwsh(f"Claude Code - {name}", script, ws), f"Opened Claude Code CLI for {name}."
        if provider == "cursor":
            if self.cursor_agent_path:
                script = f'Write-Host "Cursor Agent"\n& {L.quote_ps(self.cursor_agent_path)} --workspace {L.quote_ps(ws)}\n'
                return self._pwsh(f"Cursor Agent - {name}", script, ws), f"Opened Cursor Agent for {name}."
            if self.cursor_cli_path:
                script = f'Write-Host "Cursor"\n& {L.quote_ps(self.cursor_cli_path)} {L.quote_ps(ws)}\n'
                return self._pwsh(f"Cursor CLI - {name}", script, ws), f"Opened Cursor CLI for {name}."
            return False, "Cursor Agent and cursor.cmd were not found."
        if provider == "antigravity":
            if self.antigravity_cli_path and Path(self.antigravity_cli_path).name.lower() != "agy-node.cmd":
                script = f'Write-Host "Antigravity CLI"\n& {L.quote_ps(self.antigravity_cli_path)}\n'
                return self._pwsh(f"Antigravity CLI - {name}", script, ws), f"Opened Antigravity CLI for {name}."
            if self.antigravity_desktop_path:
                self.action_desktop(profile)
                return True, "Antigravity CLI shim unavailable; opened desktop."
            return False, "Antigravity desktop/CLI was not found."
        # codex
        if not self.codex_cli_path:
            return False, self.codex_cli_error or "codex.exe was not found."
        L.ensure_file_credential_store(profile)
        script = (f"$env:CODEX_HOME = {L.quote_ps(profile.get('codexHome'))}\n"
                  'Write-Host "CODEX_HOME=$env:CODEX_HOME"\n'
                  f"& {L.quote_ps(self.codex_cli_path)}\n")
        return self._pwsh(f"Codex CLI - {name}", script, ws), f"Opened CLI for {name}."

    def action_desktop(self, profile: dict) -> tuple[bool, str]:
        import subprocess
        provider = L.provider_key(profile)
        name = profile.get("name", "Account")
        ws = self._workspace(profile)
        try:
            if provider == "claude":
                return self.claude_switch_desktop(profile)
            elif provider == "cursor":
                target = self.cursor_desktop_path or self.cursor_cli_path
                if not target:
                    return False, "Cursor desktop/CLI was not found."
                subprocess.Popen([target, str(ws)], cwd=str(ws))
            elif provider == "antigravity":
                if not self.antigravity_desktop_path:
                    return False, "Antigravity.exe was not found."
                subprocess.Popen([self.antigravity_desktop_path], cwd=str(Path(self.antigravity_desktop_path).parent))
            else:
                # codex desktop switch is a distinct, heavier flow; open codex home for now
                return self.action_home(profile)
        except OSError as error:
            return False, f"Could not open desktop: {error}"
        return True, f"Opened {L.provider_label(profile)} for {name}."

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

    # ---------- Codex desktop switch (ported process/auth flow) ----------
    def _active_desktop_marker(self) -> dict:
        import json
        try:
            return json.loads(L.DESKTOP_ACTIVE_PROFILE_PATH.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}

    def _stop_codex_desktop(self) -> str:
        script = r"""
function Get-CodexDesktopProcess {
    $items = @()
    foreach ($process in @(Get-Process -ErrorAction SilentlyContinue)) {
        $path = ""
        try { $path = [string]$process.Path } catch { $path = "" }
        if ($path -match '\\WindowsApps\\OpenAI\.Codex_' -and $path -match '\\app\\(Codex|resources\\codex)\.exe$') { $items += $process }
    }
    return @($items)
}
$matches = @(Get-CodexDesktopProcess)
if ($matches.Count -eq 0) { Write-Output "No Codex Desktop background processes were running."; exit 0 }
$closed = 0
foreach ($p in $matches) { try { if ($p.ProcessName -eq "Codex" -and $p.MainWindowHandle -ne [IntPtr]::Zero) { if ($p.CloseMainWindow()) { $closed++ } } } catch {} }
$deadline = [DateTime]::Now.AddSeconds(12)
do { Start-Sleep -Milliseconds 250; $remaining = @(Get-CodexDesktopProcess) } while ($remaining.Count -gt 0 -and [DateTime]::Now -lt $deadline)
$killed = 0
foreach ($p in $remaining) { try { $p.Kill(); $killed++ } catch {} }
Write-Output "Stopped $($matches.Count) Codex Desktop process(es). Graceful: $closed. Force: $killed."
"""
        proc = L.run_capture("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], L.DEFAULT_WORKSPACE, timeout=25)
        return proc.stdout.strip() or proc.stderr.strip() or "Desktop process check completed."

    def _sync_active_back(self) -> str:
        import shutil
        if not L.DESKTOP_ACTIVE_PROFILE_PATH.exists():
            return ""
        default_auth = L.default_auth_path()
        if not default_auth.exists():
            return "No default desktop auth to save back."
        marker = self._active_desktop_marker()
        home = str(marker.get("codexHome") or "").strip()
        if not home:
            return "Previous marker had no CODEX_HOME; skipping save-back."
        target = Path(home)
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(default_auth, target / "auth.json")
        return f"Saved current desktop auth back to {marker.get('name') or 'previous profile'}."

    def _clear_default_auth(self) -> str:
        import datetime as dt
        import shutil
        L.ensure_file_credential_store({"codexHome": str(L.DEFAULT_CODEX_HOME), "workspace": str(L.DEFAULT_WORKSPACE)})
        auth_path = L.default_auth_path()
        backed = ""
        if auth_path.exists():
            L.DESKTOP_BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            bp = L.DESKTOP_BACKUP_ROOT / f"auth-before-local-clear-{stamp}.json"
            shutil.copy2(auth_path, bp)
            backed = str(bp)
            auth_path.unlink()
        if L.DESKTOP_ACTIVE_PROFILE_PATH.exists():
            L.DESKTOP_ACTIVE_PROFILE_PATH.unlink()
        return f"Cleared default Codex desktop auth locally (no token revoked). Backup: {backed or 'none'}."

    def _sync_profile_to_default(self, profile: dict) -> str:
        import json
        import shutil
        L.ensure_file_credential_store(profile)
        L.ensure_file_credential_store({"codexHome": str(L.DEFAULT_CODEX_HOME), "workspace": str(profile.get("workspace") or L.DEFAULT_WORKSPACE)})
        profile_auth = L.profile_auth_path(profile)
        if not profile_auth.exists():
            raise RuntimeError(f"No auth.json for {profile.get('name','Account')}. Run Login first.")
        L.DEFAULT_CODEX_HOME.mkdir(parents=True, exist_ok=True)
        L.DESKTOP_BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        backup_auth = L.DESKTOP_BACKUP_ROOT / "auth.json"
        current = L.default_auth_path()
        if current.exists() and not backup_auth.exists():
            shutil.copy2(current, backup_auth)
        shutil.copy2(profile_auth, current)
        marker = {"name": str(profile.get("name", "Account")), "codexHome": str(profile.get("codexHome")), "syncedAtUtc": L.iso_utc_now()}
        L.DESKTOP_ACTIVE_PROFILE_PATH.write_text(json.dumps(marker, indent=2), encoding="utf-8")
        return f"Synced {profile.get('name','Account')} auth into the default Codex desktop home."

    def _start_codex_desktop(self, profile: dict) -> None:
        import os
        import subprocess
        ws = self._workspace(profile)
        env = os.environ.copy()
        env["CODEX_HOME"] = str(profile.get("codexHome"))
        subprocess.Popen([self.codex_cli_path, "app", str(ws)], cwd=str(ws), env=env, creationflags=getattr(L, "CREATE_NO_WINDOW", 0))

    def codex_switch_desktop(self, profile: dict) -> tuple[bool, str]:
        if L.provider_key(profile) != "codex":
            return self.action_desktop(profile)
        if not self.codex_cli_path:
            return False, self.codex_cli_error or "codex.exe was not found."
        if not L.has_profile_auth(profile):
            return False, f"No auth.json for {profile.get('name','Account')}. Use Login first."
        lines = []
        lines.append(self._stop_codex_desktop())
        back = self._sync_active_back()
        if back:
            lines.append(back)
        lines.append(self._clear_default_auth())
        lines.append(self._sync_profile_to_default(profile))
        self._start_codex_desktop(profile)
        lines.append(f"Switched Codex Desktop to {profile.get('name','Account')} and requested relaunch.")
        return True, "\n".join(lines)

    def codex_dry_run(self, profile: dict) -> tuple[bool, str]:
        if L.provider_key(profile) != "codex":
            return False, "Desktop switch dry-run is Codex-only."
        auth_path = L.profile_auth_path(profile)
        marker = self._active_desktop_marker()
        lines = [
            f"Dry run for Codex Desktop switch to {profile.get('name','Account')}",
            f"Selected CODEX_HOME: {profile.get('codexHome')}",
            f"Profile auth exists: {auth_path.exists()} ({auth_path})",
            f"Default desktop auth exists: {L.default_auth_path().exists()}",
            f"Active marker: {marker.get('name') or 'none'}",
            f"Codex CLI: {self.codex_cli_path or 'not found'}",
            "",
            "Planned: verify login, stop processes, save-back, clear+backup default, copy profile auth, relaunch.",
        ]
        return True, "\n".join(lines)

    def codex_restore_backup(self) -> tuple[bool, str]:
        import datetime as dt
        import shutil
        primary = L.DESKTOP_BACKUP_ROOT / "auth.json"
        backups = [primary] if primary.exists() else []
        backups.extend(sorted(L.DESKTOP_BACKUP_ROOT.glob("auth-before-local-clear-*.json"), key=lambda p: p.stat().st_mtime, reverse=True))
        backup = next((p for p in backups if p.exists()), None)
        if backup is None:
            return False, f"No default desktop auth backup found under {L.DESKTOP_BACKUP_ROOT}."
        L.ensure_file_credential_store({"codexHome": str(L.DEFAULT_CODEX_HOME), "workspace": str(L.DEFAULT_WORKSPACE)})
        L.DEFAULT_CODEX_HOME.mkdir(parents=True, exist_ok=True)
        current = L.default_auth_path()
        if current.exists():
            L.DESKTOP_BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(current, L.DESKTOP_BACKUP_ROOT / f"auth-before-restore-{stamp}.json")
        shutil.copy2(backup, current)
        if L.DESKTOP_ACTIVE_PROFILE_PATH.exists():
            L.DESKTOP_ACTIVE_PROFILE_PATH.unlink()
        return True, f"Restored default Codex desktop auth from {backup}. Active marker cleared."

    # ---------- persistence + history (pure module reuse) ----------
    def record_history(self, profile: dict, reason: str) -> None:
        try:
            L.record_profile_history(profile, refresh_reason=reason)
        except Exception:
            pass

    def save(self, profiles: list[dict]) -> None:
        L.save_profiles(profiles)


def _safe(fn) -> str:
    try:
        return fn() or ""
    except Exception:
        return ""
