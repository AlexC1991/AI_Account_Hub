"""Native coding passthrough for the Qt view.

Reuses the Tk-free ``native_harness`` transports directly (CodexTransport /
StreamJsonTransport / AntigravityTransport). The transports invoke an
``event_callback(message: dict)`` from their own worker threads; this bridge
marshals every such message onto the Qt thread through a Signal, then turns the
provider's native events into UI updates. No agent loop of our own -- exactly
like the Tk app, this is a thin passthrough.

This first cut handles the core streaming path: start/resume a session, send a
turn, stream assistant text + activity lines, and completion/errors. Rich
block types, approvals, and per-provider composer nuances build on top of the
same signal without changing the transport wiring.
"""

from __future__ import annotations

import http.server
import json
import os
import secrets
import sys
import threading
import re
import datetime as dt
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ai_account_hub import data
from ai_account_hub import core as L


class CodingBridge(QObject):
    # UI-thread signals
    assistant_delta = Signal(str, str)   # native_id, text
    activity = Signal(str)               # one-line activity/notice
    block = Signal(dict)                  # structured rich block (command/plan/diff/tool/thinking)
    turn_started = Signal()
    turn_finished = Signal()
    error = Signal(str)
    session_ready = Signal(str)          # thread/session id
    rate_limits_changed = Signal()
    skills_ready = Signal(list)
    approval_request = Signal(dict)      # {request_id, kind, subject}

    claude_permission = Signal(str)      # pending key; UI answers via answer_permission()

    def __init__(self) -> None:
        super().__init__()
        self._transport = None
        self._profile: dict | None = None
        self._busy = False
        self._turn_active = False
        self._session_id = ""
        self._transport_key = ""
        self._last_title = ""
        self._perm_server = None
        self._perm_url = ""
        self._perm_token = ""
        self._pending_perms: dict = {}

    # ---------- Claude permission bridge (loopback HTTP, reused subprocess) ----------
    def _ensure_permission_bridge(self) -> tuple[str, str]:
        if self._perm_server is not None and self._perm_url:
            return self._perm_url, self._perm_token
        token = secrets.token_urlsafe(24)
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):
                return

            def _json(self, status, payload):
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path != "/permission" or self.headers.get("Authorization", "") != f"Bearer {token}":
                    self._json(403, {"behavior": "deny", "message": "Invalid permission request."})
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(size).decode("utf-8", "replace"))
                except Exception:
                    self._json(400, {"behavior": "deny", "message": "Invalid payload."})
                    return
                decision = owner._await_permission(payload if isinstance(payload, dict) else {})
                self._json(200, decision)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, name="qt-claude-permission", daemon=True).start()
        self._perm_server = server
        self._perm_token = token
        self._perm_url = f"http://127.0.0.1:{server.server_address[1]}/permission"
        return self._perm_url, self._perm_token

    def _await_permission(self, payload: dict) -> dict:
        # Runs on the HTTP thread; marshal to UI and block until answered.
        key = secrets.token_hex(8)
        event = threading.Event()
        self._pending_perms[key] = {"payload": payload, "event": event, "result": None}
        self.claude_permission.emit(key)
        if not event.wait(timeout=300):
            self._pending_perms.pop(key, None)
            return _deny(payload, "Timed out waiting for AI Account Hub permission response.")
        entry = self._pending_perms.pop(key, None)
        return (entry or {}).get("result") or _deny(payload, "No permission response.")

    def permission_payload(self, key: str) -> dict:
        entry = self._pending_perms.get(key)
        return (entry or {}).get("payload") or {}

    def answer_permission(self, key: str, decision: dict) -> None:
        entry = self._pending_perms.get(key)
        if entry is not None:
            entry["result"] = decision
            entry["event"].set()

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def session_id(self) -> str:
        if self._transport is not None:
            value = getattr(self._transport, "thread_id", "") or getattr(self._transport, "session_id", "")
            if value:
                self._session_id = str(value)
        return self._session_id

    def set_session(self, profile: dict, session_id: str) -> None:
        session_id = str(session_id or "")
        key = self._profile_key(profile)
        if key != self._transport_key or session_id != self.session_id:
            self._close_transport()
        self._profile = profile
        self._transport_key = key
        self._session_id = session_id

    def reset_session(self) -> None:
        self._close_transport()
        self._session_id = ""
        self._transport_key = ""
        self._profile = None

    def close(self) -> None:
        self.reset_session()
        server = self._perm_server
        self._perm_server = None
        self._perm_url = ""
        self._perm_token = ""
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
        for entry in list(self._pending_perms.values()):
            entry["result"] = _deny(entry.get("payload") or {}, "AI Account Hub closed.")
            entry["event"].set()
        self._pending_perms.clear()

    def _close_transport(self) -> None:
        t = self._transport
        self._transport = None
        if t is not None:
            try:
                if hasattr(t, "shutdown"):
                    t.shutdown()
                else:
                    t.stop()
            except Exception:
                pass
        self._busy = False
        self._turn_active = False

    # ---------- event marshaling (called from transport worker threads) ----------
    def _on_event(self, message: dict) -> None:
        # Runs on a transport thread -> emit signals (queued to the UI thread).
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        # Server->client request (Codex native approvals / user input)
        if message.get("id") is not None and method:
            self._handle_server_request(message)
            return
        if method == "item/agentMessage/delta":
            self.assistant_delta.emit(str(params.get("itemId") or "agent"), str(params.get("delta") or ""))
        elif method == "item/reasoning/summaryTextDelta":
            self.block.emit({"id": str(params.get("itemId") or "reasoning"), "kind": "thinking",
                             "title": "Thinking", "append": str(params.get("delta") or "")})
        elif method == "item/commandExecution/outputDelta":
            self.block.emit({"id": str(params.get("itemId") or "command"), "kind": "command",
                             "title": "Command", "append": str(params.get("delta") or "")})
        elif method in {"item/started", "item/completed"}:
            self._emit_codex_item(params)
        elif method == "turn/plan/updated":
            self._emit_plan(params)
        elif method == "turn/diff/updated":
            diff = str(params.get("diff") or "")
            if diff.strip():
                self.block.emit({"id": "active-diff", "kind": "diff", "title": "Current diff", "diff": diff})
        elif method == "turn/started":
            self.turn_started.emit()
        elif method == "turn/completed":
            self._capture_session()
            self._save_thread_ref()
            self._finish_turn()
        elif method == "stream/event":
            self._handle_stream_event(params)
        elif method == "stream/rawOutput":
            text = str(params.get("text") or "").strip()
            if text:
                self.activity.emit(text[:1000])
        elif method == "transport/stderr":
            text = str(params.get("text") or "").strip()
            if text:
                self.activity.emit(text[:1000])
        elif method == "transport/exited":
            self._capture_session()
            self._save_thread_ref()
            code = params.get("exitCode")
            if code not in (None, 0) and not params.get("stopped"):
                self.error.emit(str(params.get("stderr") or f"Process exited with code {code}."))
            self._finish_turn()
        elif method == "error":
            self.error.emit(_err_text(params))
            self._finish_turn()

    def _finish_turn(self) -> None:
        if not self._turn_active and not self._busy:
            return
        self._busy = False
        self._turn_active = False
        self.turn_finished.emit()

    def _capture_session(self) -> None:
        session_id = self.session_id
        if session_id:
            self.session_ready.emit(session_id)

    def _handle_server_request(self, message: dict) -> None:
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        request_id = message.get("id")
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            command = str(params.get("command") or "")
            reason = str(params.get("reason") or "")
            subject = command or reason or ("Apply file changes" if "fileChange" in method else "Run native command")
            self.approval_request.emit({"request_id": request_id, "kind": "approval", "subject": subject})
        elif method == "item/tool/requestUserInput":
            questions = params.get("questions") if isinstance(params.get("questions"), list) else []
            prompt = ""
            if questions and isinstance(questions[0], dict):
                prompt = str(questions[0].get("question") or questions[0].get("header") or "Input required")
            self.approval_request.emit({"request_id": request_id, "kind": "input", "subject": prompt, "questions": questions})
        else:
            # auto-decline unsupported server requests so the provider isn't left hanging
            self.respond_error(request_id, f"Unsupported request: {method}")

    def respond_approval(self, request_id, decision: str) -> None:
        t = self._transport
        if t is None:
            return
        try:
            t.respond(request_id, {"decision": decision})
        except Exception:
            pass

    def respond_input(self, request_id, questions: list, value: str | None) -> None:
        t = self._transport
        if t is None:
            return
        answers = {}
        for q in questions or []:
            if isinstance(q, dict):
                answers[str(q.get("id") or "")] = {"answers": [] if value is None else [value]}
        try:
            t.respond(request_id, {"answers": answers})
        except Exception:
            pass

    def respond_error(self, request_id, message: str) -> None:
        t = self._transport
        if t is not None and hasattr(t, "respond_error"):
            try:
                t.respond_error(request_id, message)
            except Exception:
                pass

    def _emit_codex_item(self, params: dict) -> None:
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        itype = str(item.get("type") or "")
        rich = {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall",
                "collabToolCall", "webSearch", "imageView", "contextCompaction", "plan"}
        if itype not in rich:
            return
        nh = data.native()
        try:
            summary = str(item.get("text") or "") if itype == "plan" else nh.summarize_codex_item(item)
        except Exception:
            summary = ""
        kind = {"commandExecution": "command", "fileChange": "diff", "plan": "plan"}.get(itype, "tool")
        block = {"id": str(item.get("id") or itype), "kind": kind,
                 "title": {"commandExecution": "Command", "fileChange": "File changes", "plan": "Plan"}.get(itype, str(item.get("tool") or "Tool")),
                 "status": str(item.get("status") or ""), "body": summary}
        if itype == "fileChange":
            changes = [c for c in (item.get("changes") or []) if isinstance(c, dict)]
            diffs = [str(c.get("diff") or "") for c in changes if str(c.get("diff") or "").strip()]
            block["diff"] = "\n".join(diffs)
            block["files"] = [str(c.get("path") or "") for c in changes]
        image_refs = []
        try:
            image_refs = nh.codex_content_image_refs(item.get("content"))
        except Exception:
            image_refs = []
        if image_refs:
            block["kind"] = "image"
            block["title"] = "Image"
            block["imageRefs"] = image_refs
        self.block.emit(block)

    def _emit_plan(self, params: dict) -> None:
        try:
            text = L.format_codex_plan_update(params.get("plan"), params.get("explanation"))
        except Exception:
            text = ""
        if text:
            self.block.emit({"id": "active-plan", "kind": "plan", "title": "Plan",
                             "plan": params.get("plan") if isinstance(params.get("plan"), list) else [], "body": text})

    def _handle_stream_event(self, params: dict) -> None:
        provider = str(params.get("provider") or "")
        event = params.get("event") if isinstance(params.get("event"), dict) else {}
        etype = str(event.get("type") or "")
        sid = event.get("session_id") or event.get("sessionId")
        if sid:
            sid_text = str(sid)
            if sid_text != self._session_id:
                self._session_id = sid_text
                self.session_ready.emit(sid_text)
        if provider == "claude":
            if etype == "stream_event":
                stream = event.get("event") if isinstance(event.get("event"), dict) else {}
                delta = stream.get("delta") if isinstance(stream.get("delta"), dict) else {}
                if delta.get("type") == "text_delta":
                    self.assistant_delta.emit("claude-assistant", str(delta.get("text") or ""))
            elif etype == "assistant":
                msg = event.get("message") if isinstance(event.get("message"), dict) else {}
                image_refs = nh.claude_content_image_refs(msg.get("content"))
                if image_refs:
                    self.block.emit({
                        "id": str(event.get("uuid") or msg.get("id") or "claude-image"),
                        "kind": "image",
                        "title": "Image",
                        "status": "completed",
                        "imageRefs": image_refs,
                    })
                for blk in (msg.get("content") or []):
                    if isinstance(blk, dict) and blk.get("type") == "tool_use":
                        name = str(blk.get("name") or "Claude tool")
                        fields = nh.claude_tool_activity_fields(name, blk.get("input"))
                        self.block.emit({
                            "id": str(blk.get("id") or name),
                            "kind": str(fields.get("kind") or "tool"),
                            "title": str(fields.get("title") or name),
                            "status": "requested",
                            "body": str(fields.get("text") or _tool_input_summary(blk.get("input"))),
                            "diff": str(fields.get("diff") or ""),
                            "files": list(fields.get("changes") or []),
                            "imageRefs": list(fields.get("imageRefs") or []),
                        })
            elif etype == "user":
                msg = event.get("message") if isinstance(event.get("message"), dict) else {}
                for blk in (msg.get("content") or []):
                    if isinstance(blk, dict) and blk.get("type") == "tool_result":
                        text = nh.claude_tool_result_text(event, blk)
                        fields = nh.claude_tool_result_fields(event, blk, text)
                        self.block.emit({
                            "id": str(blk.get("id") or blk.get("tool_use_id") or "result") + ":result",
                            "kind": str(fields.get("kind") or "result"),
                            "title": str(fields.get("title") or "Tool result"),
                            "status": "completed",
                            "body": str(fields.get("text") or text),
                            "diff": str(fields.get("diff") or ""),
                            "files": list(fields.get("changes") or []),
                            "imageRefs": list(fields.get("imageRefs") or []),
                        })
            elif etype == "system" and event.get("subtype") == "status":
                mode = str(event.get("permissionMode") or "").strip()
                if mode:
                    self.block.emit({"id": "claude-permission-mode", "kind": "plan", "title": "Plan",
                                     "body": "Claude plan mode active" if mode == "plan" else f"Permission mode: {mode}"})
            elif etype == "result":
                result = str(event.get("result") or "")
                if result:
                    self.assistant_delta.emit("claude-result", result)
                self._capture_session()
                self._save_thread_ref()
                self._finish_turn()
            elif etype == "rate_limit_event":
                info = event.get("rate_limit_info") if isinstance(event.get("rate_limit_info"), dict) else event
                self._apply_claude_rate_limit(info)
        elif provider == "cursor":
            text = str(event.get("delta") or event.get("text") or "")
            if text:
                self.assistant_delta.emit("cursor-assistant", text)
            if etype in {"result", "done", "complete", "completed"}:
                self._capture_session()
                self._save_thread_ref()
                self._finish_turn()
        elif provider == "antigravity":
            text = str(event.get("text") or event.get("message") or "")
            if text and etype == "assistant":
                self.assistant_delta.emit(str(event.get("native_id") or "antigravity-assistant"), text)
            elif etype == "error":
                self.error.emit(text or "Antigravity turn failed.")

    def _apply_claude_rate_limit(self, info: dict) -> None:
        profile = self._profile
        if profile is None or L.provider_key(profile) != "claude":
            return
        utilization = L.sanitize_float(info.get("utilization"))
        reset = L.sanitize_float(info.get("resetsAt"))
        limit_type = str(info.get("rateLimitType") or "")
        normalized = re.sub(r"[^a-z0-9]", "", limit_type.lower())
        weekly = normalized in {"sevenday", "sevendayoauth", "weekly", "week", "7d", "7day"} or "seven" in normalized or "week" in normalized
        used_percent = ""
        if utilization is not None:
            scaled = utilization * 100.0 if utilization <= 1.0 else utilization
            used_percent = str(round(max(0.0, min(100.0, scaled)), 2))
        reset_iso = ""
        if reset is not None:
            reset_iso = dt.datetime.fromtimestamp(reset, dt.timezone.utc).isoformat().replace("+00:00", "Z")
        if weekly:
            profile["weeklyLimitUsedPercent"] = used_percent
            profile["weeklyLimitResetUtc"] = reset_iso
            profile["weeklyResetEstimateUtc"] = reset_iso
            profile["weeklyResetEstimateSource"] = "claude-rate-limit-event" if reset_iso else ""
        else:
            profile["shortLimitUsedPercent"] = used_percent
            profile["shortLimitResetUtc"] = reset_iso
        profile["lastLimitsRefreshUtc"] = L.iso_utc_now()
        profile["lastUsageError"] = ""
        summary = profile.get("usageSummary") if isinstance(profile.get("usageSummary"), dict) else {}
        summary["lastRateLimitEvent"] = dict(info)
        profile["usageSummary"] = summary
        self.rate_limits_changed.emit()
        label = "weekly" if weekly else "session"
        self.activity.emit(
            f"Claude {label} limit updated"
            + (f": {used_percent}% used" if used_percent else "")
            + (f", resets {L.local_datetime_label(reset_iso)}" if reset_iso else "")
        )

    # ---------- send (starts/resumes a session, sends a turn) ----------
    def send(
        self,
        profile: dict,
        text: str,
        controls: dict | None = None,
        attachments: list[Path] | None = None,
    ) -> None:
        if self._busy:
            self.error.emit("A turn is already running.")
            return
        key = self._profile_key(profile)
        if self._transport is not None and key != self._transport_key:
            self._close_transport()
        self._profile = profile
        self._transport_key = key
        self._last_title = L.clip_text(text, 80)
        self._busy = True
        self._turn_active = True
        self.turn_started.emit()
        threading.Thread(
            target=self._send_worker,
            args=(profile, text, controls or {}, list(attachments or [])),
            daemon=True,
            name="ai-hub-qt-native-turn",
        ).start()

    def _send_worker(self, profile: dict, text: str, controls: dict, attachments: list[Path]) -> None:
        try:
            provider = L.provider_key(profile)
            workspace = Path(str(profile.get("workspace") or L.DEFAULT_WORKSPACE))
            workspace.mkdir(parents=True, exist_ok=True)
            nh = data.native()
            eng = data.engine()
            if self._transport is None:
                self._transport = self._make_transport(nh, eng, profile, workspace, provider, controls)
                if isinstance(self._transport, nh.CodexTransport):
                    self._transport.connect()
                    if self._session_id:
                        self._transport.resume_thread(self._session_id, workspace)
                    else:
                        access = L.codex_access_parameters(str(controls.get("access") or "workspace"), workspace)
                        self._transport.start_thread(
                            workspace,
                            model=str(controls.get("model") or ""),
                            approval_policy=str(access.get("approvalPolicy") or "on-request"),
                            sandbox=str(access.get("threadSandbox") or "workspace-write"),
                            personality=str(controls.get("personality") or ""),
                        )
                    self._session_id = self._transport.thread_id
                    self.session_ready.emit(self._session_id)
            t = self._transport
            if isinstance(t, nh.CodexTransport):
                access = L.codex_access_parameters(str(controls.get("access") or "workspace"), workspace)
                t.start_turn(
                    text,
                    attachments=attachments,
                    model=str(controls.get("model") or ""),
                    effort=str(controls.get("effort") or ""),
                    approval_policy=str(access.get("approvalPolicy") or "on-request"),
                    sandbox_policy=access.get("sandboxPolicy") if isinstance(access.get("sandboxPolicy"), dict) else None,
                    personality=str(controls.get("personality") or ""),
                )
            else:
                t.set_options(
                    model=str(controls.get("model") or ""),
                    effort=str(controls.get("effort") or ""),
                    access_mode=str(controls.get("access") or "default"),
                )
                t.send(L.native_attachment_prompt(text, attachments))
                self._capture_session()
                self._save_thread_ref()
        except Exception as error:
            self.error.emit(str(error))
            self._finish_turn()

    def _make_transport(self, nh, eng, profile: dict, workspace: Path, provider: str, controls: dict):
        if provider == "codex":
            if not eng.codex_cli_path:
                raise RuntimeError(eng.codex_cli_error or "Codex CLI was not found.")
            home = Path(str(profile.get("codexHome") or L.DEFAULT_CODEX_HOME))
            if not (home / "auth.json").exists():
                raise RuntimeError(f"{profile.get('name','Account')} is not logged in. Use Login first.")
            return nh.CodexTransport(eng.codex_cli_path, home, workspace, self._on_event)
        if provider == "claude":
            if L.claude_desktop_only(profile):
                raise RuntimeError(
                    "This is a Claude Desktop-only profile. Claude Code CLI and coding transport are disabled."
                )
            if not eng.claude_code_path:
                raise RuntimeError("Claude Code CLI was not found.")
            env = os.environ.copy()
            env["CLAUDE_CONFIG_DIR"] = str(L.claude_profile_home(profile))
            # Wire the loopback permission bridge so Claude tool approvals surface in-app.
            url, tok = self._ensure_permission_bridge()
            env["AI_HUB_PERMISSION_URL"] = url
            env["AI_HUB_PERMISSION_TOKEN"] = tok
            env["AI_HUB_PERMISSION_BRIDGE_PATH"] = str(L.CLAUDE_PERMISSION_BRIDGE_PATH)
            env["AI_HUB_PYTHON"] = sys.executable
            return nh.StreamJsonTransport(
                "claude",
                eng.claude_code_path,
                workspace,
                self._on_event,
                env=env,
                session_id=self._session_id,
                model=str(controls.get("model") or ""),
                effort=str(controls.get("effort") or ""),
                access_mode=str(controls.get("access") or "default"),
            )
        if provider == "cursor":
            if not eng.cursor_agent_path:
                raise RuntimeError("Cursor Agent CLI is not installed.")
            return nh.StreamJsonTransport(
                "cursor",
                eng.cursor_agent_path,
                workspace,
                self._on_event,
                session_id=self._session_id,
                model=str(controls.get("model") or ""),
                access_mode=str(controls.get("access") or "default"),
            )
        if provider == "antigravity":
            if not eng.antigravity_cli_path or Path(eng.antigravity_cli_path).name.lower() == "agy-node.cmd":
                raise RuntimeError("Antigravity structured CLI is unavailable.")
            return nh.AntigravityTransport(
                eng.antigravity_cli_path,
                workspace,
                self._on_event,
                session_id=self._session_id,
                cli_home=nh.antigravity_cli_home(),
                model=str(controls.get("model") or ""),
                access_mode=str(controls.get("access") or "default"),
                print_timeout=L.normalize_antigravity_print_timeout(profile.get("antigravityPrintTimeout")),
            )
        raise RuntimeError(f"{L.provider_label(profile)} has no structured transport.")

    def _profile_key(self, profile: dict) -> str:
        workspace = Path(str(profile.get("workspace") or L.DEFAULT_WORKSPACE))
        return f"{L.provider_key(profile)}|{L.profile_id(profile)}|{str(workspace).lower()}"

    def _save_thread_ref(self) -> None:
        profile = self._profile
        session_id = self.session_id
        if profile is None or not session_id:
            return
        nh = data.native()
        provider = L.provider_key(profile)
        workspace = Path(str(profile.get("workspace") or L.DEFAULT_WORKSPACE))
        native_home = None
        if provider == "codex":
            native_home = Path(str(profile.get("codexHome") or L.DEFAULT_CODEX_HOME))
        elif provider == "claude":
            native_home = L.claude_profile_home(profile)
        elif provider == "cursor":
            native_home = L.CURSOR_HOME
        elif provider == "antigravity":
            native_home = nh.antigravity_cli_home()
        nh.upsert_thread_ref(
            L.NATIVE_THREADS_FILE,
            nh.thread_ref(
                provider,
                L.profile_id(profile),
                workspace,
                session_id,
                title=self._last_title or f"{L.provider_label(profile)} session",
                native_home=native_home,
            ),
        )

    def load_skills(self, profile: dict) -> None:
        def worker() -> None:
            if L.provider_key(profile) != "codex":
                self.skills_ready.emit([])
                return
            transport = None
            try:
                nh = data.native()
                eng = data.engine()
                workspace = Path(str(profile.get("workspace") or L.DEFAULT_WORKSPACE))
                transport = nh.CodexTransport(
                    eng.codex_cli_path,
                    Path(str(profile.get("codexHome") or L.DEFAULT_CODEX_HOME)),
                    workspace,
                    self._on_event,
                )
                transport.connect()
                groups = transport.list_skills(workspace)
                skills = []
                for group in groups:
                    for skill in group.get("skills", []) if isinstance(group, dict) else []:
                        if isinstance(skill, dict):
                            skills.append(skill)
                self.skills_ready.emit(skills)
            except Exception as error:
                self.error.emit(f"Could not load skills: {error}")
                self.skills_ready.emit([])
            finally:
                if transport is not None:
                    transport.shutdown()

        threading.Thread(target=worker, daemon=True, name="ai-hub-qt-skills").start()

    def stop(self) -> None:
        t = self._transport
        if t is None:
            return
        try:
            if hasattr(t, "interrupt"):
                t.interrupt()
            else:
                t.stop()
        except Exception:
            pass
        self._finish_turn()


def _err_text(params) -> str:
    if isinstance(params, dict):
        return str(params.get("message") or params.get("error") or params)
    return str(params)


def _deny(payload: dict, message: str) -> dict:
    result = {"behavior": "deny", "message": message, "interrupt": True, "decisionClassification": "user_reject"}
    tuid = str(payload.get("tool_use_id") or "")
    if tuid:
        result["toolUseID"] = tuid
    return result


def allow_decision(payload: dict, updated_input: dict | None = None) -> dict:
    result = {"behavior": "allow", "updatedInput": updated_input or {}, "decisionClassification": "user_temporary"}
    tuid = str(payload.get("tool_use_id") or "")
    if tuid:
        result["toolUseID"] = tuid
    return result


def _tool_input_summary(tool_input) -> str:
    if not isinstance(tool_input, dict):
        return ""
    cmd = str(tool_input.get("command") or "").strip()
    if cmd:
        return cmd
    try:
        import json
        return json.dumps(tool_input, ensure_ascii=False)[:400]
    except Exception:
        return str(tool_input)[:400]


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(str(b.get("text") or ""))
        return "\n".join(parts)
    return str(content or "")
