from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "ai_account_hub" / "harness" / "native_harness.py"
SPEC = importlib.util.spec_from_file_location("native_harness", MODULE_PATH)
native = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = native
SPEC.loader.exec_module(native)


class NativeHarnessTests(unittest.TestCase):
    def test_codex_command_summary_uses_authoritative_completed_output(self) -> None:
        summary = native.summarize_codex_item(
            {
                "type": "commandExecution",
                "command": "pytest -q",
                "status": "completed",
                "aggregatedOutput": "\x1b[32;1m3 passed\x1b[0m",
                "exitCode": 0,
                "durationMs": 42,
            }
        )
        self.assertIn("completed | exit 0 | 42 ms", summary)
        self.assertIn("3 passed", summary)
        self.assertNotIn("\x1b", summary)

    def test_json_rpc_process_initializes_requests_and_receives_events(self) -> None:
        server = textwrap.dedent(
            """
            import json, sys
            for line in sys.stdin:
                message = json.loads(line)
                method = message.get("method")
                if method == "initialize":
                    print(json.dumps({"id": message["id"], "result": {"userAgent": "fake"}}), flush=True)
                elif method == "thread/list":
                    print(json.dumps({"method": "thread/started", "params": {"thread": {"id": "thr_fake"}}}), flush=True)
                    print(json.dumps({"id": message["id"], "result": {"data": [{"id": "thr_fake"}]}}), flush=True)
            """
        )
        events: list[dict] = []
        client = native.JsonRpcProcess(
            [sys.executable, "-u", "-c", server],
            Path.cwd(),
            None,
            events.append,
        )
        try:
            client.start()
            result = client.request("initialize", {"clientInfo": {}}, timeout=5)
            self.assertEqual(result["userAgent"], "fake")
            listed = client.request("thread/list", {}, timeout=5)
            self.assertEqual(listed["data"][0]["id"], "thr_fake")
            self.assertTrue(native.wait_until(lambda: bool(events)))
            self.assertEqual(events[0]["method"], "thread/started")
        finally:
            client.stop()

    def test_claude_history_discovery_and_read_use_native_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects" / "project"
            root.mkdir(parents=True)
            session = root / "11111111-1111-1111-1111-111111111111.jsonl"
            rows = [
                {
                    "type": "user",
                    "sessionId": session.stem,
                    "uuid": "user-1",
                    "timestamp": "2026-07-01T00:00:00Z",
                    "cwd": str(Path(tmp) / "workspace"),
                    "message": {"role": "user", "content": "Inspect this project"},
                },
                {
                    "type": "assistant",
                    "sessionId": session.stem,
                    "uuid": "assistant-1",
                    "timestamp": "2026-07-01T00:00:01Z",
                    "cwd": str(Path(tmp) / "workspace"),
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Done"}],
                    },
                },
            ]
            session.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            threads = native.discover_claude_threads(root.parent, Path(tmp) / "workspace")
            self.assertEqual(len(threads), 1)
            self.assertEqual(threads[0]["id"], session.stem)
            self.assertEqual(threads[0]["preview"], "Inspect this project")

            messages = native.read_claude_thread(session)
            self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
            self.assertEqual(messages[1]["text"], "Done")

    def test_claude_history_preserves_tools_results_thinking_and_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects" / "project"
            root.mkdir(parents=True)
            session = root / "33333333-3333-3333-3333-333333333333.jsonl"
            image_path = str(Path(tmp) / "screen.png")
            rows = [
                {
                    "type": "user",
                    "sessionId": session.stem,
                    "uuid": "user-1",
                    "timestamp": "2026-07-01T00:00:00Z",
                    "cwd": str(Path(tmp) / "workspace"),
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Inspect this screenshot"},
                            {"type": "image", "source": {"type": "file", "path": image_path}},
                        ],
                    },
                },
                {
                    "type": "assistant",
                    "sessionId": session.stem,
                    "uuid": "assistant-tool",
                    "timestamp": "2026-07-01T00:00:01Z",
                    "cwd": str(Path(tmp) / "workspace"),
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "Need to inspect files."},
                            {"type": "tool_use", "id": "toolu_1", "name": "PowerShell", "input": {"command": "Get-ChildItem"}},
                        ],
                    },
                },
                {
                    "type": "user",
                    "sessionId": session.stem,
                    "uuid": "tool-result",
                    "timestamp": "2026-07-01T00:00:02Z",
                    "cwd": str(Path(tmp) / "workspace"),
                    "message": {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "file.txt"}],
                    },
                    "tool_use_result": {"stdout": "file.txt"},
                },
            ]
            session.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            messages = native.read_claude_thread(session)
            roles = [message["role"] for message in messages]
            kinds = [message.get("kind") for message in messages if message["role"] == "activity"]
            self.assertEqual(roles, ["user", "activity", "activity", "activity"])
            self.assertEqual(kinds, ["reasoning", "tool", "result"])
            self.assertEqual(messages[0]["imageRefs"][0]["path"], image_path)
            self.assertIn("Get-ChildItem", messages[2]["text"])
            self.assertIn("file.txt", messages[3]["text"])

    def test_claude_history_promotes_file_edits_and_plans_to_rich_activity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects" / "project"
            root.mkdir(parents=True)
            session = root / "44444444-4444-4444-4444-444444444444.jsonl"
            rows = [
                {
                    "type": "assistant",
                    "sessionId": session.stem,
                    "uuid": "assistant-edit",
                    "timestamp": "2026-07-01T00:00:01Z",
                    "cwd": str(Path(tmp) / "workspace"),
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_edit",
                                "name": "Edit",
                                "input": {
                                    "file_path": "src/app.py",
                                    "old_string": "old = True\n",
                                    "new_string": "old = False\n",
                                },
                            },
                            {
                                "type": "tool_use",
                                "id": "toolu_plan",
                                "name": "ExitPlanMode",
                                "input": {"plan": "# Plan\n\n1. Inspect files\n2. Patch UI"},
                            },
                        ],
                    },
                }
            ]
            session.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            messages = native.read_claude_thread(session)
            diff = next(message for message in messages if message.get("nativeId") == "toolu_edit")
            plan = next(message for message in messages if message.get("nativeId") == "toolu_plan")
            self.assertEqual(diff["kind"], "diff")
            self.assertEqual(diff["changes"][0]["path"], "src/app.py")
            self.assertIn("-old = True", diff["diff"])
            self.assertIn("+old = False", diff["diff"])
            self.assertEqual(plan["kind"], "plan")
            self.assertIn("Inspect files", plan["text"])

    def test_claude_internal_local_command_records_are_not_threads_or_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects" / "project"
            root.mkdir(parents=True)
            session = root / "22222222-2222-2222-2222-222222222222.jsonl"
            row = {
                "type": "user",
                "sessionId": session.stem,
                "uuid": "local-command",
                "timestamp": "2026-07-01T00:00:00Z",
                "cwd": str(Path(tmp) / "workspace"),
                "message": {
                    "role": "user",
                    "content": "<local-command-caveat>Caveat</local-command-caveat>",
                },
            }
            session.write_text(json.dumps(row), encoding="utf-8")
            self.assertEqual(native.discover_claude_threads(root.parent, Path(tmp) / "workspace"), [])
            self.assertEqual(native.read_claude_thread(session), [])

    def test_thread_refs_are_upserted_without_duplicate_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "native-threads.json"
            workspace = Path(tmp) / "workspace"
            other_workspace = Path(tmp) / "other"
            first = native.thread_ref("codex", "profile-1", workspace, "thr_1", "First")
            native.upsert_thread_ref(path, first)
            second = native.thread_ref("codex", "profile-1", workspace, "thr_2", "Updated")
            native.upsert_thread_ref(path, second)
            third = native.thread_ref("codex", "profile-1", other_workspace, "thr_3", "Other")
            native.upsert_thread_ref(path, third)
            refs = native.load_thread_refs(path)
            self.assertEqual(len(refs), 2)
            self.assertEqual(refs[0]["nativeSessionId"], "thr_3")
            self.assertEqual(refs[1]["nativeSessionId"], "thr_2")
            self.assertEqual(refs[1]["title"], "Updated")

    def test_antigravity_native_transcript_discovery_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "antigravity-cli"
            session_id = "33333333-3333-3333-3333-333333333333"
            workspace = Path(tmp) / "workspace"
            transcript = home / "brain" / session_id / ".system_generated" / "logs" / "transcript.jsonl"
            transcript.parent.mkdir(parents=True)
            rows = [
                {
                    "type": "USER_INPUT",
                    "content": "<USER_REQUEST>\nInspect the project\n</USER_REQUEST>",
                },
                {"type": "PLANNER_RESPONSE", "content": "Inspection complete."},
            ]
            transcript.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            cache = home / "cache" / "last_conversations.json"
            cache.parent.mkdir(parents=True)
            cache.write_text(json.dumps({str(workspace): session_id}), encoding="utf-8")

            threads = native.discover_antigravity_threads(home, workspace)
            self.assertEqual(threads[0]["id"], session_id)
            self.assertEqual(threads[0]["preview"], "Inspect the project")
            messages = native.read_antigravity_thread(home, session_id)
            self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
            self.assertEqual(messages[-1]["text"], "Inspection complete.")

    def test_antigravity_thread_reader_logs_format_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "antigravity-cli"
            session_id = "44444444-4444-4444-4444-444444444444"
            transcript = native.antigravity_transcript_path(home, session_id)
            transcript.parent.mkdir(parents=True)
            transcript.write_text(
                "\n".join(
                    [
                        "{not-json",
                        json.dumps({"type": "NEW_EVENT_KIND", "content": "new payload"}),
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertLogs("native_harness", level="DEBUG") as logs:
                self.assertEqual(native.read_antigravity_thread(home, session_id), [])
            joined = "\n".join(logs.output)
            self.assertIn("malformed JSON", joined)
            self.assertIn("no readable messages", joined)
            self.assertIn("NEW_EVENT_KIND", joined)

    def test_cursor_history_discovery_and_read_use_agent_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cursor_home = Path(tmp) / ".cursor"
            workspace = Path(tmp) / "project"
            workspace.mkdir()
            project_name = native.cursor_project_name_candidates(workspace)[0]
            session_id = "11111111-2222-3333-4444-555555555555"
            transcript_dir = cursor_home / "projects" / project_name / "agent-transcripts" / session_id
            transcript_dir.mkdir(parents=True)
            transcript = transcript_dir / f"{session_id}.jsonl"
            rows = [
                {"role": "user", "message": {"content": [{"type": "text", "text": "Inspect the Cursor project"}]}},
                {"role": "assistant", "message": {"content": [{"type": "text", "text": "Cursor inspection complete."}]}},
            ]
            transcript.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            threads = native.discover_cursor_threads(cursor_home, workspace)
            self.assertEqual(threads[0]["id"], session_id)
            self.assertEqual(threads[0]["preview"], "Inspect the Cursor project")
            messages = native.read_cursor_thread(Path(threads[0]["path"]))
            self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
            self.assertEqual(messages[-1]["text"], "Cursor inspection complete.")

    def test_codex_file_history_discovery_and_read_use_rollout_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / ".codex-account"
            workspace = Path(tmp) / "workspace"
            child_cwd = workspace / "repo"
            session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            session = codex_home / "sessions" / "2026" / "07" / "01" / f"rollout-2026-07-01T12-00-00-{session_id}.jsonl"
            session.parent.mkdir(parents=True)
            rows = [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": session_id,
                        "timestamp": "2026-07-01T02:00:00Z",
                        "cwd": str(child_cwd),
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "<environment_context>hidden</environment_context>"}],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "id": "user-1",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Inspect Codex history"}],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "id": "assistant-1",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "History loaded."}],
                    },
                },
            ]
            session.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            (codex_home / "session_index.jsonl").write_text(
                json.dumps(
                    {
                        "id": session_id,
                        "thread_name": "Indexed Codex history",
                        "updated_at": "2026-07-01T02:01:00Z",
                    }
                ),
                encoding="utf-8",
            )

            threads = native.discover_codex_file_threads(codex_home, workspace, include_default=False)
            self.assertEqual(len(threads), 1)
            self.assertEqual(threads[0]["id"], session_id)
            self.assertEqual(threads[0]["preview"], "Indexed Codex history")
            self.assertEqual(threads[0]["cwd"], str(child_cwd))
            self.assertEqual(threads[0]["actualCwd"], str(child_cwd))
            self.assertEqual(threads[0]["source"], "codex-file")

            messages = native.read_codex_session_file(session)
            self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
            self.assertEqual(messages[0]["text"], "Inspect Codex history")
            self.assertEqual(messages[1]["text"], "History loaded.")

    def test_codex_file_history_uses_state_table_and_keeps_other_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / ".codex"
            selected_workspace = Path(tmp) / "selected"
            other_workspace = Path(tmp) / "other-project"
            session_id = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
            session = codex_home / "sessions" / "2026" / "07" / "01" / "rollout-2026-07-01T12-10-00-old-id.jsonl"
            session.parent.mkdir(parents=True)
            session.write_text(
                "\n".join(
                    json.dumps(row)
                    for row in [
                        {
                            "type": "session_meta",
                            "payload": {
                                "id": "old-id",
                                "timestamp": "2026-07-01T02:10:00Z",
                                "cwd": str(selected_workspace),
                            },
                        },
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "user",
                                "content": [{"type": "input_text", "text": "Old prompt"}],
                            },
                        },
                    ]
                ),
                encoding="utf-8",
            )
            db = codex_home / "state_5.sqlite"
            connection = sqlite3.connect(db)
            try:
                connection.execute(
                    "create table threads (id text, rollout_path text, created_at integer, updated_at integer, cwd text, title text)"
                )
                connection.execute(
                    "insert into threads values (?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        str(session),
                        1782880000,
                        1782880100,
                        "\\\\?\\" + str(other_workspace),
                        "State table title",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            threads = native.discover_codex_file_threads(codex_home, selected_workspace, include_default=False)
            self.assertEqual(len(threads), 1)
            self.assertEqual(threads[0]["id"], session_id)
            self.assertEqual(threads[0]["preview"], "State table title")
            self.assertEqual(threads[0]["cwd"], str(other_workspace))
            self.assertEqual(threads[0]["path"], str(session))

    def test_codex_file_history_compacts_raw_tool_activity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "rollout-2026-07-01T12-20-00-cccccccc-dddd-eeee-ffff-000000000000.jsonl"
            big_output = "Exit code: 0\nWall time: 0.5 seconds\nOutput:\n" + ("data:image/png;base64,AAAA" * 3000)
            rows = [
                {
                    "type": "response_item",
                    "timestamp": "2026-07-01T02:20:00Z",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Show the old project"}],
                    },
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-07-01T02:20:01Z",
                    "payload": {
                        "type": "function_call",
                        "name": "shell_command",
                        "arguments": json.dumps({"command": "Get-Content huge-file.txt", "workdir": str(Path(tmp))}),
                        "call_id": "call-1",
                    },
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-07-01T02:20:02Z",
                    "payload": {"type": "function_call_output", "call_id": "call-1", "output": big_output},
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-07-01T02:20:03Z",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Readable answer."}],
                    },
                },
            ]
            session.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            messages = native.read_codex_session_file(session)
            joined = "\n".join(str(message.get("text") or "") for message in messages)
            self.assertEqual([message["role"] for message in messages], ["user", "activity", "assistant"])
            self.assertIn("Tool activity compacted: 2 calls/results hidden", joined)
            self.assertIn("Readable answer.", joined)
            self.assertNotIn("data:image/png;base64", joined)
            self.assertNotIn('{"command"', joined)

    def test_codex_file_history_collapses_commentary_into_timed_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "rollout-2026-07-01T12-20-00-dddddddd-eeee-ffff-0000-111111111111.jsonl"
            rows = [
                {
                    "type": "response_item",
                    "timestamp": "2026-07-01T02:20:00Z",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Polish the history"}],
                    },
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-07-01T02:20:10Z",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "phase": "commentary",
                        "content": [{"type": "output_text", "text": "Inspecting the renderer."}],
                    },
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-07-01T02:21:05Z",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "phase": "final_answer",
                        "content": [{"type": "output_text", "text": "The polished answer."}],
                    },
                },
            ]
            session.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            messages = native.read_codex_session_file(session)
            self.assertEqual([message["role"] for message in messages], ["user", "turn_meta", "assistant"])
            self.assertEqual(messages[1]["text"], "Worked for 1m 5s")
            self.assertEqual(messages[1]["commentaryCount"], 1)
            self.assertNotIn("Inspecting the renderer.", "\n".join(message["text"] for message in messages))

    def test_codex_thread_messages_preserve_diff_and_image_view_metadata(self) -> None:
        thread = {
            "turns": [
                {
                    "items": [
                        {
                            "type": "fileChange",
                            "id": "change-1",
                            "status": "completed",
                            "changes": [{"path": "src/app.py", "kind": "update", "diff": "diff --git a/src/app.py b/src/app.py\n+ok"}],
                        },
                        {"type": "imageView", "id": "image-1", "path": "C:/Temp/screen.png"},
                    ]
                }
            ]
        }
        messages = native.codex_thread_messages(thread)
        self.assertEqual(messages[0]["kind"], "diff")
        self.assertIn("diff --git", messages[0]["diff"])
        self.assertEqual(messages[1]["kind"], "image")
        self.assertEqual(messages[1]["imageRefs"][0]["path"], "C:/Temp/screen.png")

    def test_claude_stream_command_uses_stdin_and_native_resume(self) -> None:
        transport = native.StreamJsonTransport(
            "claude",
            sys.executable,
            Path.cwd(),
            lambda _event: None,
            session_id="11111111-1111-1111-1111-111111111111",
        )
        args, stdin_text = transport._command_for_prompt("unchanged request")
        self.assertIn("--resume", args)
        self.assertNotIn("unchanged request", args)
        self.assertNotIn("--brief", args)
        self.assertEqual(stdin_text, "unchanged request")

    def test_codex_transport_sends_model_effort_and_access_overrides(self) -> None:
        requests: list[tuple[str, dict]] = []

        class FakeClient:
            def request(self, method: str, params: dict, timeout: int) -> dict:
                requests.append((method, params))
                if method == "model/list":
                    return {"data": [{"model": "gpt-test"}]}
                if method == "thread/start":
                    return {"thread": {"id": "thread-test"}}
                if method == "turn/start":
                    return {"turn": {"id": "turn-test"}}
                return {}

        transport = native.CodexTransport.__new__(native.CodexTransport)
        transport.client = FakeClient()
        transport.cwd = Path.cwd()
        transport.thread_id = ""
        transport.turn_id = ""
        self.assertEqual(transport.list_models(), [{"model": "gpt-test"}])
        transport.start_thread(
            Path.cwd(),
            model="gpt-test",
            approval_policy="on-request",
            sandbox="danger-full-access",
            personality="friendly",
        )
        transport.start_turn(
            "Apply the change",
            model="gpt-test",
            effort="high",
            approval_policy="on-request",
            sandbox_policy={"type": "dangerFullAccess"},
            personality="friendly",
            collaboration_mode={"mode": "plan", "settings": {"model": "gpt-test"}},
        )

        thread_params = next(params for method, params in requests if method == "thread/start")
        turn_params = next(params for method, params in requests if method == "turn/start")
        self.assertEqual(thread_params["model"], "gpt-test")
        self.assertEqual(thread_params["sandbox"], "danger-full-access")
        self.assertEqual(thread_params["personality"], "friendly")
        self.assertEqual(turn_params["model"], "gpt-test")
        self.assertEqual(turn_params["effort"], "high")
        self.assertEqual(turn_params["personality"], "friendly")
        self.assertEqual(turn_params["collaborationMode"]["mode"], "plan")
        self.assertEqual(turn_params["sandboxPolicy"], {"type": "dangerFullAccess"})

    def test_stream_commands_apply_provider_model_effort_and_access(self) -> None:
        claude = native.StreamJsonTransport(
            "claude",
            "claude.exe",
            Path.cwd(),
            lambda _event: None,
            model="opus",
            effort="xhigh",
            access_mode="full-access",
        )
        claude_args, _ = claude._command_for_prompt("request")
        self.assertEqual(claude_args[claude_args.index("--model") + 1], "opus")
        self.assertEqual(claude_args[claude_args.index("--effort") + 1], "xhigh")
        self.assertEqual(claude_args[claude_args.index("--permission-mode") + 1], "bypassPermissions")
        self.assertIn("--allow-dangerously-skip-permissions", claude_args)

        cursor = native.StreamJsonTransport(
            "cursor",
            "cursor-agent.cmd",
            Path.cwd(),
            lambda _event: None,
            model="gpt-test",
            access_mode="plan",
        )
        cursor_args, _ = cursor._command_for_prompt("request")
        self.assertEqual(cursor_args[cursor_args.index("--model") + 1], "gpt-test")
        self.assertEqual(cursor_args[cursor_args.index("--mode") + 1], "plan")

    def test_claude_stream_command_wires_permission_prompt_bridge(self) -> None:
        bridge_path = Path.cwd() / "claude_permission_bridge.py"
        transport = native.StreamJsonTransport(
            "claude",
            sys.executable,
            Path.cwd(),
            lambda _event: None,
            env={
                **os.environ,
                "AI_HUB_PERMISSION_URL": "http://127.0.0.1:8123/permission",
                "AI_HUB_PERMISSION_TOKEN": "test-token",
                "AI_HUB_PERMISSION_BRIDGE_PATH": str(bridge_path),
                "AI_HUB_PYTHON": sys.executable,
            },
        )
        args, stdin_text = transport._command_for_prompt("request")
        self.assertEqual(stdin_text, "request")
        self.assertNotIn("--brief", args)
        self.assertIn("--permission-prompt-tool", args)
        self.assertIn("mcp__ai-account-hub-permissions__mcp_auth_tool", args)
        config = args[args.index("--mcp-config") + 1]
        parsed = json.loads(config)
        server = parsed["mcpServers"]["ai-account-hub-permissions"]
        self.assertEqual(server["args"], [str(bridge_path)])
        self.assertEqual(server["env"]["AI_HUB_PERMISSION_TOKEN"], "test-token")

    def test_cursor_stream_command_uses_agent_print_stream_json(self) -> None:
        transport = native.StreamJsonTransport(
            "cursor",
            "cursor-agent.cmd",
            Path.cwd(),
            lambda _event: None,
        )
        args, stdin_text = transport._command_for_prompt("unchanged request")
        self.assertIn("--print", args)
        self.assertIn("--output-format", args)
        self.assertEqual(args[args.index("--output-format") + 1], "stream-json")
        self.assertIn("--stream-partial-output", args)
        self.assertIn("--trust", args)
        self.assertEqual(args[-1], "unchanged request")
        self.assertIsNone(stdin_text)

        resumed = native.StreamJsonTransport(
            "cursor",
            "cursor-agent.cmd",
            Path.cwd(),
            lambda _event: None,
            session_id="chat-123",
        )
        resumed_args, _ = resumed._command_for_prompt("follow up")
        self.assertIn("--resume", resumed_args)
        self.assertEqual(resumed_args[resumed_args.index("--resume") + 1], "chat-123")
        self.assertEqual(resumed_args[-1], "follow up")


if __name__ == "__main__":
    unittest.main()
