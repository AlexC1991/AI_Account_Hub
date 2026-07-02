"""Native protocol / session-lifecycle bridge for the Coding workbench.

Extracted from AccountCalendarApp. Holds no state of its own beyond
``self.app`` -- every reference to shared app state goes through it.

A handful of small helper functions/constants (provider_key, DEFAULT_WORKSPACE,
etc.) are defined in ai_hub_calendar_gui.py itself, which in turn imports
CodingTransportBridge from this module. Importing them back here at module
level would be a circular import, and relying on the "partially-initialized
module in sys.modules" trick to make that work is fragile: it silently
breaks for any loader that does not register the module in sys.modules
before executing it (the test suite loads ai_hub_calendar_gui.py via
importlib.util.spec_from_file_location + exec_module, which skips that
registration step). So instead, ai_hub_calendar_gui.py calls
configure_helpers(globals()) once, immediately after importing this module,
passing its own globals() dict -- which works regardless of how it was
loaded, because globals() always returns that module's real, live __dict__.

Pure helper functions are snapshotted once (safe -- they are never
reassigned). A few constants that are file paths / delay values are looked
up live through the shared dict every time (via _hub_globals["NAME"]),
because the test suite intentionally monkeypatches some of them at runtime
(e.g. hub.NATIVE_THREADS_FILE = <tempdir>) for test isolation, and a one-time
snapshot would keep using the stale pre-test value.
"""

from __future__ import annotations

import datetime as dt
import http.server
import json
import logging
import os
import queue
import secrets
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

_logger = logging.getLogger(__name__)

from native_harness import (
    AntigravityTransport,
    CodexTransport,
    NativeTransportError,
    StreamJsonTransport,
    claude_content_image_refs,
    claude_tool_activity_fields,
    claude_tool_result_fields,
    claude_tool_result_text,
    codex_thread_messages,
    discover_antigravity_threads,
    discover_claude_threads,
    discover_codex_file_threads,
    discover_cursor_threads,
    extract_message_text,
    load_thread_refs,
    normalized_path_key,
    read_antigravity_thread,
    read_claude_thread,
    read_codex_session_file,
    read_cursor_thread,
    summarize_codex_item,
    thread_ref,
    upsert_thread_ref,
)

_HUB_FUNCTION_NAMES = (
    "claude_profile_home",
    "clip_text",
    "codex_access_parameters",
    "coding_display_text",
    "coding_user_message_parts",
    "format_codex_plan_update",
    "iso_utc_now",
    "local_datetime_label",
    "native_attachment_kind",
    "native_attachment_prompt",
    "parse_coding_slash_command",
    "parse_iso_datetime",
    "profile_id",
    "provider_key",
    "provider_label",
    "same_local_path",
    "sanitize_float",
    "save_profiles",
)
_HUB_LIVE_CONSTANT_NAMES = (
    "CLAUDE_CLI_HOME",
    "CLAUDE_PERMISSION_BRIDGE_PATH",
    "CODING_NATIVE_FULL_RENDER_DELAY_MS",
    "CODING_NATIVE_RENDER_DELAY_MS",
    "CURSOR_HOME",
    "DEFAULT_CODEX_HOME",
    "DEFAULT_WORKSPACE",
    "NATIVE_THREADS_FILE",
)

_hub_globals: dict = {}


def configure_helpers(hub_globals: dict) -> None:
    """Wire up ai_hub_calendar_gui.py's small helpers.

    Called once by ai_hub_calendar_gui.py immediately after it imports
    CodingTransportBridge from this module, passing its own globals().
    See the module docstring above for why this replaces a direct import.
    """
    missing = (set(_HUB_FUNCTION_NAMES) | set(_HUB_LIVE_CONSTANT_NAMES)) - set(hub_globals)
    if missing:
        raise RuntimeError(f"configure_helpers missing required names: {sorted(missing)}")
    globals().update({name: hub_globals[name] for name in _HUB_FUNCTION_NAMES})
    # Rebind (not copy) so _hub_globals["NAME"] always reflects whatever
    # ai_hub_calendar_gui.py's own namespace currently holds, including
    # later test monkeypatches like hub.NATIVE_THREADS_FILE = <tempdir>.
    global _hub_globals
    _hub_globals = hub_globals


class CodingTransportBridge:
    """Marshals native provider events between the transports in
    native_harness.py and the AccountCalendarApp UI. Holds no state of its
    own; all shared state lives on ``self.app``.
    """

    def __init__(self, app) -> None:
        self.app = app

    def prepare_native_thread(self) -> None:
        profile = self.app.coding_selected_profile()
        if profile is None:
            messagebox.showinfo("Select harness account", "Add or select a provider account first.", parent=self.app)
            return
        if self.app.native_busy:
            return
        provider = provider_key(profile)
        workspace = Path(self.app.coding_workspace_var.get() or profile.get("workspace") or _hub_globals["DEFAULT_WORKSPACE"])
        workspace.mkdir(parents=True, exist_ok=True)
        controls = self.app.coding_control_values()
        access = codex_access_parameters(controls["access"], workspace)

        if provider == "cursor" and not self.app.cursor_agent_path:
            self.app._append_native_message(
                "error",
                "Cursor Desktop is installed, but Cursor Agent CLI is not. Opened the native Cursor project instead.",
            )
            self.app.open_cursor_desktop(profile)
            self.app.coding_session_active = False
            self.app._render_coding()
            return
        if provider == "antigravity" and (
            not self.app.antigravity_cli_path or Path(self.app.antigravity_cli_path).name.lower() == "agy-node.cmd"
        ):
            self.app._append_native_message(
                "activity",
                "This Antigravity install exposes no healthy structured CLI. Opened the native Antigravity desktop fallback.",
            )
            self.app.open_antigravity_desktop(profile)
            self.app.coding_session_active = False
            self.app._render_coding()
            return

        saved_ref = self.app._saved_native_thread_ref(profile, workspace)
        saved_session_id = str(saved_ref.get("nativeSessionId") or "") if saved_ref else ""
        self.app.close_native_transport()
        self.app.native_generation += 1
        generation = self.app.native_generation
        self.app.native_messages = []
        self.app.native_attachments = []
        self.app.native_busy = True
        self.app.coding_session_active = False
        self.app.coding_composer_status.configure(text=f"Connecting {provider_label(profile)}")
        self.app._render_coding()

        def worker() -> tuple[CodexTransport | StreamJsonTransport, str, dict]:
            transport = self.app._create_native_transport(
                profile,
                workspace,
                session_id="" if provider == "codex" else saved_session_id,
            )
            self.app._track_pending_transport(transport)
            try:
                if isinstance(transport, CodexTransport):
                    initialize = transport.connect()
                    thread: dict = {}
                    if saved_session_id:
                        try:
                            resumed = transport.resume_thread(saved_session_id, workspace)
                            thread = resumed.get("thread") if isinstance(resumed.get("thread"), dict) else {}
                        except Exception:
                            thread = {}
                    if not transport.thread_id:
                        started = transport.start_thread(
                            workspace,
                            model=controls["model"],
                            approval_policy=access["approvalPolicy"],
                            sandbox=access["threadSandbox"],
                            personality=controls["personality"],
                        )
                        thread = started.get("thread") if isinstance(started.get("thread"), dict) else {}
                    return transport, transport.thread_id, {"initialize": initialize, "thread": thread}
                return transport, saved_session_id, {"thread": dict(saved_ref or {})}
            except Exception:
                self.app._stop_transport(transport)
                self.app._untrack_pending_transport(transport)
                raise

        def success(result: tuple[CodexTransport | StreamJsonTransport, str, dict]) -> None:
            self.app._untrack_pending_transport(result[0])
            if generation != self.app.native_generation:
                self.app._stop_transport(result[0])
                return
            transport, thread_id, metadata = result
            self.app.native_transport = transport
            self.app.native_transport_key = self.app._native_transport_key(profile, workspace)
            self.app.native_thread_id = thread_id
            self.app.native_turn_id = ""
            thread = metadata.get("thread") if isinstance(metadata, dict) and isinstance(metadata.get("thread"), dict) else {}
            self.app.native_thread_title = clip_text(thread.get("title") or thread.get("preview") or thread.get("thread_name") or "", 90)
            self.app.native_busy = False
            self.app.coding_session_active = True
            initialize = metadata.get("initialize") if isinstance(metadata, dict) else {}
            user_agent = str(initialize.get("userAgent") or "") if isinstance(initialize, dict) else ""
            self.app.native_diagnostics = [user_agent] if user_agent else []
            self.app.status_var.set(f"{provider_label(profile)} native thread ready.")
            self.app._render_coding()
            self.app.coding_input.focus_set()
            if self.app.coding_context_tab == "skills":
                self.app.refresh_native_skills(force=True)
            pending_command = getattr(self.app, "_native_pending_command", None)
            self.app._native_pending_command = None
            if pending_command:
                parsed = pending_command.get("parsed") if isinstance(pending_command, dict) else None
                queued_attachments = pending_command.get("attachments") if isinstance(pending_command, dict) else []
                if isinstance(parsed, dict):
                    self.app._run_coding_slash_command(parsed, list(queued_attachments or []))
                    return
            pending = getattr(self.app, "_native_pending_send", None)
            self.app._native_pending_send = None
            if pending:
                self.app._send_native_now(*pending)

        self.app._run_native_worker(worker, success, generation)

    def attach_native_files(self) -> None:
        profile = self.app.coding_selected_profile()
        if profile is None:
            return
        selected = filedialog.askopenfilenames(parent=self.app, title=f"Attach files to the next {provider_label(profile)} turn")
        if not selected:
            return
        self.app._add_native_attachments([Path(path) for path in selected])

    def send_native_message(self) -> None:
        if self.app.native_busy:
            return
        text = "" if getattr(self.app, "coding_input_placeholder_active", False) else self.app.coding_input.get("1.0", "end-1c")
        attachments = list(self.app.native_attachments)
        if not text.strip() and not attachments:
            return
        parsed = parse_coding_slash_command(text)
        if parsed is not None:
            self.app._handle_coding_slash_command(parsed, attachments)
            return
        if not self.app.coding_session_active or self.app.native_transport is None:
            self.app._native_pending_send = (text, attachments, {})
            self.app.prepare_native_thread()
            return
        self.app._send_native_now(text, attachments)

    def _send_native_now(self, text: str, attachments: list[Path], turn_options: dict | None = None) -> None:
        transport = self.app.native_transport
        profile = self.app.coding_selected_profile()
        if transport is None or profile is None:
            return
        turn_options = turn_options if isinstance(turn_options, dict) else {}
        controls = self.app.coding_control_values()
        access = codex_access_parameters(controls["access"], Path(self.app.coding_workspace_var.get() or _hub_globals["DEFAULT_WORKSPACE"]))
        if isinstance(transport, StreamJsonTransport):
            transport.set_options(
                model=controls["model"],
                effort=controls["effort"],
                access_mode=controls["access"],
            )
        display_text = text.strip() or "Please review the attached files."
        transport_text = display_text
        if isinstance(transport, StreamJsonTransport):
            transport_text = native_attachment_prompt(display_text, attachments)
        self.app.coding_input.delete("1.0", "end")
        self.app.coding_input_placeholder_active = False
        self.app.native_attachments = []
        self.app._render_native_attachments()
        image_refs = [
            {"name": path.name, "path": str(path), "url": "", "data": "", "mediaType": ""}
            for path in attachments
            if native_attachment_kind(path) == "image"
        ]
        self.app._append_native_message("user", display_text, render=False, imageRefs=image_refs)
        self.app.native_busy = True
        self.app.coding_composer_status.configure(text=f"{provider_label(profile)} working")
        self.app._render_coding()

        def worker() -> object:
            if isinstance(transport, CodexTransport):
                return transport.start_turn(
                    text,
                    attachments,
                    model=controls["model"],
                    effort=controls["effort"],
                    approval_policy=access["approvalPolicy"],
                    sandbox_policy=access["sandboxPolicy"],
                    personality=str(turn_options.get("personality") or controls["personality"]),
                    collaboration_mode=turn_options.get("collaborationMode") if isinstance(turn_options.get("collaborationMode"), dict) else None,
                )
            return transport.send(transport_text)

        def success(result: object) -> None:
            if isinstance(transport, CodexTransport):
                turn = result.get("turn") if isinstance(result, dict) and isinstance(result.get("turn"), dict) else {}
                self.app.native_turn_id = str(turn.get("id") or transport.turn_id)
            elif isinstance(result, int) and result != 0 and self.app.native_busy:
                self.app.native_busy = False
            self.app._render_coding()

        self.app._run_native_worker(worker, success, self.app.native_generation)

    def stop_native_turn(self) -> None:
        transport = self.app.native_transport
        if transport is None or not self.app.native_busy:
            return
        self.app.coding_composer_status.configure(text="Stopping")

        def worker() -> None:
            if isinstance(transport, CodexTransport):
                transport.interrupt()
            else:
                transport.stop()

        def success(_result: object) -> None:
            self.app.native_busy = False
            self.app._append_native_message("activity", "Native turn stopped.", render=False)
            self.app._render_coding()

        self.app._run_native_worker(worker, success, self.app.native_generation)

    def close_native_transport(self) -> None:
        self.app.native_generation += 1
        transport = self.app.native_transport
        self.app.native_transport = None
        self.app.native_transport_key = ""
        self.app.native_thread_id = ""
        self.app.native_thread_title = ""
        self.app.native_turn_id = ""
        self.app.native_busy = False
        self.app.coding_session_active = False
        self.app.native_messages = []
        self.app.native_attachments = []
        self.app.native_diagnostics = []
        self.app.native_turn_diff = ""
        self.app.native_file_changes = []
        self.app.native_token_usage = {}
        self.app._clear_native_skills_cache()
        with self.app.native_transport_lock:
            pending = list(self.app.native_pending_transports)
            self.app.native_pending_transports.clear()
        if transport is not None:
            self.app._stop_transport(transport)
        for item in pending:
            self.app._stop_transport(item)

    def _ensure_claude_permission_bridge(self) -> tuple[str, str]:
        if self.app.claude_permission_server is not None and self.app.claude_permission_url:
            return self.app.claude_permission_url, self.app.claude_permission_token
        token = secrets.token_urlsafe(24)
        app = self.app

        class PermissionHandler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args) -> None:
                return

            def _send_json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                if self.path != "/permission":
                    self._send_json(404, {"behavior": "deny", "message": "Unknown AI Account Hub permission endpoint."})
                    return
                if self.headers.get("Authorization", "") != f"Bearer {token}":
                    self._send_json(403, {"behavior": "deny", "message": "Invalid AI Account Hub permission token."})
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    size = 0
                try:
                    payload = json.loads(self.rfile.read(size).decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    self._send_json(400, {"behavior": "deny", "message": "Invalid permission payload."})
                    return
                if not isinstance(payload, dict):
                    self._send_json(400, {"behavior": "deny", "message": "Invalid permission payload."})
                    return
                decision = app._handle_claude_permission_payload(payload)
                self._send_json(200, decision)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), PermissionHandler)
        thread = threading.Thread(target=server.serve_forever, name="ai-hub-claude-permission", daemon=True)
        thread.start()
        self.app.claude_permission_server = server
        self.app.claude_permission_thread = thread
        self.app.claude_permission_token = token
        self.app.claude_permission_url = f"http://127.0.0.1:{server.server_address[1]}/permission"
        return self.app.claude_permission_url, self.app.claude_permission_token

    def _shutdown_claude_permission_bridge(self) -> None:
        server = self.app.claude_permission_server
        self.app.claude_permission_server = None
        self.app.claude_permission_url = ""
        self.app.claude_permission_token = ""
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except OSError:
                pass
        thread = self.app.claude_permission_thread
        self.app.claude_permission_thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)

    def _handle_claude_permission_payload(self, payload: dict) -> dict:
        result: dict[str, object] = {}
        finished = threading.Event()

        def ask() -> None:
            try:
                result["value"] = self.app._ask_claude_permission(payload)
            except Exception as error:
                result["value"] = self.app._claude_permission_deny(payload, f"AI Account Hub permission prompt failed: {error}", interrupt=True)
            finally:
                finished.set()

        self.app._post_native_ui(ask)
        if not finished.wait(timeout=300):
            return self.app._claude_permission_deny(payload, "Timed out waiting for AI Account Hub permission response.", interrupt=True)
        value = result.get("value")
        return value if isinstance(value, dict) else self.app._claude_permission_deny(payload, "Invalid AI Account Hub permission response.", interrupt=True)

    def _claude_permission_allow(self, payload: dict) -> dict:
        result: dict = {
            "behavior": "allow",
            "updatedInput": {},
            "decisionClassification": "user_temporary",
        }
        tool_use_id = str(payload.get("tool_use_id") or "")
        if tool_use_id:
            result["toolUseID"] = tool_use_id
        return result

    def _claude_permission_deny(self, payload: dict, message: str, interrupt: bool = False) -> dict:
        result: dict = {
            "behavior": "deny",
            "message": message,
            "interrupt": interrupt,
            "decisionClassification": "user_reject",
        }
        tool_use_id = str(payload.get("tool_use_id") or "")
        if tool_use_id:
            result["toolUseID"] = tool_use_id
        return result

    def _claude_permission_summary(self, payload: dict) -> str:
        tool_name = str(payload.get("tool_name") or "Claude tool")
        tool_input = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        command = str(tool_input.get("command") or "").strip()
        description = str(tool_input.get("description") or "").strip()
        if command:
            parts = [f"{tool_name}: {command}"]
            if description:
                parts.append(description)
            return "\n".join(parts)
        questions = tool_input.get("questions") if isinstance(tool_input.get("questions"), list) else []
        if questions:
            first = questions[0] if isinstance(questions[0], dict) else {}
            question = str(first.get("question") or first.get("header") or "Claude question").strip()
            return f"{tool_name}: {question}"
        try:
            raw = json.dumps(tool_input, ensure_ascii=False, indent=2)
        except TypeError:
            raw = str(tool_input)
        raw = raw.strip()
        return f"{tool_name}: {clip_text(raw, 420) if raw else 'permission requested'}"

    def _ask_claude_permission(self, payload: dict) -> dict:
        if str(payload.get("tool_name") or "") == "ExitPlanMode":
            return self.app._ask_claude_plan_review(payload)
        if str(payload.get("tool_name") or "") == "AskUserQuestion":
            return self.app._ask_claude_user_question(payload)
        summary = self.app._claude_permission_summary(payload)
        tool_use_id = str(payload.get("tool_use_id") or "claude-permission")
        self.app._upsert_native_activity(f"claude-permission-{tool_use_id}", f"Claude permission requested\n{summary}", render=False)
        self.app._schedule_native_render(full=False)
        self.app.update_idletasks()
        decision = self.app._native_request_dialog(
            "Claude permission request",
            summary,
            [
                ("Allow once", "allow", "Let Claude Code run this native action once."),
                ("Deny", "deny", "Refuse the action and let Claude continue."),
                ("Stop turn", "interrupt", "Deny this action and interrupt the active Claude turn."),
            ],
            allow_text=False,
        )
        if decision == "allow":
            self.app._upsert_native_activity(f"claude-permission-{tool_use_id}", f"Claude permission allowed\n{summary}", render=False)
            return self.app._claude_permission_allow(payload)
        interrupt = decision in {None, "interrupt"}
        self.app._upsert_native_activity(f"claude-permission-{tool_use_id}", f"Claude permission denied\n{summary}", render=False)
        return self.app._claude_permission_deny(payload, "Denied by user in AI Account Hub.", interrupt=interrupt)

    def _ask_claude_user_question(self, payload: dict) -> dict:
        tool_use_id = str(payload.get("tool_use_id") or "claude-question")
        tool_input = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        questions = tool_input.get("questions") if isinstance(tool_input.get("questions"), list) else []
        if not questions:
            return self.app._claude_permission_deny(payload, "Claude asked for input, but no questions were provided.", interrupt=False)

        self.app._upsert_native_activity(
            f"claude-question-{tool_use_id}",
            f"Claude question\n{self.app._claude_permission_summary(payload)}",
            render=False,
        )
        self.app._schedule_native_render(full=False)
        self.app.update_idletasks()

        answers: dict[str, object] = {}
        for index, raw_question in enumerate(questions, start=1):
            if not isinstance(raw_question, dict):
                continue
            question_text = str(raw_question.get("question") or raw_question.get("header") or f"Question {index}").strip()
            header = str(raw_question.get("header") or "Claude question").strip()[:32]
            options = raw_question.get("options") if isinstance(raw_question.get("options"), list) else []
            choices: list[tuple[str, str, str]] = []
            for option in options:
                if not isinstance(option, dict):
                    continue
                label = str(option.get("label") or "").strip()
                if not label:
                    continue
                choices.append((label, label, str(option.get("description") or "")))
            prompt = question_text
            if raw_question.get("multiSelect"):
                prompt = f"{question_text}\n\nSelect one option, or type comma-separated labels/custom text for multiple answers."
            value = self.app._native_request_dialog(
                header or "Claude question",
                prompt,
                choices,
                allow_text=True,
            )
            if value is None:
                self.app._upsert_native_activity(
                    f"claude-question-{tool_use_id}",
                    f"Claude question cancelled\n{question_text}",
                    render=False,
                )
                return self.app._claude_permission_deny(payload, "User cancelled the Claude question in AI Account Hub.", interrupt=False)
            value = str(value).strip()
            if value:
                if raw_question.get("multiSelect") and "," in value:
                    answers[question_text] = [part.strip() for part in value.split(",") if part.strip()]
                else:
                    answers[question_text] = value

        updated_input = dict(tool_input)
        updated_input["questions"] = questions
        updated_input["answers"] = answers
        result: dict[str, object] = {
            "behavior": "allow",
            "updatedInput": updated_input,
            "decisionClassification": "user_temporary",
        }
        if tool_use_id:
            result["toolUseID"] = tool_use_id
        answer_summary = "; ".join(f"{key}: {value}" for key, value in answers.items()) or "No answers supplied"
        self.app._upsert_native_activity(
            f"claude-question-{tool_use_id}",
            f"Claude question answered\n{answer_summary}",
            render=False,
        )
        return result

    def _ask_claude_plan_review(self, payload: dict) -> dict:
        tool_use_id = str(payload.get("tool_use_id") or "claude-plan")
        tool_input = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        plan = str(tool_input.get("plan") or "").strip()
        plan_file_path = str(tool_input.get("planFilePath") or "").strip()
        if not plan:
            return self.app._claude_permission_deny(payload, "Claude asked to exit plan mode, but no plan was provided.", interrupt=False)

        self.app._upsert_native_activity(
            f"claude-plan-{tool_use_id}",
            f"Claude plan review requested\n{clip_text(plan, 900)}",
            render=False,
        )
        self.app._schedule_native_render(full=False)
        self.app.update_idletasks()

        reviewed_plan = self.app._native_plan_review_dialog(plan, plan_file_path)
        if reviewed_plan is None:
            self.app._upsert_native_activity(
                f"claude-plan-{tool_use_id}",
                "Claude plan denied",
                render=False,
            )
            return self.app._claude_permission_deny(payload, "Plan was not approved in AI Account Hub.", interrupt=False)

        updated_input = dict(tool_input)
        updated_input["plan"] = reviewed_plan
        if plan_file_path:
            updated_input["planFilePath"] = plan_file_path
        result: dict[str, object] = {
            "behavior": "allow",
            "updatedInput": updated_input,
            "decisionClassification": "user_temporary",
        }
        if tool_use_id:
            result["toolUseID"] = tool_use_id
        changed = reviewed_plan.strip() != plan.strip()
        label = "Claude plan edited and approved" if changed else "Claude plan approved"
        self.app._upsert_native_activity(
            f"claude-plan-{tool_use_id}",
            f"{label}\n{clip_text(reviewed_plan, 900)}",
            render=False,
        )
        return result

    def _claude_tool_activity_text(self, name: str, tool_input: object) -> str:
        payload = {"tool_name": name, "input": tool_input if isinstance(tool_input, dict) else {}}
        summary = self.app._claude_permission_summary(payload)
        if name in {"EnterPlanMode", "ExitPlanMode"}:
            return f"Plan\n{summary}"
        if name == "AskUserQuestion":
            return f"Claude question\n{summary}"
        return summary

    def _codex_activity_fields(self, item: dict) -> dict:
        item_type = str(item.get("type") or "")
        fields: dict[str, object] = {
            "kind": {
                "commandExecution": "command",
                "fileChange": "file_change",
                "mcpToolCall": "tool",
                "dynamicToolCall": "tool",
                "collabToolCall": "tool",
                "webSearch": "tool",
                "imageView": "image",
                "contextCompaction": "notice",
                "plan": "plan",
            }.get(item_type, "activity"),
            "title": {
                "commandExecution": "Command",
                "fileChange": "File changes",
                "mcpToolCall": f"MCP {item.get('tool') or 'tool'}",
                "dynamicToolCall": str(item.get("tool") or "Tool"),
                "collabToolCall": "Collaboration",
                "webSearch": "Web search",
                "imageView": "Image",
                "contextCompaction": "Context compacted",
                "plan": "Plan",
            }.get(item_type, item_type or "Activity"),
            "status": str(item.get("status") or ""),
        }
        if item_type == "fileChange":
            changes = [change for change in item.get("changes") or [] if isinstance(change, dict)]
            fields["changes"] = changes
            diffs = [coding_display_text(change.get("diff") or "") for change in changes if str(change.get("diff") or "").strip()]
            if diffs:
                fields["kind"] = "diff"
                fields["title"] = "File changes"
                fields["diff"] = "\n".join(diffs)
        elif item_type == "imageView":
            path = str(item.get("path") or "").strip()
            if path:
                fields["imageRefs"] = [{"name": Path(path).name, "path": path, "url": "", "data": "", "mediaType": ""}]
        return fields

    def _format_claude_rate_limit_event(self, info: dict) -> str:
        limit_type = str(info.get("rateLimitType") or "limit")
        utilization = sanitize_float(info.get("utilization"))
        percent = f"{utilization * 100:.0f}% used" if utilization is not None else "usage not exposed"
        reset = sanitize_float(info.get("resetsAt"))
        reset_text = "-"
        if reset is not None:
            reset_text = local_datetime_label(dt.datetime.fromtimestamp(reset, dt.timezone.utc).isoformat())
        status = str(info.get("status") or "event")
        overage = " | overage" if info.get("isUsingOverage") else ""
        return f"Claude limit\n{limit_type}: {percent} | {status} | resets {reset_text}{overage}"

    def _apply_claude_rate_limit_event(self, info: dict) -> None:
        profile = self.app.coding_selected_profile()
        if profile is None or provider_key(profile) != "claude":
            return
        utilization = sanitize_float(info.get("utilization"))
        reset = sanitize_float(info.get("resetsAt"))
        limit_type = str(info.get("rateLimitType") or "").lower()
        reset_iso = dt.datetime.fromtimestamp(reset, dt.timezone.utc).isoformat().replace("+00:00", "Z") if reset is not None else ""
        used_percent = "" if utilization is None else str(round(max(0.0, min(1.0, utilization)) * 100, 2))
        if "seven" in limit_type or "week" in limit_type:
            profile["weeklyLimitUsedPercent"] = used_percent
            profile["weeklyLimitResetUtc"] = reset_iso
            profile["weeklyResetEstimateUtc"] = reset_iso
            profile["weeklyResetEstimateSource"] = "claude-rate-limit-event" if reset_iso else ""
        else:
            profile["shortLimitUsedPercent"] = used_percent
            profile["shortLimitResetUtc"] = reset_iso
        profile["lastLimitsRefreshUtc"] = iso_utc_now()
        profile["lastUsageError"] = ""
        summary = profile.get("usageSummary") if isinstance(profile.get("usageSummary"), dict) else {}
        summary["lastRateLimitEvent"] = info
        profile["usageSummary"] = summary
        save_profiles(self.app.profiles)

    def _track_pending_transport(self, transport: CodexTransport | StreamJsonTransport) -> None:
        with self.app.native_transport_lock:
            self.app.native_pending_transports.append(transport)

    def _untrack_pending_transport(self, transport: CodexTransport | StreamJsonTransport) -> None:
        with self.app.native_transport_lock:
            self.app.native_pending_transports = [item for item in self.app.native_pending_transports if item is not transport]

    def _stop_transport(self, transport: CodexTransport | StreamJsonTransport) -> None:
        try:
            if isinstance(transport, CodexTransport):
                transport.shutdown()
            else:
                transport.stop()
        except Exception:
            _logger.debug("Native transport shutdown failed", exc_info=True)

    def _create_native_transport(self, profile: dict, workspace: Path, session_id: str = "") -> CodexTransport | StreamJsonTransport:
        provider = provider_key(profile)
        controls = self.app.coding_control_values()
        if provider == "codex":
            if not self.app.codex_cli_path:
                raise NativeTransportError(self.app.codex_cli_error or "Codex CLI was not found.")
            codex_home = Path(str(profile.get("codexHome") or _hub_globals["DEFAULT_CODEX_HOME"]))
            if not (codex_home / "auth.json").exists():
                raise NativeTransportError(f"{profile.get('name', 'Account')} is not logged in. Use Accounts > Login first.")
            return CodexTransport(
                self.app.codex_cli_path,
                codex_home,
                workspace,
                self.app._native_event_callback,
            )
        if provider == "claude":
            if not self.app.claude_code_path:
                raise NativeTransportError("Claude Code CLI was not found.")
            env = os.environ.copy()
            env["CLAUDE_CONFIG_DIR"] = str(claude_profile_home(profile))
            permission_url, permission_token = self.app._ensure_claude_permission_bridge()
            env["AI_HUB_PERMISSION_URL"] = permission_url
            env["AI_HUB_PERMISSION_TOKEN"] = permission_token
            env["AI_HUB_PERMISSION_BRIDGE_PATH"] = str(_hub_globals["CLAUDE_PERMISSION_BRIDGE_PATH"])
            env["AI_HUB_PYTHON"] = sys.executable
            return StreamJsonTransport(
                "claude",
                self.app.claude_code_path,
                workspace,
                self.app._native_event_callback,
                env=env,
                session_id=session_id,
                model=controls["model"],
                effort=controls["effort"],
                access_mode=controls["access"],
            )
        if provider == "cursor":
            if not self.app.cursor_agent_path:
                raise NativeTransportError("Cursor Agent CLI is not installed.")
            return StreamJsonTransport(
                "cursor",
                self.app.cursor_agent_path,
                workspace,
                self.app._native_event_callback,
                session_id=session_id,
                model=controls["model"],
                effort=controls["effort"],
                access_mode=controls["access"],
            )
        if provider == "antigravity":
            if not self.app.antigravity_cli_path or Path(self.app.antigravity_cli_path).name.lower() == "agy-node.cmd":
                raise NativeTransportError("Antigravity CLI is not installed.")
            return AntigravityTransport(
                self.app.antigravity_cli_path,
                workspace,
                self.app._native_event_callback,
                session_id=session_id,
                model=controls["model"],
                access_mode=controls["access"],
            )
        raise NativeTransportError(f"{provider_label(profile)} has no structured transport on this installation.")

    def _native_transport_key(self, profile: dict, workspace: Path) -> str:
        return f"{provider_key(profile)}|{profile_id(profile)}|{str(workspace).lower()}"

    def _thread_updated_at(self, item: dict) -> float:
        value = item.get("updatedAt") or item.get("updated_at") or 0
        try:
            return float(value)
        except (TypeError, ValueError):
            return self.app._timestamp_from_iso(value)

    def _thread_workspace_key(self, item: dict, fallback: object = "") -> str:
        return normalized_path_key(
            item.get("cwd")
            or item.get("actualCwd")
            or item.get("projectPath")
            or fallback
        )

    def _saved_native_thread_ref(self, profile: dict, workspace: Path) -> dict | None:
        provider = provider_key(profile)
        pid = profile_id(profile)
        workspace_key = normalized_path_key(workspace)
        refs = [
            ref
            for ref in load_thread_refs(_hub_globals["NATIVE_THREADS_FILE"])
            if str(ref.get("provider") or "") == provider
            and str(ref.get("profileId") or "") == pid
            and str(ref.get("nativeSessionId") or "")
            and self.app._thread_workspace_key(ref) == workspace_key
        ]
        if not refs:
            return None
        return max(refs, key=self.app._thread_updated_at)

    def _merge_saved_thread_refs(self, threads: list[dict], profile: dict) -> list[dict]:
        provider = provider_key(profile)
        pid = profile_id(profile)
        merged = list(threads)
        seen = {str(thread.get("id") or "") for thread in merged}
        for ref in load_thread_refs(_hub_globals["NATIVE_THREADS_FILE"]):
            session_id = str(ref.get("nativeSessionId") or "")
            if not session_id or session_id in seen:
                continue
            if str(ref.get("provider") or "") != provider or str(ref.get("profileId") or "") != pid:
                continue
            merged.append(
                {
                    "id": session_id,
                    "provider": provider,
                    "preview": str(ref.get("title") or f"{provider_label(profile)} session"),
                    "cwd": str(ref.get("projectPath") or ""),
                    "createdAt": 0,
                    "updatedAt": self.app._timestamp_from_iso(ref.get("updatedAt")),
                    "path": "",
                    "status": {"type": "notLoaded"},
                }
            )
            seen.add(session_id)
        return merged

    def _collapse_native_threads(self, threads: list[dict], profile: dict) -> list[dict]:
        provider = provider_key(profile)
        pid = profile_id(profile)
        preferred: dict[str, str] = {}
        for ref in sorted(load_thread_refs(_hub_globals["NATIVE_THREADS_FILE"]), key=self.app._thread_updated_at, reverse=True):
            if str(ref.get("provider") or "") != provider or str(ref.get("profileId") or "") != pid:
                continue
            key = self.app._thread_workspace_key(ref)
            session_id = str(ref.get("nativeSessionId") or "")
            if key and session_id and key not in preferred:
                preferred[key] = session_id

        grouped: dict[str, list[dict]] = {}
        for thread in threads:
            if not isinstance(thread, dict):
                continue
            item = dict(thread)
            item.setdefault("provider", provider)
            key = self.app._thread_workspace_key(item)
            if not key:
                key = f"session:{item.get('id') or len(grouped)}"
            grouped.setdefault(key, []).append(item)

        collapsed: list[dict] = []
        for key, items in grouped.items():
            chosen = None
            if self.app.native_thread_id:
                chosen = next((item for item in items if str(item.get("id") or "") == self.app.native_thread_id), None)
            preferred_session = preferred.get(key)
            if chosen is None and preferred_session:
                chosen = next((item for item in items if str(item.get("id") or "") == preferred_session), None)
            if chosen is None:
                chosen = max(items, key=self.app._thread_updated_at)
            collapsed.append(chosen)
        return sorted(collapsed, key=self.app._thread_updated_at, reverse=True)

    def _run_native_worker(self, worker, success, generation: int) -> None:
        def run() -> None:
            try:
                result = worker()
            except Exception as error:
                self.app._post_native_ui(lambda value=str(error): self.app._native_worker_failed(value, generation))
                return
            self.app._post_native_ui(lambda value=result: success(value))

        threading.Thread(target=run, name="ai-hub-native-worker", daemon=True).start()

    def _native_worker_failed(self, error: str, generation: int) -> None:
        if generation != self.app.native_generation:
            return
        self.app.native_busy = False
        self.app.coding_session_active = False if self.app.native_transport is None else self.app.coding_session_active
        self.app._append_native_message("error", error, render=False)
        self.app.status_var.set(error)
        self.app._render_coding()

    def refresh_native_threads(self) -> None:
        if self.app.native_loading_threads or self.app.native_busy:
            return
        with self.app.native_transport_lock:
            if self.app.native_pending_transports:
                return
        profile = self.app.coding_selected_profile()
        if profile is None:
            self.app.native_threads = []
            self.app._render_coding_projects()
            return
        workspace = Path(self.app.coding_workspace_var.get() or profile.get("workspace") or _hub_globals["DEFAULT_WORKSPACE"])
        provider = provider_key(profile)
        expected_profile = profile_id(profile)
        expected_workspace = normalized_path_key(workspace)
        self.app.native_loading_threads = True
        self.app._render_coding_projects()

        def worker() -> list[dict]:
            if provider == "codex":
                codex_home = Path(str(profile.get("codexHome") or _hub_globals["DEFAULT_CODEX_HOME"]))

                def file_threads() -> list[dict]:
                    return discover_codex_file_threads(codex_home, workspace, limit=100)

                try:
                    reusable = (
                        isinstance(self.app.native_transport, CodexTransport)
                        and self.app.native_transport_key == self.app._native_transport_key(profile, workspace)
                        and self.app.native_transport.alive
                    )
                    transport = self.app.native_transport if reusable else self.app._create_native_transport(profile, workspace)
                    assert isinstance(transport, CodexTransport)
                    if not reusable:
                        self.app._track_pending_transport(transport)
                    try:
                        if not reusable:
                            transport.connect()
                        threads = transport.list_threads(workspace, limit=100)
                    finally:
                        if not reusable:
                            transport.shutdown()
                            self.app._untrack_pending_transport(transport)
                except Exception:
                    fallback = file_threads()
                    if fallback:
                        return fallback
                    raise
                for thread in threads:
                    thread["provider"] = "codex"
                    thread.setdefault("cwd", str(workspace))
                merged = list(threads)
                seen = {str(thread.get("id") or "") for thread in merged}
                for thread in file_threads():
                    thread_id = str(thread.get("id") or "")
                    if thread_id and thread_id in seen:
                        continue
                    merged.append(thread)
                    if thread_id:
                        seen.add(thread_id)

                def sort_value(item: dict) -> float:
                    value = item.get("updatedAt") or item.get("updated_at") or 0
                    try:
                        return float(value)
                    except (TypeError, ValueError):
                        return self.app._timestamp_from_iso(value)

                return sorted(merged, key=sort_value, reverse=True)[:100]
            if provider == "claude":
                roots = [claude_profile_home(profile) / "projects"]
                default_root = _hub_globals["CLAUDE_CLI_HOME"] / "projects"
                if not any(same_local_path(root, default_root) for root in roots):
                    roots.append(default_root)
                discovered: list[dict] = []
                seen_paths: set[str] = set()
                for root in roots:
                    for thread in discover_claude_threads(root, workspace, limit=100):
                        path_key = str(thread.get("path") or thread.get("id") or "").lower()
                        if not path_key or path_key in seen_paths:
                            continue
                        seen_paths.add(path_key)
                        discovered.append(thread)
                if not discovered:
                    for root in roots:
                        for thread in discover_claude_threads(root, None, limit=100):
                            path_key = str(thread.get("path") or thread.get("id") or "").lower()
                            if not path_key or path_key in seen_paths:
                                continue
                            seen_paths.add(path_key)
                            discovered.append(thread)
                discovered = sorted(
                    discovered,
                    key=lambda thread: float(thread.get("updatedAt") or thread.get("updated_at") or 0),
                    reverse=True,
                )[:100]
                refs = load_thread_refs(_hub_globals["NATIVE_THREADS_FILE"])
                seen = {str(thread.get("id") or "") for thread in discovered}
                for ref in refs:
                    session_id = str(ref.get("nativeSessionId") or "")
                    if not session_id or session_id in seen:
                        continue
                    if (
                        str(ref.get("provider") or "") == provider
                        and str(ref.get("profileId") or "") == expected_profile
                    ):
                        discovered.append(
                            {
                                "id": session_id,
                                "provider": provider,
                                "preview": str(ref.get("title") or f"{provider_label(profile)} session"),
                                "cwd": str(ref.get("projectPath") or workspace),
                                "createdAt": 0,
                                "updatedAt": self.app._timestamp_from_iso(ref.get("updatedAt")),
                                "path": "",
                                "status": {"type": "notLoaded"},
                            }
                        )
                        seen.add(session_id)
                return discovered[:100]
            if provider == "cursor":
                discovered = discover_cursor_threads(_hub_globals["CURSOR_HOME"], workspace, limit=100)
                refs = load_thread_refs(_hub_globals["NATIVE_THREADS_FILE"])
                seen = {str(thread.get("id") or "") for thread in discovered}
                for ref in refs:
                    session_id = str(ref.get("nativeSessionId") or "")
                    if not session_id or session_id in seen:
                        continue
                    if (
                        str(ref.get("provider") or "") == provider
                        and str(ref.get("profileId") or "") == expected_profile
                        and self.app._thread_workspace_key(ref) == expected_workspace
                    ):
                        discovered.append(
                            {
                                "id": session_id,
                                "provider": provider,
                                "preview": str(ref.get("title") or f"{provider_label(profile)} session"),
                                "cwd": str(ref.get("projectPath") or ""),
                                "createdAt": 0,
                                "updatedAt": self.app._timestamp_from_iso(ref.get("updatedAt")),
                                "path": "",
                                "status": {"type": "notLoaded"},
                            }
                        )
                        seen.add(session_id)
                return discovered[:100]
            if provider == "antigravity":
                return discover_antigravity_threads(Path.home() / ".gemini" / "antigravity-cli", workspace, limit=100)
            refs = load_thread_refs(_hub_globals["NATIVE_THREADS_FILE"])
            return [
                {
                    "id": str(ref.get("nativeSessionId") or ""),
                    "provider": provider,
                    "preview": str(ref.get("title") or f"{provider_label(profile)} session"),
                    "cwd": str(ref.get("projectPath") or ""),
                    "createdAt": 0,
                    "updatedAt": self.app._timestamp_from_iso(ref.get("updatedAt")),
                    "path": "",
                    "status": {"type": "notLoaded"},
                }
                for ref in refs
                if str(ref.get("provider") or "") == provider
                and str(ref.get("profileId") or "") == expected_profile
                and self.app._thread_workspace_key(ref) == expected_workspace
            ]

        def success(threads: list[dict]) -> None:
            self.app.native_loading_threads = False
            current = self.app.coding_selected_profile()
            if current is None:
                return
            if profile_id(current) != expected_profile or normalized_path_key(self.app.coding_workspace_var.get()) != expected_workspace:
                return
            merged = self.app._merge_saved_thread_refs(threads, current)
            self.app.native_threads = self.app._collapse_native_threads(merged, current)
            self.app._render_coding_projects()

        def run() -> None:
            try:
                result = worker()
            except Exception as error:
                self.app._post_native_ui(lambda value=str(error): self.app._native_threads_failed(value))
                return
            self.app._post_native_ui(lambda value=result: success(value))

        threading.Thread(target=run, name="ai-hub-native-history", daemon=True).start()

    def _native_threads_failed(self, error: str) -> None:
        self.app.native_loading_threads = False
        self.app.native_threads = []
        self.app.native_diagnostics.append(error)
        self.app._render_coding_projects()

    def select_native_thread(self, thread: dict) -> None:
        profile = self.app.coding_selected_profile()
        if profile is None or self.app.native_busy:
            return
        workspace = Path(str(thread.get("cwd") or self.app.coding_workspace_var.get() or _hub_globals["DEFAULT_WORKSPACE"]))
        session_id = str(thread.get("id") or "")
        if not session_id:
            return
        self.app.close_native_transport()
        self.app.native_generation += 1
        generation = self.app.native_generation
        self.app.native_busy = True
        self.app.native_messages = []
        self.app.native_thread_id = session_id
        self.app.native_thread_title = clip_text(thread.get("preview") or thread.get("title") or "", 90)
        self.app.coding_composer_status.configure(text=f"Opening {provider_label(profile)}")
        self.app._render_coding()

        def worker() -> tuple[CodexTransport | StreamJsonTransport | None, list[dict], bool]:
            history_path = Path(str(thread.get("path") or ""))
            if provider_key(profile) == "codex" and thread.get("source") == "codex-file" and history_path.is_file():
                return None, read_codex_session_file(history_path), True
            transport = self.app._create_native_transport(profile, workspace, session_id=session_id)
            self.app._track_pending_transport(transport)
            try:
                if isinstance(transport, CodexTransport):
                    transport.connect()
                    resumed = transport.resume_thread(session_id, workspace)
                    native_thread = resumed.get("thread") if isinstance(resumed.get("thread"), dict) else {}
                    if not native_thread.get("turns"):
                        read = transport.read_thread(session_id)
                        native_thread = read.get("thread") if isinstance(read.get("thread"), dict) else native_thread
                    return transport, codex_thread_messages(native_thread), False
                if provider_key(profile) == "claude":
                    path = Path(str(thread.get("path") or ""))
                    return transport, read_claude_thread(path) if path.is_file() else [], False
                if provider_key(profile) == "cursor":
                    path = Path(str(thread.get("path") or ""))
                    return transport, read_cursor_thread(path) if path.is_file() else [], False
                if provider_key(profile) == "antigravity":
                    return transport, read_antigravity_thread(
                        Path.home() / ".gemini" / "antigravity-cli",
                        session_id,
                    ), False
                return transport, [], False
            except Exception:
                self.app._stop_transport(transport)
                self.app._untrack_pending_transport(transport)
                raise

        def success(result: tuple[CodexTransport | StreamJsonTransport | None, list[dict], bool]) -> None:
            transport, messages, read_only = result
            if transport is not None:
                self.app._untrack_pending_transport(transport)
            if generation != self.app.native_generation:
                if transport is not None:
                    self.app._stop_transport(transport)
                return
            self.app.native_transport = transport
            self.app.native_transport_key = self.app._native_transport_key(profile, workspace) if transport is not None else ""
            self.app.native_thread_id = session_id
            self.app.native_messages = messages
            self.app._restore_native_file_context(messages)
            self.app.native_busy = False
            self.app.coding_session_active = transport is not None
            self.app.coding_workspace_var.set(str(workspace))
            if read_only:
                self.app.status_var.set(f"Opened {provider_label(profile)} history {session_id} read-only.")
                self.app.coding_composer_status.configure(text="History read-only")
            else:
                self.app.status_var.set(f"Resumed {provider_label(profile)} session {session_id}.")
            self.app._render_coding()
            self.app.coding_input.focus_set()

        self.app._run_native_worker(worker, success, generation)

    def _native_event_callback(self, message: dict) -> None:
        self.app._post_native_ui(lambda value=message: self.app._handle_native_event(value))

    def _post_native_ui(self, callback) -> None:
        if self.app._closing:
            return
        self.app.native_ui_queue.put(callback)

    def _drain_native_ui_queue(self) -> None:
        if self.app._closing:
            return
        if self.app._native_queue_after_id:
            try:
                self.app.after_cancel(self.app._native_queue_after_id)
            except tk.TclError:
                pass
            self.app._native_queue_after_id = None
        try:
            while True:
                callback = self.app.native_ui_queue.get_nowait()
                try:
                    callback()
                except Exception as error:
                    self.app.native_diagnostics.append(f"UI event error: {error}")
        except queue.Empty:
            pass
        if not self.app._closing:
            try:
                self.app._native_queue_after_id = self.app.after(50, self.app._drain_native_ui_queue)
            except tk.TclError:
                self.app._native_queue_after_id = None

    def _handle_native_event(self, message: dict) -> None:
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        full_render = method in {"turn/completed", "transport/exited", "error"}
        if message.get("id") is not None and method:
            self.app._handle_native_server_request(message)
            return
        if method == "item/agentMessage/delta":
            self.app._append_native_delta(
                str(params.get("itemId") or "agent"),
                str(params.get("delta") or ""),
                render=False,
            )
        elif method == "item/commandExecution/outputDelta":
            self.app._append_native_activity_delta(
                str(params.get("itemId") or "command"),
                str(params.get("delta") or ""),
                render=False,
            )
        elif method == "item/plan/delta":
            self.app._append_native_activity_delta(
                str(params.get("itemId") or "plan"),
                str(params.get("delta") or ""),
                prefix="Plan",
                render=False,
            )
        elif method == "item/reasoning/summaryTextDelta":
            self.app._append_native_activity_delta(
                str(params.get("itemId") or "reasoning"),
                str(params.get("delta") or ""),
                prefix="Reasoning",
                render=False,
            )
        elif method in {"item/started", "item/completed"}:
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            item_type = str(item.get("type") or "")
            if item_type == "fileChange":
                self.app._capture_native_file_changes(item)
            if item_type in {
                "commandExecution",
                "fileChange",
                "mcpToolCall",
                "dynamicToolCall",
                "collabToolCall",
                "webSearch",
                "imageView",
                "contextCompaction",
                "plan",
            }:
                text = str(item.get("text") or "") if item_type == "plan" else summarize_codex_item(item)
                self.app._upsert_native_activity(
                    str(item.get("id") or item_type),
                    text,
                    render=False,
                    **self.app._codex_activity_fields(item),
                )
        elif method == "turn/started":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            self.app.native_turn_id = str(turn.get("id") or self.app.native_turn_id)
            if isinstance(self.app.native_transport, CodexTransport):
                self.app.native_transport.turn_id = self.app.native_turn_id
            self.app.native_busy = True
        elif method == "turn/completed":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            status = str(turn.get("status") or "completed")
            error = turn.get("error")
            self.app.native_busy = False
            self.app.coding_composer_status.configure(text=f"Turn {status}")
            if error:
                self.app._append_native_message("error", self.app._native_error_text(error), render=False)
            self.app._save_active_native_thread()
            self.app.refresh_native_threads()
        elif method == "turn/plan/updated":
            text = format_codex_plan_update(params.get("plan"), params.get("explanation"))
            if text:
                self.app._upsert_native_activity(
                    "active-plan",
                    text,
                    render=False,
                    kind="plan",
                    title="Plan",
                    plan=params.get("plan") if isinstance(params.get("plan"), list) else [],
                )
        elif method == "turn/diff/updated":
            self.app.native_turn_diff = coding_display_text(params.get("diff") or "")
            if self.app.native_turn_diff.strip():
                self.app._upsert_native_activity(
                    "active-diff",
                    "Current diff",
                    render=False,
                    kind="diff",
                    title="Current diff",
                    diff=self.app.native_turn_diff,
                )
        elif method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage")
            self.app.native_token_usage = usage if isinstance(usage, dict) else dict(params)
        elif method == "skills/changed":
            self.app._clear_native_skills_cache()
            if self.app.coding_details_visible and self.app.coding_context_tab == "skills":
                self.app.refresh_native_skills(force=True)
        elif method == "stream/event":
            provider = str(params.get("provider") or "")
            event = params.get("event") if isinstance(params.get("event"), dict) else {}
            self.app._handle_stream_event(provider, event)
            if str(event.get("type") or "") in {"result", "error"}:
                full_render = True
        elif method == "transport/stderr":
            text = coding_display_text(params.get("text") or "")
            if text:
                self.app.native_diagnostics.append(text)
                self.app.native_diagnostics = self.app.native_diagnostics[-200:]
        elif method == "transport/rawOutput":
            text = coding_display_text(params.get("text") or "")
            if text:
                self.app.native_diagnostics.append(text)
        elif method == "transport/exited":
            exit_code = params.get("exitCode")
            stopped = bool(params.get("stopped"))
            stderr = str(params.get("stderr") or "").strip()
            is_codex_server = isinstance(self.app.native_transport, CodexTransport)
            if not is_codex_server or exit_code not in {None, 0}:
                self.app.native_busy = False
            if exit_code not in {None, 0} and not stopped:
                detail = stderr or f"Native process exited with code {exit_code}."
                self.app._append_native_message("error", detail, render=False)
            if not is_codex_server:
                self.app._capture_stream_session()
                self.app._save_active_native_thread()
                self.app.refresh_native_threads()
        elif method == "error":
            self.app.native_busy = False
            self.app._append_native_message("error", self.app._native_error_text(params), render=False)
        self.app._schedule_native_render(full=full_render)

    def _schedule_native_render(self, full: bool = False) -> None:
        if self.app._closing:
            return
        self.app._native_render_full = self.app._native_render_full or full
        if self.app._native_render_after_id:
            return
        try:
            delay = _hub_globals["CODING_NATIVE_FULL_RENDER_DELAY_MS"] if full else _hub_globals["CODING_NATIVE_RENDER_DELAY_MS"]
            self.app._native_render_after_id = self.app.after(delay, self.app._flush_native_render)
        except tk.TclError:
            self.app._native_render_after_id = None

    def _flush_native_render(self) -> None:
        self.app._native_render_after_id = None
        if self.app._closing:
            return
        full = self.app._native_render_full
        self.app._native_render_full = False
        if full:
            self.app._render_coding()
            return
        self.app._render_coding_stream()
        if self.app.coding_details_visible:
            self.app._render_coding_context()

    def _handle_stream_event(self, provider: str, event: dict) -> None:
        event_type = str(event.get("type") or "")
        session_id = event.get("session_id") or event.get("sessionId")
        if session_id:
            self.app.native_thread_id = str(session_id)
        if provider == "claude":
            if event_type == "rate_limit_event":
                info = event.get("rate_limit_info") if isinstance(event.get("rate_limit_info"), dict) else {}
                self.app._upsert_native_activity(
                    "claude-rate-limit",
                    self.app._format_claude_rate_limit_event(info),
                    render=False,
                    kind="notice",
                    title="Claude limit",
                )
                self.app._apply_claude_rate_limit_event(info)
            elif event_type == "stream_event":
                stream = event.get("event") if isinstance(event.get("event"), dict) else {}
                delta = stream.get("delta") if isinstance(stream.get("delta"), dict) else {}
                if delta.get("type") == "text_delta":
                    self.app._append_native_delta("claude-assistant", str(delta.get("text") or ""), render=False)
            elif event_type == "assistant":
                message = event.get("message") if isinstance(event.get("message"), dict) else {}
                content = message.get("content") if isinstance(message.get("content"), list) else []
                image_refs = claude_content_image_refs(content)
                if image_refs:
                    self.app._upsert_native_activity(
                        str(event.get("uuid") or message.get("id") or "claude-image"),
                        "Image",
                        render=False,
                        kind="image",
                        title="Image",
                        imageRefs=image_refs,
                    )
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = str(block.get("name") or "Claude tool")
                        fields = claude_tool_activity_fields(name, block.get("input"))
                        fields.setdefault("kind", "tool")
                        fields.setdefault("title", name)
                        fields.setdefault("status", "requested")
                        self.app._capture_activity_file_fields(fields, "requested")
                        self.app._upsert_native_activity(
                            str(block.get("id") or name),
                            self.app._claude_tool_activity_text(name, block.get("input")),
                            render=False,
                            **fields,
                        )
            elif event_type == "user":
                message = event.get("message") if isinstance(event.get("message"), dict) else {}
                content = message.get("content") if isinstance(message.get("content"), list) else []
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    result_text = claude_tool_result_text(event, block)
                    fields = claude_tool_result_fields(event, block, result_text)
                    fields.setdefault("kind", "result")
                    fields.setdefault("title", "Tool result")
                    fields.setdefault("status", "completed")
                    self.app._capture_activity_file_fields(fields, "completed")
                    native_id = str(block.get("id") or block.get("tool_use_id") or event.get("uuid") or "claude-result")
                    self.app._upsert_native_activity(
                        f"{native_id}:result",
                        result_text,
                        render=False,
                        **fields,
                    )
            elif event_type == "system" and event.get("subtype") == "api_retry":
                attempt = event.get("attempt")
                maximum = event.get("max_retries")
                self.app._upsert_native_activity(
                    "claude-retry",
                    f"Retrying request {attempt}/{maximum}",
                    render=False,
                    kind="notice",
                    title="Claude retry",
                )
            elif event_type == "system" and event.get("subtype") == "status":
                permission_mode = str(event.get("permissionMode") or "").strip()
                if permission_mode:
                    text = "Claude plan mode active" if permission_mode == "plan" else f"Claude permission mode: {permission_mode}"
                    self.app._upsert_native_activity(
                        "claude-permission-mode",
                        f"Plan\n{text}",
                        render=False,
                        kind="plan",
                        title="Plan",
                    )
            elif event_type == "result":
                result_text = coding_display_text(event.get("result") or "")
                if result_text:
                    self.app._finish_native_assistant_message(
                        "claude-assistant",
                        "claude-result",
                        result_text,
                        render=False,
                    )
                self.app.native_busy = False
        elif provider == "cursor":
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            content = message.get("content") if isinstance(message.get("content"), list) else event.get("content")
            text = (
                str(event.get("delta") or event.get("text") or event.get("response") or "")
                or extract_message_text(content)
            )
            role = str(event.get("role") or message.get("role") or "").lower()
            if text and (
                event_type in {"assistant", "assistant_message", "assistantMessage", "message", "text", "chunk", "output"}
                or role == "assistant"
            ):
                self.app._append_native_delta("cursor-assistant", text, render=False)
            elif event_type == "assistant":
                message = event.get("message") if isinstance(event.get("message"), dict) else {}
                content = message.get("content") if isinstance(message.get("content"), list) else []
                text = "".join(
                    str(block.get("text") or "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
                self.app._append_native_delta("cursor-assistant", text, render=False)
            elif event_type in {"tool_call", "toolCall", "tool_use", "toolUse"} or isinstance(event.get("tool_call") or event.get("toolCall") or event.get("tool"), dict):
                call = (
                    event.get("tool_call")
                    if isinstance(event.get("tool_call"), dict)
                    else event.get("toolCall")
                    if isinstance(event.get("toolCall"), dict)
                    else event.get("tool")
                    if isinstance(event.get("tool"), dict)
                    else {}
                )
                tool_name = str(call.get("name") or call.get("tool") or next(iter(call), "Cursor tool"))
                state = str(event.get("subtype") or "")
                self.app._upsert_native_activity(
                    str(event.get("call_id") or tool_name),
                    f"{tool_name}\n{state}",
                    render=False,
                    kind="tool",
                    title=tool_name,
                    status=state,
                )
            elif event_type in {"error", "fatal"} or isinstance(event.get("error"), dict):
                error = event.get("error") if isinstance(event.get("error"), dict) else event
                self.app._append_native_message("error", self.app._native_error_text(error), render=False)
                self.app.native_busy = False
            elif event_type in {"result", "done", "complete", "completed"}:
                self.app.native_busy = False
        elif provider == "antigravity":
            text = str(event.get("text") or event.get("message") or "")
            if text and event_type == "assistant":
                self.app._append_native_message(
                    "assistant",
                    text,
                    native_id=str(event.get("native_id") or "antigravity-assistant"),
                    render=False,
                )
            elif event_type == "error":
                self.app._append_native_message("error", text or "Antigravity turn failed.", render=False)
                self.app.native_busy = False

    def _handle_native_server_request(self, message: dict) -> None:
        transport = self.app.native_transport
        if not isinstance(transport, CodexTransport):
            return
        request_id = message.get("id")
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            command = str(params.get("command") or "")
            reason = str(params.get("reason") or "")
            subject = command or reason or ("Apply file changes" if "fileChange" in method else "Run native command")
            self.app._upsert_native_activity(str(request_id), f"Approval requested\n{subject}", render=False)
            self.app._schedule_native_render(full=False)
            self.app.update_idletasks()
            decision = self.app._native_request_dialog(
                "Native approval request",
                subject,
                [
                    ("Allow once", "accept", "Approve this native action once."),
                    ("Decline", "decline", "Refuse the action and let the provider continue."),
                    ("Cancel request", "cancel", "Cancel the provider request."),
                ],
                allow_text=False,
            )
            transport.respond(request_id, {"decision": decision or "cancel"})
            return
        if method == "item/tool/requestUserInput":
            answers: dict[str, dict] = {}
            for question in params.get("questions") or []:
                if not isinstance(question, dict):
                    continue
                prompt = str(question.get("question") or question.get("header") or "Input required")
                options = question.get("options") if isinstance(question.get("options"), list) else []
                choices = [
                    (
                        str(option.get("label") or ""),
                        str(option.get("label") or ""),
                        str(option.get("description") or ""),
                    )
                    for option in options
                    if isinstance(option, dict) and str(option.get("label") or "").strip()
                ]
                self.app._upsert_native_activity(str(request_id), f"Input requested\n{prompt}", render=False)
                self.app._schedule_native_render(full=False)
                self.app.update_idletasks()
                value = self.app._native_request_dialog(
                    str(question.get("header") or "Native question"),
                    prompt,
                    choices,
                    secret=bool(question.get("isSecret")),
                    allow_text=True,
                )
                answers[str(question.get("id") or "")] = {"answers": [] if value is None else [value]}
            transport.respond(request_id, {"answers": answers})
            return
        transport.respond_error(request_id, f"AI Account Hub does not automatically grant unsupported request: {method}")

    def _capture_stream_session(self) -> None:
        transport = self.app.native_transport
        if isinstance(transport, StreamJsonTransport) and transport.session_id:
            self.app.native_thread_id = transport.session_id

    def _save_active_native_thread(self) -> None:
        profile = self.app.coding_selected_profile()
        if profile is None:
            return
        self.app._capture_stream_session()
        if not self.app.native_thread_id:
            return
        workspace = Path(self.app.coding_workspace_var.get() or _hub_globals["DEFAULT_WORKSPACE"])
        title = next(
            (str(message.get("text") or "").strip() for message in self.app.native_messages if message.get("role") == "user"),
            "Native thread",
        )
        self.app._save_native_thread_ref(profile, workspace, self.app.native_thread_id, title[:120])

    def _save_native_thread_ref(self, profile: dict, workspace: Path, session_id: str, title: str) -> None:
        native_home = Path(str(profile.get("codexHome"))) if provider_key(profile) == "codex" and profile.get("codexHome") else None
        upsert_thread_ref(
            _hub_globals["NATIVE_THREADS_FILE"],
            thread_ref(
                provider_key(profile),
                profile_id(profile),
                workspace,
                session_id,
                title,
                native_home=native_home,
            ),
        )

    def _append_native_message(
        self,
        role: str,
        text: str,
        native_id: str = "",
        render: bool = True,
        **fields,
    ) -> None:
        text = coding_display_text(text)
        if not text and not fields.get("imageRefs"):
            return
        if native_id:
            for message in reversed(self.app.native_messages):
                if message.get("role") == role and message.get("nativeId") == native_id:
                    message["text"] = text
                    message.update(fields)
                    if render:
                        self.app._render_coding_stream()
                    return
        if self.app.native_messages:
            previous = self.app.native_messages[-1]
            if previous.get("role") == role and previous.get("text") == text:
                return
        message = {
            "role": role,
            "text": text,
            "nativeId": native_id,
            "timestamp": iso_utc_now(),
        }
        message.update(fields)
        self.app.native_messages.append(message)
        if role == "user" and not self.app.native_thread_title:
            body, _attachments = coding_user_message_parts(text)
            self.app.native_thread_title = clip_text(body, 90) or "New thread"
            if hasattr(self.app, "coding_title"):
                self.app.coding_title.configure(text=self.app.native_thread_title)
        if render:
            self.app._render_coding_stream()

    def _append_native_delta(self, native_id: str, delta: str, render: bool = True) -> None:
        delta = coding_display_text(delta)
        if not delta:
            return
        for message in reversed(self.app.native_messages):
            if message.get("role") == "assistant" and message.get("nativeId") == native_id:
                message["text"] = coding_display_text(message.get("text") or "") + delta
                break
        else:
            self.app._append_native_message("assistant", delta, native_id=native_id, render=False)
        if render:
            self.app._render_coding_stream()

    def _finish_native_assistant_message(
        self,
        stream_native_id: str,
        result_native_id: str,
        text: str,
        render: bool = True,
    ) -> None:
        text = coding_display_text(text)
        if not text:
            return
        for message in reversed(self.app.native_messages):
            if message.get("role") != "assistant":
                continue
            current = coding_display_text(message.get("text") or "")
            same_stream = message.get("nativeId") == stream_native_id
            same_text = current and (text.startswith(current) or current.startswith(text))
            if same_stream or same_text:
                message["text"] = text if len(text) >= len(current) else current
                message["nativeId"] = result_native_id or stream_native_id
                if render:
                    self.app._render_coding_stream()
                return
        self.app._append_native_message("assistant", text, native_id=result_native_id, render=render)

    def _upsert_native_activity(self, native_id: str, text: str, render: bool = True, **fields) -> None:
        text = coding_display_text(text)
        if not text and not fields:
            return
        for message in reversed(self.app.native_messages):
            if message.get("role") == "activity" and message.get("nativeId") == native_id:
                message["text"] = text
                message.update(fields)
                break
        else:
            self.app._append_native_message("activity", text, native_id=native_id, render=False, **fields)
        if render:
            self.app._render_coding_stream()

    def _append_native_activity_delta(
        self,
        native_id: str,
        delta: str,
        prefix: str = "Output",
        render: bool = True,
    ) -> None:
        delta = coding_display_text(delta)
        if not delta:
            return
        for message in reversed(self.app.native_messages):
            if message.get("role") == "activity" and message.get("nativeId") == native_id:
                text = coding_display_text(message.get("text") or "")
                marker = f"\n{prefix}\n"
                if not text.startswith(f"{prefix}\n") and marker not in text:
                    text = text.rstrip() + marker
                message["text"] = text + delta
                break
        else:
            self.app._append_native_message(
                "activity",
                f"{prefix}\n{delta}",
                native_id=native_id,
                render=False,
            )
        if render:
            self.app._render_coding_stream()

    def _capture_native_file_changes(self, item: dict) -> None:
        status = str(item.get("status") or "")
        for change in item.get("changes") or []:
            if not isinstance(change, dict):
                continue
            path = str(change.get("path") or "").strip()
            if not path:
                continue
            captured = {
                "path": path,
                "kind": str(change.get("kind") or "update"),
                "diff": coding_display_text(change.get("diff") or ""),
                "status": status,
            }
            for index, existing in enumerate(self.app.native_file_changes):
                if same_local_path(existing.get("path"), path):
                    self.app.native_file_changes[index] = captured
                    break
            else:
                self.app.native_file_changes.append(captured)

    def _capture_activity_file_fields(self, fields: dict, status: str = "") -> None:
        changes = fields.get("changes") if isinstance(fields.get("changes"), list) else []
        diff_text = coding_display_text(fields.get("diff") or "")
        for change in changes:
            if not isinstance(change, dict):
                continue
            path = str(change.get("path") or "").strip()
            if not path:
                continue
            captured = {
                "path": path,
                "kind": str(change.get("kind") or "update"),
                "diff": diff_text or coding_display_text(change.get("diff") or ""),
                "status": status or str(fields.get("status") or ""),
            }
            for index, existing in enumerate(self.app.native_file_changes):
                if same_local_path(existing.get("path"), path):
                    self.app.native_file_changes[index] = captured
                    break
            else:
                self.app.native_file_changes.append(captured)
        if diff_text:
            self.app.native_turn_diff = diff_text

    def _restore_native_file_context(self, messages: list[dict]) -> None:
        self.app.native_file_changes = []
        self.app.native_turn_diff = ""
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "activity":
                continue
            self.app._capture_activity_file_fields(message, str(message.get("status") or ""))

    def _native_error_text(self, error: object) -> str:
        if isinstance(error, str):
            return error
        if isinstance(error, dict):
            nested = error.get("error")
            if isinstance(nested, (dict, str)):
                return self.app._native_error_text(nested)
            return str(error.get("message") or error.get("codexErrorInfo") or json.dumps(error, ensure_ascii=False))
        return str(error)

    def _timestamp_from_iso(self, value: object) -> float:
        parsed = parse_iso_datetime(value)
        return parsed.timestamp() if parsed is not None else 0.0
