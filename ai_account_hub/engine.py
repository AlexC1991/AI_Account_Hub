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
from pathlib import Path

from ai_account_hub import core as L

_logger = logging.getLogger(__name__)


from ai_account_hub.engine_claude_desktop import _ClaudeDesktopMixin


class HubEngine(_ClaudeDesktopMixin):
    def __init__(self) -> None:
        self.codex_cli_path = ""
        self.codex_desktop_path = ""
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
            self.codex_desktop_path = path_for("codex", "desktop")
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
            self.codex_desktop_path = ""
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
                return self.codex_switch_desktop(profile)
        except OSError as error:
            return False, f"Could not open desktop: {error}"
        return True, f"Opened {L.provider_label(profile)} for {name}."

    # ---------- Codex desktop switch (ported process/auth flow) ----------
    def _active_desktop_marker(self) -> dict:
        import json
        try:
            return json.loads(L.DESKTOP_ACTIVE_PROFILE_PATH.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}

    def _stop_codex_desktop(self) -> str:
        script = r"""
$packageRoots = @()
try {
    $packageRoots = @(
        Get-AppxPackage -Name OpenAI.Codex -ErrorAction SilentlyContinue |
            ForEach-Object { [string]$_.InstallLocation } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
} catch {}

function Test-CodexDesktopPath([string]$ExecutablePath) {
    if ([string]::IsNullOrWhiteSpace($ExecutablePath)) { return $false }

    # The desktop host was originally Codex.exe and is ChatGPT.exe in newer
    # builds. Match the stable OpenAI.Codex package app directory so future
    # host renames do not break account switching again.
    foreach ($root in $packageRoots) {
        try {
            $appRoot = [IO.Path]::GetFullPath((Join-Path $root "app")).TrimEnd('\') + '\'
            $fullPath = [IO.Path]::GetFullPath($ExecutablePath)
            if ($fullPath.StartsWith($appRoot, [StringComparison]::OrdinalIgnoreCase)) {
                return $true
            }
        } catch {}
    }

    # Keep a fallback for systems where Get-AppxPackage cannot return the
    # install location to the current process.
    return $ExecutablePath -match '\\WindowsApps\\OpenAI\.Codex_[^\\]+\\app\\'
}

function Get-CodexDesktopProcess {
    $items = @()
    foreach ($process in @(Get-Process -ErrorAction SilentlyContinue)) {
        $path = ""
        try { $path = [string]$process.Path } catch { $path = "" }
        if (Test-CodexDesktopPath $path) { $items += $process }
    }
    return @($items)
}
$matches = @(Get-CodexDesktopProcess)
if ($matches.Count -eq 0) { Write-Output "No Codex Desktop background processes were running."; exit 0 }
$closed = 0
foreach ($p in $matches) {
    try {
        if ($p.MainWindowHandle -ne [IntPtr]::Zero -and $p.CloseMainWindow()) { $closed++ }
    } catch {}
}
$deadline = [DateTime]::Now.AddSeconds(12)
do { Start-Sleep -Milliseconds 250; $remaining = @(Get-CodexDesktopProcess) } while ($remaining.Count -gt 0 -and [DateTime]::Now -lt $deadline)
$killed = 0
foreach ($p in $remaining) { try { $p.Kill(); $killed++ } catch {} }
$killDeadline = [DateTime]::Now.AddSeconds(3)
do { Start-Sleep -Milliseconds 100; $stillRunning = @(Get-CodexDesktopProcess) } while ($stillRunning.Count -gt 0 -and [DateTime]::Now -lt $killDeadline)
Write-Output "Stopped $($matches.Count) Codex Desktop process(es). Graceful: $closed. Force: $killed. Remaining: $($stillRunning.Count)."
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

    def _codex_desktop_activation_info(self) -> dict[str, str]:
        """Resolve the installed Store app through its current manifest.

        Codex Desktop's host executable has changed from ``Codex.exe`` to
        ``ChatGPT.exe``. The package family + application ID is the stable
        Windows activation contract, so do not infer the launcher from a host
        filename or route through ``codex app`` (which can invoke installation).
        """

        script = r"""
$ErrorActionPreference = "Stop"
$pkg = Get-AppxPackage -Name OpenAI.Codex -ErrorAction Stop |
    Sort-Object Version -Descending |
    Select-Object -First 1
if ($null -eq $pkg) { throw "The installed OpenAI.Codex package was not found." }
$manifest = Get-AppxPackageManifest -Package $pkg
$app = @($manifest.Package.Applications.Application) |
    Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_.Id) } |
    Select-Object -First 1
if ($null -eq $app) { throw "OpenAI.Codex has no launchable application in its manifest." }
[pscustomobject]@{
    target = "shell:AppsFolder\$($pkg.PackageFamilyName)!$($app.Id)"
    installLocation = [string]$pkg.InstallLocation
    executable = [string]$app.Executable
    packageFullName = [string]$pkg.PackageFullName
} | ConvertTo-Json -Compress
"""
        process = L.run_capture(
            "powershell.exe",
            ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            L.DEFAULT_WORKSPACE,
            timeout=12,
        )
        output = process.stdout.strip()
        if process.returncode != 0 or not output:
            raise RuntimeError(
                process.stderr.strip()
                or output
                or "Could not resolve the installed Codex Desktop package."
            )
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as error:
            raise RuntimeError("Codex Desktop package lookup returned invalid data.") from error
        info = {
            "target": str(payload.get("target") or "").strip(),
            "installLocation": str(payload.get("installLocation") or "").strip(),
            "executable": str(payload.get("executable") or "").strip(),
            "packageFullName": str(payload.get("packageFullName") or "").strip(),
        }
        if not info["target"].lower().startswith("shell:appsfolder\\"):
            raise RuntimeError("Codex Desktop package did not expose a valid activation ID.")
        if not info["installLocation"]:
            raise RuntimeError("Codex Desktop package did not expose its install location.")
        return info

    def _start_codex_desktop(self, activation: dict[str, str]) -> str:
        target = str(activation.get("target") or "").strip()
        install_location = str(activation.get("installLocation") or "").strip()
        package_name = str(activation.get("packageFullName") or "OpenAI.Codex").strip()
        script = f"""
$ErrorActionPreference = "Stop"
$target = {L.quote_ps(target)}
$installLocation = {L.quote_ps(install_location)}
$packageName = {L.quote_ps(package_name)}
$appRoot = [IO.Path]::GetFullPath((Join-Path $installLocation "app")).TrimEnd('\\') + '\\'
Start-Process -FilePath "explorer.exe" -ArgumentList $target | Out-Null

function Get-CodexDesktopProcess {{
    $items = @()
    foreach ($process in @(Get-Process -ErrorAction SilentlyContinue)) {{
        $path = ""
        try {{ $path = [string]$process.Path }} catch {{ $path = "" }}
        if ([string]::IsNullOrWhiteSpace($path)) {{ continue }}
        try {{
            $fullPath = [IO.Path]::GetFullPath($path)
            if ($fullPath.StartsWith($appRoot, [StringComparison]::OrdinalIgnoreCase)) {{
                $items += $process
            }}
        }} catch {{}}
    }}
    return @($items)
}}

$deadline = [DateTime]::Now.AddSeconds(12)
do {{
    Start-Sleep -Milliseconds 200
    $running = @(Get-CodexDesktopProcess)
}} while ($running.Count -eq 0 -and [DateTime]::Now -lt $deadline)
if ($running.Count -eq 0) {{
    throw "Windows accepted the Codex Desktop activation request, but no app process started."
}}
Write-Output "Opened installed Codex Desktop package $packageName."
"""
        process = L.run_capture(
            "powershell.exe",
            ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            L.DEFAULT_WORKSPACE,
            timeout=18,
        )
        output = process.stdout.strip()
        if process.returncode != 0:
            raise RuntimeError(
                process.stderr.strip()
                or output
                or "Codex Desktop did not reopen after the account switch."
            )
        return output or f"Opened installed Codex Desktop package {package_name}."

    def codex_switch_desktop(self, profile: dict) -> tuple[bool, str]:
        if L.provider_key(profile) != "codex":
            return self.action_desktop(profile)
        if not L.has_profile_auth(profile):
            return False, f"No auth.json for {profile.get('name','Account')}. Use Login first."
        try:
            activation = self._codex_desktop_activation_info()
        except Exception as error:
            return False, f"Could not prepare Codex Desktop: {error}"
        lines = []
        lines.append(self._stop_codex_desktop())
        back = self._sync_active_back()
        if back:
            lines.append(back)
        lines.append(self._clear_default_auth())
        lines.append(self._sync_profile_to_default(profile))
        try:
            lines.append(self._start_codex_desktop(activation))
        except Exception as error:
            lines.append(f"Codex Desktop relaunch failed: {error}")
            return False, "\n".join(lines)
        lines.append(f"Switched Codex Desktop to {profile.get('name','Account')}.")
        return True, "\n".join(lines)

    def codex_dry_run(self, profile: dict) -> tuple[bool, str]:
        if L.provider_key(profile) != "codex":
            return False, "Desktop switch dry-run is Codex-only."
        auth_path = L.profile_auth_path(profile)
        marker = self._active_desktop_marker()
        try:
            activation = self._codex_desktop_activation_info()
            desktop_target = activation.get("target") or "not found"
            desktop_executable = activation.get("executable") or "not exposed"
        except Exception as error:
            desktop_target = f"not available ({error})"
            desktop_executable = "-"
        lines = [
            f"Dry run for Codex Desktop switch to {profile.get('name','Account')}",
            f"Selected CODEX_HOME: {profile.get('codexHome')}",
            f"Profile auth exists: {auth_path.exists()} ({auth_path})",
            f"Default desktop auth exists: {L.default_auth_path().exists()}",
            f"Active marker: {marker.get('name') or 'none'}",
            f"Codex CLI: {self.codex_cli_path or 'not found'}",
            f"Codex Desktop activation: {desktop_target}",
            f"Codex Desktop executable: {desktop_executable}",
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
