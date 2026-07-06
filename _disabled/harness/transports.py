"""Native provider transports: the Codex app-server JSON-RPC process and the Claude / Cursor stream-json + Antigravity print-mode transports."""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_logger = logging.getLogger("native_harness")

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
EventCallback = Callable[[dict], None]

from ai_account_hub.harness.history import (
    antigravity_cli_home,
    antigravity_last_conversation_id,
    read_antigravity_thread,
)

def close_process_streams(process: subprocess.Popen[str]) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except OSError:
                pass


class NativeTransportError(RuntimeError):
    pass


@dataclass
class PendingRequest:
    event: threading.Event = field(default_factory=threading.Event)
    message: dict | None = None
    error: BaseException | None = None


class JsonRpcProcess:
    """Small JSONL JSON-RPC client used by native harness protocol servers."""

    def __init__(
        self,
        command: list[str],
        cwd: Path,
        env: dict[str, str] | None,
        event_callback: EventCallback,
    ) -> None:
        self.command = list(command)
        self.cwd = Path(cwd)
        self.env = dict(env or os.environ)
        self.event_callback = event_callback
        self.process: subprocess.Popen[str] | None = None
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, PendingRequest] = {}
        self._request_id = 0
        self._stopping = False
        self._threads: list[threading.Thread] = []

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> None:
        if self.alive:
            return
        self._stopping = False
        self.process = subprocess.Popen(
            self.command,
            cwd=str(self.cwd),
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        self._threads = [
            threading.Thread(target=self._read_stdout, name="native-jsonrpc-stdout", daemon=True),
            threading.Thread(target=self._read_stderr, name="native-jsonrpc-stderr", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def request(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        if not self.alive:
            raise NativeTransportError("Native protocol process is not running.")
        with self._pending_lock:
            self._request_id += 1
            request_id = self._request_id
            pending = PendingRequest()
            self._pending[request_id] = pending
        self._send({"method": method, "id": request_id, "params": params or {}})
        if not pending.event.wait(timeout):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise NativeTransportError(f"Native request timed out: {method}")
        if pending.error is not None:
            raise NativeTransportError(str(pending.error))
        message = pending.message or {}
        if message.get("error"):
            error = message["error"]
            detail = error.get("message") if isinstance(error, dict) else str(error)
            raise NativeTransportError(f"{method}: {detail}")
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"method": method, "params": params or {}})

    def respond(self, request_id: int | str, result: dict | None = None, error: dict | None = None) -> None:
        message: dict = {"id": request_id}
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result or {}
        self._send(message)

    def stop(self) -> None:
        self._stopping = True
        process = self.process
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=1)
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass
        self._fail_pending(NativeTransportError("Native protocol process stopped."))

    def _send(self, message: dict) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise NativeTransportError("Native protocol process is not available.")
        payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
        with self._write_lock:
            try:
                process.stdin.write(payload + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                raise NativeTransportError(f"Could not write to native protocol process: {error}") from error

    def _read_stdout(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        try:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._emit({"method": "transport/rawOutput", "params": {"text": line}})
                    continue
                if not isinstance(message, dict):
                    continue
                request_id = message.get("id")
                is_response = request_id is not None and "method" not in message
                if is_response:
                    with self._pending_lock:
                        pending = self._pending.pop(request_id, None)
                    if pending is not None:
                        pending.message = message
                        pending.event.set()
                    continue
                self._emit(message)
        finally:
            code = process.poll()
            if code is None:
                try:
                    code = process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    code = None
            if not self._stopping:
                self._emit({"method": "transport/exited", "params": {"exitCode": code}})
            self._fail_pending(NativeTransportError(f"Native protocol process exited with code {code}."))

    def _read_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        for raw_line in process.stderr:
            line = raw_line.rstrip()
            if line:
                self._emit({"method": "transport/stderr", "params": {"text": line}})

    def _emit(self, message: dict) -> None:
        try:
            self.event_callback(message)
        except Exception:
            _logger.exception("event_callback failed for native event %r", message.get("method"))

    def _fail_pending(self, error: BaseException) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            item.error = error
            item.event.set()


class CodexTransport:
    provider = "codex"

    def __init__(
        self,
        executable: str,
        codex_home: Path,
        cwd: Path,
        event_callback: EventCallback,
    ) -> None:
        self.executable = str(executable)
        self.codex_home = Path(codex_home)
        self.cwd = Path(cwd)
        self.event_callback = event_callback
        self.thread_id = ""
        self.turn_id = ""
        env = os.environ.copy()
        env["CODEX_HOME"] = str(self.codex_home)
        self.client = JsonRpcProcess(
            [self.executable, "app-server", "--listen", "stdio://"],
            self.cwd,
            env,
            event_callback,
        )
        self.initialize_result: dict = {}

    @property
    def alive(self) -> bool:
        return self.client.alive

    def connect(self) -> dict:
        self.client.start()
        self.initialize_result = self.client.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "ai_account_hub",
                    "title": "AI Account Hub",
                    "version": "0.2.0",
                },
                "capabilities": {"experimentalApi": True},
            },
            timeout=15,
        )
        self.client.notify("initialized", {})
        return self.initialize_result

    def list_threads(self, cwd: Path | None = None, limit: int = 100) -> list[dict]:
        params: dict = {
            "limit": limit,
            "sortKey": "updated_at",
            "sortDirection": "desc",
        }
        if cwd is not None:
            params["cwd"] = str(Path(cwd))
        result = self.client.request("thread/list", params, timeout=30)
        data = result.get("data")
        return data if isinstance(data, list) else []

    def list_models(self, include_hidden: bool = False, limit: int = 100) -> list[dict]:
        result = self.client.request(
            "model/list",
            {"includeHidden": include_hidden, "limit": limit},
            timeout=30,
        )
        data = result.get("data")
        return data if isinstance(data, list) else []

    def list_skills(self, cwd: Path | None = None, force_reload: bool = False) -> list[dict]:
        params: dict = {}
        if cwd is not None:
            params["cwds"] = [str(Path(cwd))]
        if force_reload:
            params["forceReload"] = True
        result = self.client.request("skills/list", params, timeout=30)
        data = result.get("data")
        return data if isinstance(data, list) else []

    def write_skill_config(self, enabled: bool, name: str = "", path: str = "") -> dict:
        params: dict = {"enabled": bool(enabled)}
        if name:
            params["name"] = name
        if path:
            params["path"] = path
        return self.client.request("skills/config/write", params, timeout=30)

    def update_thread_settings(self, **settings) -> dict:
        if not self.thread_id:
            raise NativeTransportError("Start or resume a Codex thread before changing settings.")
        params = {"threadId": self.thread_id}
        params.update({key: value for key, value in settings.items() if value is not None})
        return self.client.request("thread/settings/update", params, timeout=30)

    def get_goal(self) -> dict:
        if not self.thread_id:
            raise NativeTransportError("Start or resume a Codex thread before reading a goal.")
        return self.client.request("thread/goal/get", {"threadId": self.thread_id}, timeout=30)

    def set_goal(
        self,
        objective: str | None = None,
        status: str | None = None,
        token_budget: int | None = None,
    ) -> dict:
        if not self.thread_id:
            raise NativeTransportError("Start or resume a Codex thread before setting a goal.")
        params: dict = {"threadId": self.thread_id}
        if objective is not None:
            params["objective"] = objective
        if status is not None:
            params["status"] = status
        if token_budget is not None:
            params["tokenBudget"] = token_budget
        return self.client.request("thread/goal/set", params, timeout=30)

    def clear_goal(self) -> dict:
        if not self.thread_id:
            raise NativeTransportError("Start or resume a Codex thread before clearing a goal.")
        return self.client.request("thread/goal/clear", {"threadId": self.thread_id}, timeout=30)

    def start_thread(
        self,
        cwd: Path | None = None,
        model: str = "",
        approval_policy: str = "on-request",
        sandbox: str = "workspace-write",
        personality: str = "",
    ) -> dict:
        params = {
            "cwd": str(Path(cwd or self.cwd)),
            "approvalPolicy": approval_policy,
            "sandbox": sandbox,
            "threadSource": "aiAccountHub",
        }
        if model:
            params["model"] = model
        if personality:
            params["personality"] = personality
        result = self.client.request("thread/start", params, timeout=30)
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
        self.thread_id = str(thread.get("id") or "")
        if not self.thread_id:
            raise NativeTransportError("Codex did not return a native thread id.")
        return result

    def resume_thread(self, thread_id: str, cwd: Path | None = None) -> dict:
        params: dict = {"threadId": thread_id}
        if cwd is not None:
            params["cwd"] = str(Path(cwd))
        result = self.client.request("thread/resume", params, timeout=30)
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
        self.thread_id = str(thread.get("id") or thread_id)
        return result

    def read_thread(self, thread_id: str) -> dict:
        return self.client.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": True},
            timeout=30,
        )

    def start_turn(
        self,
        text: str,
        attachments: list[Path] | None = None,
        model: str = "",
        effort: str = "",
        approval_policy: str = "",
        sandbox_policy: dict | None = None,
        personality: str = "",
        collaboration_mode: dict | None = None,
    ) -> dict:
        if not self.thread_id:
            raise NativeTransportError("Start or resume a Codex thread before sending a message.")
        inputs: list[dict] = [{"type": "text", "text": text, "text_elements": []}]
        for path in attachments or []:
            path = Path(path)
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                inputs.append({"type": "localImage", "path": str(path)})
            else:
                inputs.append({"type": "mention", "name": path.name, "path": str(path)})
        params: dict = {
            "threadId": self.thread_id,
            "input": inputs,
            "cwd": str(self.cwd),
        }
        if model:
            params["model"] = model
        if effort:
            params["effort"] = effort
        if personality:
            params["personality"] = personality
        if collaboration_mode:
            params["collaborationMode"] = collaboration_mode
        if approval_policy:
            params["approvalPolicy"] = approval_policy
        if sandbox_policy:
            params["sandboxPolicy"] = sandbox_policy
        result = self.client.request("turn/start", params, timeout=30)
        turn = result.get("turn") if isinstance(result.get("turn"), dict) else {}
        self.turn_id = str(turn.get("id") or "")
        return result

    def interrupt(self) -> None:
        if self.thread_id and self.turn_id:
            self.client.request(
                "turn/interrupt",
                {"threadId": self.thread_id, "turnId": self.turn_id},
                timeout=10,
            )

    def respond(self, request_id: int | str, result: dict) -> None:
        self.client.respond(request_id, result=result)

    def respond_error(self, request_id: int | str, message: str) -> None:
        self.client.respond(
            request_id,
            error={"code": -32000, "message": message},
        )

    def shutdown(self) -> None:
        self.client.stop()


class StreamJsonTransport:
    """One native provider process per turn, preserving its native session id."""

    def __init__(
        self,
        provider: str,
        executable: str,
        cwd: Path,
        event_callback: EventCallback,
        env: dict[str, str] | None = None,
        session_id: str = "",
        model: str = "",
        effort: str = "",
        access_mode: str = "default",
    ) -> None:
        self.provider = provider
        self.executable = str(executable)
        self.cwd = Path(cwd)
        self.event_callback = event_callback
        self.env = dict(env or os.environ)
        self.session_id = session_id
        self.model = model
        self.effort = effort
        self.access_mode = access_mode
        self.process: subprocess.Popen[str] | None = None
        self._stopping = False

    def set_options(self, model: str = "", effort: str = "", access_mode: str = "default") -> None:
        self.model = model
        self.effort = effort
        self.access_mode = access_mode

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def send(self, text: str) -> int:
        if self.alive:
            raise NativeTransportError(f"{self.provider.title()} is already running a turn.")
        args, stdin_text = self._command_for_prompt(text)
        self._stopping = False
        self.process = subprocess.Popen(
            args,
            cwd=str(self.cwd),
            env=self.env,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        stderr_lines: queue.Queue[str] = queue.Queue()
        stderr_thread = threading.Thread(target=self._read_stderr, args=(stderr_lines,), daemon=True)
        stderr_thread.start()
        try:
            if stdin_text is not None and self.process.stdin is not None:
                self.process.stdin.write(stdin_text)
                self.process.stdin.close()
            if self.process.stdout is not None:
                for raw_line in self.process.stdout:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        self._emit({"method": "stream/rawOutput", "params": {"provider": self.provider, "text": line}})
                        continue
                    if isinstance(event, dict):
                        self._capture_session_id(event)
                        self._emit({"method": "stream/event", "params": {"provider": self.provider, "event": event}})
            code = self.process.wait()
        except BaseException:
            self.stop()
            raise
        finally:
            stderr_thread.join(timeout=2)
            close_process_streams(self.process)
        stderr: list[str] = []
        while not stderr_lines.empty():
            stderr.append(stderr_lines.get_nowait())
        self._emit(
            {
                "method": "transport/exited",
                "params": {
                    "provider": self.provider,
                    "exitCode": code,
                    "stderr": "\n".join(stderr[-20:]),
                    "stopped": self._stopping,
                },
            }
        )
        return code

    def stop(self) -> None:
        self._stopping = True
        process = self.process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    def _command_for_prompt(self, text: str) -> tuple[list[str], str | None]:
        if self.provider == "claude":
            permission_mode = {
                "accept-edits": "acceptEdits",
                "auto": "auto",
                "full-access": "bypassPermissions",
                "dont-ask": "dontAsk",
                "plan": "plan",
            }.get(self.access_mode, "default")
            args = [
                self.executable,
                "-p",
                "--input-format",
                "text",
                "--output-format",
                "stream-json",
                "--verbose",
                "--include-partial-messages",
                "--permission-mode",
                permission_mode,
            ]
            if permission_mode == "bypassPermissions":
                args.append("--allow-dangerously-skip-permissions")
            if self.model:
                args.extend(["--model", self.model])
            if self.effort:
                args.extend(["--effort", self.effort])
            permission_url = str(self.env.get("AI_HUB_PERMISSION_URL") or "").strip()
            bridge_path = str(self.env.get("AI_HUB_PERMISSION_BRIDGE_PATH") or "").strip()
            if permission_url and bridge_path:
                python_exe = str(self.env.get("AI_HUB_PYTHON") or sys.executable)
                mcp_config = {
                    "mcpServers": {
                        "ai-account-hub-permissions": {
                            "command": python_exe,
                            "args": [bridge_path],
                            "env": {
                                "AI_HUB_PERMISSION_URL": permission_url,
                                "AI_HUB_PERMISSION_TOKEN": str(self.env.get("AI_HUB_PERMISSION_TOKEN") or ""),
                            },
                        }
                    }
                }
                args.extend(["--mcp-config", json.dumps(mcp_config, separators=(",", ":"))])
                args.extend(["--permission-prompt-tool", "mcp__ai-account-hub-permissions__mcp_auth_tool"])
            if self.session_id:
                args.extend(["--resume", self.session_id])
            else:
                self.session_id = str(uuid.uuid4())
                args.extend(["--session-id", self.session_id])
            return args, text
        if self.provider == "cursor":
            args = [
                self.executable,
                "--print",
                "--output-format",
                "stream-json",
                "--stream-partial-output",
                "--trust",
            ]
            if self.model:
                args.extend(["--model", self.model])
            if self.access_mode == "plan":
                args.extend(["--mode", "plan"])
            elif self.access_mode == "ask":
                args.extend(["--mode", "ask"])
            elif self.access_mode == "full-access":
                args.extend(["--force", "--sandbox", "disabled"])
            if self.session_id:
                args.extend(["--resume", self.session_id])
            args.append(text)
            return args, None
        raise NativeTransportError(f"No stream-json command is defined for {self.provider}.")

    def _capture_session_id(self, event: dict) -> None:
        value = event.get("session_id") or event.get("sessionId") or event.get("chatId") or event.get("conversationId")
        if value:
            self.session_id = str(value)

    def _read_stderr(self, target: queue.Queue[str]) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        for raw_line in process.stderr:
            line = raw_line.rstrip()
            if line:
                target.put(line)
                self._emit(
                    {
                        "method": "transport/stderr",
                        "params": {"provider": self.provider, "text": line},
                    }
                )

    def _emit(self, message: dict) -> None:
        try:
            self.event_callback(message)
        except Exception:
            _logger.exception("event_callback failed for native event %r", message.get("method"))


class AntigravityTransport(StreamJsonTransport):
    """Native agy print-mode transport backed by Antigravity's own transcript."""

    def __init__(
        self,
        executable: str,
        cwd: Path,
        event_callback: EventCallback,
        session_id: str = "",
        cli_home: Path | None = None,
        model: str = "",
        access_mode: str = "default",
        print_timeout: str = "5m",
    ) -> None:
        super().__init__(
            "antigravity",
            executable,
            cwd,
            event_callback,
            session_id=session_id,
            model=model,
            access_mode=access_mode,
        )
        self.cli_home = Path(cli_home) if cli_home is not None else antigravity_cli_home()
        self.print_timeout = print_timeout or "5m"

    def send(self, text: str) -> int:
        if self.alive:
            raise NativeTransportError("Antigravity is already running a turn.")
        before = read_antigravity_thread(self.cli_home, self.session_id) if self.session_id else []
        args = [self.executable]
        if self.session_id:
            args.extend(["--conversation", self.session_id])
        if self.model:
            args.extend(["--model", self.model])
        if self.access_mode == "sandbox":
            args.append("--sandbox")
        elif self.access_mode == "full-access":
            args.append("--dangerously-skip-permissions")
        args.extend(["--print", text, "--print-timeout", self.print_timeout])
        self._stopping = False
        self.process = subprocess.Popen(
            args,
            cwd=str(self.cwd),
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        try:
            stdout, stderr = self.process.communicate()
            code = self.process.returncode
        finally:
            close_process_streams(self.process)
        detected = antigravity_last_conversation_id(self.cli_home, self.cwd)
        if detected:
            self.session_id = detected
        after = read_antigravity_thread(self.cli_home, self.session_id) if self.session_id else []
        existing_ids = {str(message.get("nativeId") or "") for message in before}
        emitted = 0
        for message in after:
            if message.get("role") != "assistant":
                continue
            native_id = str(message.get("nativeId") or "")
            if native_id and native_id in existing_ids:
                continue
            self._emit(
                {
                    "method": "stream/event",
                    "params": {
                        "provider": self.provider,
                        "event": {
                            "type": "assistant",
                            "text": str(message.get("text") or ""),
                            "session_id": self.session_id,
                            "native_id": native_id,
                        },
                    },
                }
            )
            emitted += 1
        if stdout.strip():
            self._emit(
                {
                    "method": "stream/rawOutput",
                    "params": {"provider": self.provider, "text": stdout.strip()},
                }
            )
        if code == 0 and emitted == 0:
            self._emit(
                {
                    "method": "stream/event",
                    "params": {
                        "provider": self.provider,
                        "event": {
                            "type": "error",
                            "session_id": self.session_id,
                            "message": "Antigravity completed without exposing response text.",
                        },
                    },
                }
            )
        self._emit(
            {
                "method": "transport/exited",
                "params": {
                    "provider": self.provider,
                    "exitCode": code,
                    "stderr": stderr.strip(),
                    "stopped": self._stopping,
                },
            }
        )
        return int(code or 0)




__all__ = [
    "close_process_streams",
    "NativeTransportError",
    "PendingRequest",
    "JsonRpcProcess",
    "CodexTransport",
    "StreamJsonTransport",
    "AntigravityTransport",
]
