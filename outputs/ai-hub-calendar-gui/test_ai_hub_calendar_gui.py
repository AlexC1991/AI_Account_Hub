import datetime as dt
import importlib.util
import json
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent / "ai_hub_calendar_gui.py"
SPEC = importlib.util.spec_from_file_location("ai_hub_calendar_gui", MODULE_PATH)
hub = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(hub)


class HubDataTests(unittest.TestCase):
    def test_coding_user_message_parts_hides_transport_wrapper_and_keeps_attachments(self) -> None:
        text = (
            "# Files mentioned by the user:\n\n"
            "## first screenshot.png: C:/Temp/first.png\n\n"
            "## second screenshot.png: C:/Temp/second.png\n\n"
            "## My request for Codex:\n"
            "This should look like **Codex**.\n"
            '<image name=\"Image #1\" path=\"C:/Temp/first.png\"></image>'
        )
        body, attachments = hub.coding_user_message_parts(text)
        self.assertEqual(body, "This should look like **Codex**.")
        self.assertEqual(attachments, ["first screenshot.png", "second screenshot.png"])
        detail_body, detail_attachments = hub.coding_user_message_details(text)
        self.assertEqual(detail_body, body)
        self.assertEqual(detail_attachments[0]["path"], "C:/Temp/first.png")
        self.assertEqual(detail_attachments[0]["name"], "first screenshot.png")

    def test_coding_access_parameters_match_codex_app_server_shapes(self) -> None:
        workspace = Path("C:/work")
        self.assertEqual(hub.codex_access_parameters("read-only", workspace)["sandboxPolicy"]["type"], "readOnly")
        self.assertEqual(hub.codex_access_parameters("full-access", workspace)["threadSandbox"], "danger-full-access")
        workspace_policy = hub.codex_access_parameters("workspace", workspace)["sandboxPolicy"]
        self.assertEqual(workspace_policy["type"], "workspaceWrite")
        self.assertEqual(workspace_policy["writableRoots"], [str(workspace)])

    def test_native_model_listing_parser_keeps_model_ids(self) -> None:
        models = hub.parse_native_model_listing("Available models\n* gpt-5.5  Default\nclaude-opus-4-6 - Coding")
        self.assertEqual([model["model"] for model in models], ["gpt-5.5", "claude-opus-4-6"])

    def test_codex_transport_skills_methods_use_app_server_protocol(self) -> None:
        calls: list[tuple[str, dict, float]] = []

        class FakeClient:
            def request(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
                calls.append((method, params or {}, timeout))
                if method == "skills/list":
                    return {"data": [{"cwd": "C:/work", "errors": [], "skills": [{"name": "demo"}]}]}
                return {"ok": True}

        transport = hub.CodexTransport("codex", Path("C:/codex-home"), Path("C:/work"), lambda _message: None)
        transport.client = FakeClient()
        transport.thread_id = "thr_test"
        self.assertEqual(transport.list_skills(Path("C:/work"), force_reload=True)[0]["cwd"], "C:/work")
        self.assertEqual(calls[-1], ("skills/list", {"cwds": ["C:\\work"], "forceReload": True}, 30))
        transport.write_skill_config(enabled=False, name="demo", path="C:/skills/demo/SKILL.md")
        self.assertEqual(
            calls[-1],
            ("skills/config/write", {"enabled": False, "name": "demo", "path": "C:/skills/demo/SKILL.md"}, 30),
        )
        transport.update_thread_settings(personality="friendly")
        self.assertEqual(calls[-1], ("thread/settings/update", {"threadId": "thr_test", "personality": "friendly"}, 30))
        transport.set_goal(objective="Ship it", status="active")
        self.assertEqual(
            calls[-1],
            ("thread/goal/set", {"threadId": "thr_test", "objective": "Ship it", "status": "active"}, 30),
        )
        transport.get_goal()
        self.assertEqual(calls[-1], ("thread/goal/get", {"threadId": "thr_test"}, 30))
        transport.clear_goal()
        self.assertEqual(calls[-1], ("thread/goal/clear", {"threadId": "thr_test"}, 30))

    def test_stream_attachment_prompt_lists_local_files(self) -> None:
        attachments = [Path("C:/Temp/screenshot.png"), Path("C:/Temp/notes.txt")]
        prompt = hub.native_attachment_prompt("Review these.", attachments)
        self.assertIn("# Files attached by the user", prompt)
        self.assertIn("screenshot.png", prompt)
        self.assertIn("Type: image", prompt)
        self.assertIn("notes.txt", prompt)
        self.assertIn("Type: file", prompt)
        self.assertTrue(prompt.endswith("Review these."))

    def test_coding_display_text_strips_terminal_control_sequences(self) -> None:
        text = hub.coding_display_text("\x1b[31;1mfailed\x1b[0m\x00\n\x1b[32;1mPath\x1b[0m")
        self.assertEqual(text, "failed\nPath")

    def test_command_activity_parts_simplify_powershell_wrapper(self) -> None:
        command, details, output = hub.coding_command_activity_parts(
            "\"C:/Program Files/PowerShell/7/pwsh.exe\" -Command 'Get-ChildItem -Force'\n"
            "completed | exit 0 | 1484 ms\n"
            "Output\n"
            "\x1b[32;1mPath\x1b[0m\nC:/work"
        )
        self.assertEqual(command, "Get-ChildItem -Force")
        self.assertEqual(details, "completed | exit 0 | 1484 ms")
        self.assertEqual(output, "Path\nC:/work")

    def test_coding_sidebar_thread_preview_limit_caps_selected_projects(self) -> None:
        self.assertEqual(hub.coding_sidebar_thread_preview_limit(15, selected=True, expanded=False), 7)
        self.assertEqual(hub.coding_sidebar_thread_preview_limit(15, selected=False, expanded=False), 3)
        self.assertEqual(hub.coding_sidebar_thread_preview_limit(15, selected=True, expanded=True), 15)

    def test_claude_reset_label_accepts_hour_without_minutes(self) -> None:
        base = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
        parsed = hub.parse_claude_reset_label("Jul 4, 1pm (Australia/Sydney)", base=base)
        self.assertTrue(parsed.startswith("2026-07-04T"))

    def test_history_accumulates_usage_buckets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_db = hub.HISTORY_DB_FILE
            hub.HISTORY_DB_FILE = Path(tmp) / "history.sqlite3"
            try:
                profile = hub.normalize_profile(
                    {
                        "id": "codex:test",
                        "name": "Codex Test",
                        "provider": "codex",
                        "codexHome": str(Path(tmp) / "home"),
                        "usageDailyBuckets": [{"startDate": "2026-07-01", "tokens": 100}],
                        "lastLimitsRefreshUtc": "2026-07-01T00:00:00+00:00",
                    },
                    0,
                )
                hub.record_profile_history(profile, refresh_reason="test")
                profile["usageDailyBuckets"] = [{"startDate": "2026-07-02", "tokens": 200}]
                profile["lastLimitsRefreshUtc"] = "2026-07-02T00:00:00+00:00"
                hub.record_profile_history(profile, refresh_reason="test")
                entries = hub.history_usage_entries([profile])
                self.assertEqual([entry["day"] for entry in entries], ["2026-07-01", "2026-07-02"])
                self.assertEqual(sum(entry["tokens"] for entry in entries), 300)
                self.assertGreaterEqual(hub.history_limit_count(), 2)
            finally:
                hub.HISTORY_DB_FILE = old_db

    def test_combined_limit_left_text_sums_known_account_capacity(self) -> None:
        profiles = [
            {"weeklyLimitUsedPercent": "20", "shortLimitUsedPercent": "100"},
            {"weeklyLimitUsedPercent": "50", "shortLimitUsedPercent": "25"},
            {"weeklyLimitUsedPercent": "", "shortLimitUsedPercent": ""},
        ]
        self.assertEqual(hub.combined_limit_left_text(profiles, "weeklyLimitUsedPercent"), "130% / 200%")
        self.assertEqual(hub.combined_limit_left_text(profiles, "shortLimitUsedPercent"), "75% / 200%")

    def test_online_links_merge_provider_defaults_and_custom_links(self) -> None:
        profile = hub.normalize_profile(
            {
                "id": "codex:links",
                "name": "Links",
                "provider": "codex",
                "onlineLinks": ["Team Billing | https://example.com/billing"],
            },
            0,
        )
        links = hub.online_links_for_profile(profile)
        labels = [link["label"] for link in links]
        self.assertIn("ChatGPT", labels)
        self.assertIn("API Usage", labels)
        self.assertIn("Team Billing", labels)
        self.assertTrue(all(hub.is_safe_online_url(link["url"]) for link in links))

    def test_custom_online_link_parser_rejects_unsafe_urls(self) -> None:
        links = hub.parse_custom_online_links_text(
            "Bad | javascript:alert(1)\n"
            "Good | https://example.com/account\n"
            "Local | file:///C:/temp/test.html"
        )
        self.assertEqual(links, [{"key": "good", "label": "Good", "url": "https://example.com/account"}])

    def test_browser_command_for_url_supports_placeholder(self) -> None:
        command = hub.browser_command_for_url(
            {"browserCommand": 'chrome.exe --profile-directory="Profile 2" {url}'},
            "https://chatgpt.com/?a=1&b=2",
        )
        self.assertEqual(command, 'chrome.exe --profile-directory="Profile 2" "https://chatgpt.com/?a=1&b=2"')

    def test_browser_profile_defaults_to_isolated_cookie_profile(self) -> None:
        profile = hub.normalize_profile(
            {
                "id": "codex:cookie-profile",
                "name": "Cookie Profile",
                "provider": "codex",
            },
            0,
        )
        self.assertEqual(hub.browser_profile_mode(profile), "isolated")
        self.assertTrue(hub.uses_isolated_browser_profile(profile))
        profile["browserCommand"] = "chrome {url}"
        self.assertFalse(hub.uses_isolated_browser_profile(profile))
        profile["browserCommand"] = ""
        profile["browserProfileMode"] = "system"
        self.assertFalse(hub.uses_isolated_browser_profile(profile))

    def test_browser_profile_dir_is_stable_and_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_root = hub.BROWSER_PROFILES_ROOT
            hub.BROWSER_PROFILES_ROOT = Path(tmp)
            try:
                profile = hub.normalize_profile(
                    {
                        "id": "codex:cookie-dir",
                        "name": "Cookie Dir Account!",
                        "provider": "codex",
                    },
                    0,
                )
                first = hub.browser_profile_dir_for_profile(profile)
                second = hub.browser_profile_dir_for_profile(profile)
                self.assertEqual(first, second)
                self.assertEqual(first.parent, Path(tmp))
                self.assertIn("cookie-dir-account", first.name)
            finally:
                hub.BROWSER_PROFILES_ROOT = old_root

    def test_browser_profile_launch_args_use_user_data_dir(self) -> None:
        profile = hub.normalize_profile(
            {
                "id": "codex:launch-profile",
                "name": "Launch Profile",
                "provider": "codex",
                "browserProfileDir": "C:/Hub Browser Profiles/Launch Profile",
            },
            0,
        )
        args = hub.browser_profile_launch_args(profile, "https://chatgpt.com/", "C:/Program Files/Microsoft/Edge/Application/msedge.exe")
        self.assertEqual(args[0], "C:/Program Files/Microsoft/Edge/Application/msedge.exe")
        self.assertIn(f"--user-data-dir={Path('C:/Hub Browser Profiles/Launch Profile')}", args)
        self.assertIn("--new-window", args)
        self.assertEqual(args[-1], "https://chatgpt.com/")

    def test_browser_profile_web_login_label_defaults_to_needed(self) -> None:
        profile = hub.normalize_profile(
            {
                "id": "codex:web-login-needed",
                "name": "Web Login Needed",
                "provider": "codex",
            },
            0,
        )
        self.assertEqual(hub.browser_profile_web_login_label(profile), "Web login needed")
        profile["browserProfileMode"] = "system"
        self.assertEqual(hub.browser_profile_web_login_label(profile), "System browser")

    def test_claude_auth_status_parser_exposes_subscription_type(self) -> None:
        status = hub.parse_claude_auth_status_text(
            'Exit code: 0\n\n{"loggedIn":true,"authMethod":"claude.ai","apiProvider":"firstParty","subscriptionType":"pro"}'
        )
        self.assertTrue(status["loggedIn"])
        self.assertEqual(status["authMethod"], "claude.ai")
        self.assertEqual(status["subscriptionType"], "pro")
        self.assertEqual(hub.account_plan_label({"provider": "claude", "usageSummary": status}), "Pro")

    def test_claude_profile_home_migrates_legacy_desktop_path_and_preserves_isolated_home(self) -> None:
        legacy = hub.normalize_profile(
            {
                "id": "claude:legacy",
                "name": "Claude Legacy",
                "provider": "claude",
                "codexHome": str(hub.CLAUDE_ROAMING_HOME),
            },
            0,
        )
        self.assertEqual(hub.claude_profile_home(legacy), hub.CLAUDE_CLI_HOME)
        self.assertEqual(legacy["claudeConfigDir"], str(hub.CLAUDE_CLI_HOME))

        with tempfile.TemporaryDirectory() as tmp:
            isolated = hub.normalize_profile(
                {
                    "id": "claude:isolated",
                    "name": "Claude Isolated",
                    "provider": "claude",
                    "codexHome": tmp,
                },
                0,
            )
            self.assertEqual(hub.claude_profile_home(isolated), Path(tmp))
            self.assertEqual(isolated["claudeConfigDir"], tmp)

    def test_gemini_profiles_migrate_to_antigravity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = hub.normalize_profile(
                {
                    "id": "gemini:isolated",
                    "name": "Gemini Isolated",
                    "provider": "gemini",
                    "codexHome": tmp,
                },
                0,
            )
            self.assertEqual(hub.provider_key(profile), "antigravity")
            self.assertEqual(profile["provider"], "antigravity")
            self.assertEqual(profile["codexHome"], tmp)
            self.assertNotIn("geminiConfigDir", profile)

    def test_claude_cli_login_is_ready_without_desktop_login(self) -> None:
        profile = hub.normalize_profile(
            {
                "id": "claude:cli-ready",
                "name": "Claude CLI",
                "provider": "claude",
                "lastLimitsError": "Claude Desktop login not detected.",
                "usageSummary": {
                    "desktopReady": False,
                    "claudeAuthStatus": {"loggedIn": True},
                },
            },
            0,
        )
        self.assertEqual(hub.effective_state(profile), "ready")

    def test_native_token_usage_label_accepts_nested_app_server_shape(self) -> None:
        self.assertEqual(
            hub.native_token_usage_label({"total": {"inputTokens": 1200, "outputTokens": 300}}),
            "2K",
        )
        self.assertEqual(hub.native_token_usage_label({}), "-")


class HubUiTests(unittest.TestCase):
    def test_codex_coding_style_defaults_to_friendly(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            profile = hub.normalize_profile(
                {
                    "id": "codex:style-default",
                    "name": "Codex Style",
                    "provider": "codex",
                    "codexHome": str(Path(tempfile.gettempdir()) / "style-default-home"),
                },
                0,
            )
            app.profiles = [profile]
            app.coding_profile_id = hub.profile_id(profile)
            app._sync_coding_controls()
            self.assertEqual(app.coding_control_values()["personality"], "friendly")
            self.assertEqual(app.coding_personality_var.get(), "Friendly")
        finally:
            app.destroy()

    def test_codex_slash_commands_use_native_settings_goal_and_plan(self) -> None:
        class FakeCodexTransport(hub.CodexTransport):
            def __init__(self) -> None:
                self.provider = "codex"
                self.thread_id = "thr_fake"
                self.turn_id = ""
                self.settings_calls: list[dict] = []
                self.goal_calls: list[dict] = []
                self.turn_calls: list[dict] = []

            @property
            def alive(self) -> bool:
                return True

            def update_thread_settings(self, **settings) -> dict:
                self.settings_calls.append(settings)
                return {}

            def set_goal(self, objective=None, status=None, token_budget=None) -> dict:
                payload = {"objective": objective, "status": status, "tokenBudget": token_budget}
                self.goal_calls.append(payload)
                return {
                    "goal": {
                        "threadId": self.thread_id,
                        "objective": objective or "",
                        "status": status or "active",
                        "tokensUsed": 0,
                        "timeUsedSeconds": 0,
                        "createdAt": 0,
                        "updatedAt": 0,
                    }
                }

            def start_turn(self, text, attachments=None, **kwargs) -> dict:
                self.turn_calls.append({"text": text, "attachments": attachments or [], **kwargs})
                self.turn_id = "turn_fake"
                return {"turn": {"id": self.turn_id}}

            def shutdown(self) -> None:
                return None

        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            profile = hub.normalize_profile(
                {
                    "id": "codex:slash",
                    "name": "Codex Slash",
                    "provider": "codex",
                    "codexHome": str(Path(tempfile.gettempdir()) / "slash-home"),
                },
                0,
            )
            fake = FakeCodexTransport()
            app.profiles = [profile]
            app.coding_profile_id = hub.profile_id(profile)
            app._sync_coding_controls()
            app.native_transport = fake
            app.native_thread_id = fake.thread_id
            app.coding_session_active = True

            app.coding_input.delete("1.0", "end")
            app.coding_input.insert("1.0", "/personality friendly")
            app.coding_input_placeholder_active = False
            app.send_native_message()
            deadline = time.time() + 3
            while time.time() < deadline and not fake.settings_calls:
                app.update()
                time.sleep(0.01)
            self.assertEqual(fake.settings_calls[-1], {"personality": "friendly"})

            app.native_busy = False
            app.coding_input.delete("1.0", "end")
            app.coding_input.insert("1.0", "/plan Audit the plan command")
            app.send_native_message()
            deadline = time.time() + 3
            while time.time() < deadline and not fake.turn_calls:
                app.update()
                time.sleep(0.01)
            self.assertEqual(fake.turn_calls[-1]["text"], "Audit the plan command")
            self.assertEqual(fake.turn_calls[-1]["collaboration_mode"]["mode"], "plan")
            self.assertEqual(fake.turn_calls[-1]["personality"], "friendly")

            app.native_busy = False
            app.coding_input.delete("1.0", "end")
            app.coding_input.insert("1.0", "/goal Finish the slash command support")
            app.send_native_message()
            deadline = time.time() + 3
            while time.time() < deadline and not fake.goal_calls:
                app.update()
                time.sleep(0.01)
            self.assertEqual(fake.goal_calls[-1]["objective"], "Finish the slash command support")
        finally:
            app.destroy()

    def test_native_coding_flow_prepares_sends_streams_and_completes(self) -> None:
        class FakeClaudeTransport(hub.StreamJsonTransport):
            def __init__(self, callback) -> None:
                self.provider = "claude"
                self.event_callback = callback
                self.session_id = "11111111-1111-1111-1111-111111111111"
                self.process = None
                self.received = ""

            @property
            def alive(self) -> bool:
                return False

            def send(self, text: str) -> int:
                self.received = text
                self.event_callback(
                    {
                        "method": "stream/event",
                        "params": {
                            "provider": "claude",
                            "event": {
                                "type": "stream_event",
                                "session_id": self.session_id,
                                "event": {"delta": {"type": "text_delta", "text": "OK."}},
                            },
                        },
                    }
                )
                self.event_callback(
                    {
                        "method": "stream/event",
                        "params": {
                            "provider": "claude",
                            "event": {"type": "result", "session_id": self.session_id, "result": "OK."},
                        },
                    }
                )
                self.event_callback(
                    {
                        "method": "transport/exited",
                        "params": {"provider": "claude", "exitCode": 0, "stderr": "", "stopped": False},
                    }
                )
                return 0

            def stop(self) -> None:
                return None

        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            profile = hub.normalize_profile(
                {
                    "id": "claude:test",
                    "name": "Claude Test",
                    "provider": "claude",
                    "workspace": str(Path.cwd()),
                },
                0,
            )
            app.profiles = [profile]
            app.coding_profile_id = hub.profile_id(profile)
            fake = FakeClaudeTransport(app._native_event_callback)
            app._create_native_transport = lambda *_args, **_kwargs: fake
            app.prepare_native_thread()
            deadline = time.time() + 3
            while time.time() < deadline and app.native_busy:
                app.update()
                time.sleep(0.01)
            self.assertTrue(app.coding_session_active)

            request = "unchanged native request"
            app.coding_input.delete("1.0", "end")
            app.coding_input.insert("1.0", request)
            app.coding_input_placeholder_active = False
            app.send_native_message()
            deadline = time.time() + 3
            while time.time() < deadline and app.native_busy:
                app.update()
                time.sleep(0.01)
            app.update()

            self.assertEqual(fake.received, request)
            self.assertFalse(app.native_busy)
            self.assertEqual(app.native_thread_id, fake.session_id)
            self.assertTrue(any(message["role"] == "assistant" and message["text"] == "OK." for message in app.native_messages))
        finally:
            app.close_application()

    def test_coding_is_default_and_accounts_dashboard_is_separate(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            app.update_idletasks()
            self.assertEqual(app.active_section, "coding")
            self.assertIn(app.coding_page, app.page_host.grid_slaves())
            self.assertNotIn(app.account_page, app.page_host.grid_slaves())
            self.assertEqual(app.section_subtitle_label.cget("text"), "Projects and native coding sessions")
            self.assertTrue(app.coding_profile_combo.cget("values"))
            self.assertTrue(str(app.coding_profile_combo).startswith(str(app.coding_sidebar_account)))
            self.assertFalse(str(app.coding_profile_combo).startswith(str(app.coding_composer)))
            self.assertTrue(app.coding_model_combo.cget("values"))
            self.assertTrue(app.coding_access_combo.cget("values"))
            self.assertTrue(app.coding_short_limit_label.cget("text"))
            self.assertFalse(app.topbar.grid_info())
            self.assertFalse(app.statusbar.grid_info())
            self.assertFalse(app.coding_inspector.grid_info())

            app.toggle_coding_details()
            app.update_idletasks()
            self.assertTrue(app.coding_details_visible)
            self.assertTrue(app.coding_inspector.grid_info())
            app.toggle_coding_details()

            account_selection = app.selected_profile
            selected_day = app.selected_date
            app.show_section("accounts")
            app.update_idletasks()
            self.assertEqual(app.active_section, "accounts")
            self.assertIn(app.account_page, app.page_host.grid_slaves())
            self.assertNotIn(app.coding_page, app.page_host.grid_slaves())
            self.assertEqual(app.section_subtitle_label.cget("text"), "Accounts, limits and usage history")
            self.assertTrue(app.topbar.grid_info())
            self.assertTrue(app.statusbar.grid_info())
            self.assertEqual(app.selected_profile, account_selection)
            self.assertEqual(app.selected_date, selected_day)
            self.assertTrue(app.account_scroll.winfo_exists())
            self.assertTrue(app.calendar_panel.winfo_exists())
            self.assertTrue(app.breakdown_scroll.winfo_exists())
        finally:
            app.destroy()

    def test_native_attachment_tray_renders_and_removes_files(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "sample.py"
                path.write_text("print('ok')", encoding="utf-8")
                app._add_native_attachments([path])
                app.update_idletasks()
                self.assertTrue(app.coding_attachment_tray.grid_info())

                texts: list[str] = []

                def collect_text(widget) -> None:
                    try:
                        texts.append(str(widget.cget("text")))
                    except Exception:
                        pass
                    for child in widget.winfo_children():
                        collect_text(child)

                collect_text(app.coding_attachment_tray)
                self.assertTrue(any("sample.py" in text for text in texts))
                self.assertIn("1 attachment staged", app.coding_composer_status.cget("text"))

                app.remove_native_attachment(0)
                app.update_idletasks()
                self.assertEqual(app.native_attachments, [])
                self.assertFalse(app.coding_attachment_tray.grid_info())
        finally:
            app.destroy()

    def test_stream_result_replaces_partial_assistant_message(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            app.native_messages = []
            app._append_native_delta("claude-assistant", "Working", render=False)
            app._finish_native_assistant_message(
                "claude-assistant",
                "claude-result",
                "Working complete.",
                render=False,
            )
            assistants = [message for message in app.native_messages if message.get("role") == "assistant"]
            self.assertEqual(len(assistants), 1)
            self.assertEqual(assistants[0]["text"], "Working complete.")
        finally:
            app.destroy()

    def test_coding_input_enter_sends_and_shift_enter_keeps_newline_behavior(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            sent = {"count": 0}
            app.send_native_message = lambda: sent.update({"count": sent["count"] + 1})
            event = type("Event", (), {"state": 0})()
            shift_event = type("Event", (), {"state": 0x0001})()
            self.assertEqual(app._coding_input_return(event), "break")
            self.assertEqual(sent["count"], 1)
            self.assertIsNone(app._coding_input_return(shift_event))
            self.assertEqual(sent["count"], 1)
            self.assertEqual(app._coding_send_key(event), "break")
            self.assertEqual(sent["count"], 2)
        finally:
            app.destroy()

    def test_assistant_delta_updates_existing_stream_range(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            app.native_messages = [{"role": "assistant", "text": "Hello", "nativeId": "assistant-1"}]
            app._render_coding_stream()
            signature = app._coding_stream_signature
            ranges = dict(app._coding_stream_message_ranges)
            app._append_native_delta("assistant-1", " world", render=True)
            self.assertEqual(app._coding_stream_signature, signature)
            self.assertEqual(app._coding_stream_message_ranges, ranges)
            self.assertIn("Hello world", app.coding_stream_text.get("1.0", "end-1c"))
        finally:
            app.destroy()

    def test_live_activity_delta_updates_in_place_without_rich_widgets(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            app.native_busy = True
            app.native_messages = [
                {
                    "role": "activity",
                    "kind": "command",
                    "title": "Command",
                    "text": "pytest -q",
                    "nativeId": "cmd-1",
                    "status": "inProgress",
                }
            ]
            app._render_coding_stream()
            signature = app._coding_stream_signature
            ranges = dict(app._coding_stream_message_ranges)
            self.assertEqual(app.coding_stream_text.winfo_children(), [])

            app._append_native_activity_delta("cmd-1", "one passed", render=True)
            self.assertEqual(app._coding_stream_signature, signature)
            self.assertEqual(app._coding_stream_message_ranges, ranges)
            self.assertEqual(app.coding_stream_text.winfo_children(), [])
            self.assertIn("one passed", app.coding_stream_text.get("1.0", "end-1c"))
        finally:
            app.destroy()

    def test_live_activity_append_keeps_existing_message_widgets(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            app.native_busy = True
            app.native_messages = [{"role": "user", "text": "Run the tests", "nativeId": "user-1"}]
            app._render_coding_stream()
            children_before = app.coding_stream_text.winfo_children()
            self.assertEqual(len(children_before), 1)

            app.native_messages.append(
                {
                    "role": "activity",
                    "kind": "command",
                    "title": "Command",
                    "text": "pytest -q",
                    "nativeId": "cmd-1",
                    "status": "inProgress",
                }
            )
            app._render_coding_stream()
            self.assertTrue(children_before[0].winfo_exists())
            self.assertIn("pytest -q", app.coding_stream_text.get("1.0", "end-1c"))
        finally:
            app.destroy()

    def test_loaded_history_restores_diff_context_for_files_view(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            messages = [
                {
                    "role": "activity",
                    "kind": "diff",
                    "title": "File changes",
                    "text": "File changes",
                    "nativeId": "diff-1",
                    "status": "completed",
                    "changes": [{"path": "src/app.py", "kind": "update", "diff": "-old\n+new"}],
                    "diff": "diff --git a/src/app.py b/src/app.py\n-old\n+new",
                }
            ]
            app._restore_native_file_context(messages)
            self.assertEqual(app.native_file_changes[0]["path"], "src/app.py")
            self.assertIn("diff --git", app.native_turn_diff)
        finally:
            app.destroy()

    def test_native_threads_collapse_to_one_active_thread_per_workspace(self) -> None:
        old_file = hub.NATIVE_THREADS_FILE
        with tempfile.TemporaryDirectory() as tmp:
            hub.NATIVE_THREADS_FILE = Path(tmp) / "native-threads.json"
            app = hub.AccountCalendarApp()
            try:
                app.withdraw()
                workspace = Path(tmp) / "workspace"
                other = Path(tmp) / "other"
                profile = hub.normalize_profile(
                    {
                        "id": "claude:collapse",
                        "name": "Claude Collapse",
                        "provider": "claude",
                        "workspace": str(workspace),
                    },
                    0,
                )
                app.profiles = [profile]
                app.coding_profile_id = hub.profile_id(profile)
                hub.upsert_thread_ref(
                    hub.NATIVE_THREADS_FILE,
                    hub.thread_ref("claude", hub.profile_id(profile), workspace, "preferred", "Preferred"),
                )
                collapsed = app._collapse_native_threads(
                    [
                        {"id": "newer", "provider": "claude", "preview": "Newer", "cwd": str(workspace), "updatedAt": 200},
                        {"id": "preferred", "provider": "claude", "preview": "Preferred", "cwd": str(workspace), "updatedAt": 100},
                        {"id": "other", "provider": "claude", "preview": "Other", "cwd": str(other), "updatedAt": 50},
                    ],
                    profile,
                )
                self.assertEqual(len(collapsed), 2)
                self.assertEqual(collapsed[0]["id"], "preferred")
                self.assertEqual({item["id"] for item in collapsed}, {"preferred", "other"})
            finally:
                app.destroy()
                hub.NATIVE_THREADS_FILE = old_file

    def test_coding_skills_tab_renders_codex_skill_access(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            profile = hub.normalize_profile(
                {
                    "id": "codex:skills",
                    "name": "Codex Skills",
                    "provider": "codex",
                    "codexHome": str(Path(tempfile.gettempdir()) / "codex-skills"),
                    "workspace": str(Path.cwd()),
                },
                0,
            )
            app.profiles = [profile]
            app.coding_profile_id = hub.profile_id(profile)
            app.native_skills_profile_id = hub.profile_id(profile)
            app.native_skills_workspace = str(Path.cwd()).lower()
            app.native_skills = [
                {
                    "cwd": str(Path.cwd()),
                    "errors": [],
                    "skills": [
                        {
                            "name": "skill-creator",
                            "description": "Create Codex skills.",
                            "enabled": True,
                            "path": "C:/skills/skill-creator/SKILL.md",
                            "scope": "system",
                            "shortDescription": "Create skills",
                        }
                    ],
                }
            ]
            app.coding_context_tab = "skills"
            app._render_coding_context()
            texts: list[str] = []

            def collect_text(widget) -> None:
                try:
                    texts.append(str(widget.cget("text")))
                except Exception:
                    pass
                for child in widget.winfo_children():
                    collect_text(child)

            collect_text(app.coding_context_scroll.inner)
            self.assertIn("skill-creator", texts)
            self.assertIn("Enabled", texts)
            self.assertTrue(any("1/1 enabled" in text for text in texts))
        finally:
            app.destroy()

    def test_selected_account_can_be_handed_to_coding_without_changing_account_selection(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            profile = hub.normalize_profile(
                {
                    "id": "claude:handoff",
                    "name": "Claude Handoff",
                    "provider": "claude",
                    "codexHome": str(Path(tempfile.gettempdir()) / "claude-handoff"),
                    "workspace": str(Path.cwd()),
                },
                0,
            )
            app.profiles = [profile]
            app.selected_profile = hub.profile_id(profile)
            app.refresh_native_threads = lambda: None
            app.show_section("accounts")
            app.use_selected_in_coding()
            app.update_idletasks()

            self.assertEqual(app.active_section, "coding")
            self.assertEqual(app.coding_profile_id, hub.profile_id(profile))
            self.assertEqual(app.coding_workspace_var.get(), str(Path.cwd()))
            self.assertEqual(app.selected_profile, hub.profile_id(profile))
            self.assertIn("Claude Handoff", app.coding_profile_var.get())
        finally:
            app.destroy()

    def test_codex_native_events_capture_output_diff_files_and_tokens(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            app.native_messages = []
            app._handle_native_event(
                {
                    "method": "item/started",
                    "params": {
                        "item": {
                            "id": "cmd-1",
                            "type": "commandExecution",
                            "command": "pytest -q",
                            "status": "inProgress",
                        }
                    },
                }
            )
            app._handle_native_event(
                {
                    "method": "item/commandExecution/outputDelta",
                    "params": {"itemId": "cmd-1", "delta": "\x1b[32;1mone passed\x1b[0m"},
                }
            )
            app._handle_native_event(
                {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "id": "cmd-1",
                            "type": "commandExecution",
                            "command": "pytest -q",
                            "status": "completed",
                            "aggregatedOutput": "\x1b[32;1mone passed\x1b[0m",
                            "exitCode": 0,
                        }
                    },
                }
            )
            app._handle_native_event(
                {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "id": "patch-1",
                            "type": "fileChange",
                            "status": "completed",
                            "changes": [{"path": "src/app.py", "kind": "update", "diff": "+ok"}],
                        }
                    },
                }
            )
            app._handle_native_event(
                {"method": "turn/diff/updated", "params": {"diff": "diff --git a/src/app.py b/src/app.py"}}
            )
            app._handle_native_event(
                {
                    "method": "thread/tokenUsage/updated",
                    "params": {"tokenUsage": {"total": {"totalTokens": 321}}},
                }
            )

            activity = next(message for message in app.native_messages if message.get("nativeId") == "cmd-1")
            self.assertIn("one passed", activity["text"])
            self.assertIn("exit 0", activity["text"])
            self.assertNotIn("\x1b", activity["text"])
            self.assertEqual(app.native_file_changes[0]["path"], "src/app.py")
            self.assertIn("diff --git", app.native_turn_diff)
            diff_activity = next(message for message in app.native_messages if message.get("nativeId") == "active-diff")
            self.assertEqual(diff_activity["kind"], "diff")
            self.assertIn("diff --git", diff_activity["diff"])
            self.assertEqual(hub.native_token_usage_label(app.native_token_usage), "321")
        finally:
            app.destroy()

    def test_codex_plan_updates_render_readable_statuses(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            app.native_messages = []
            app._handle_native_event(
                {
                    "method": "turn/plan/updated",
                    "params": {
                        "explanation": "Working plan",
                        "plan": [
                            {"step": "Inspect files", "status": "completed"},
                            {"step": "Patch UI", "status": "inProgress"},
                            {"step": "Run tests", "status": "pending"},
                        ],
                    },
                }
            )
            activity = next(message for message in app.native_messages if message.get("nativeId") == "active-plan")
            self.assertIn("[done] Inspect files", activity["text"])
            self.assertIn("[active] Patch UI", activity["text"])
            self.assertIn("[todo] Run tests", activity["text"])
            self.assertEqual(activity["kind"], "plan")
            self.assertNotIn('"status"', activity["text"])
        finally:
            app.destroy()

    def test_coding_stream_renders_rich_activity_widgets(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            app.native_messages = [
                {
                    "role": "activity",
                    "kind": "diff",
                    "title": "File changes",
                    "text": "Current diff",
                    "diff": "diff --git a/app.py b/app.py\n-old\n+new",
                    "nativeId": "diff-1",
                },
                {
                    "role": "activity",
                    "kind": "plan",
                    "title": "Plan",
                    "text": "Plan\n[done] Inspect files\n[active] Patch UI",
                    "nativeId": "plan-1",
                },
                {
                    "role": "assistant",
                    "text": "Here is the screenshot:\n![missing](C:/Temp/not-real.png)",
                    "nativeId": "assistant-1",
                },
            ]
            app._render_coding_stream()
            children = app.coding_stream_text.winfo_children()
            self.assertGreaterEqual(len(children), 3)
        finally:
            app.destroy()

    def test_claude_permission_dialog_returns_expected_allow_shape(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            app._native_request_dialog = lambda *_args, **_kwargs: "allow"
            decision = app._ask_claude_permission(
                {
                    "tool_name": "PowerShell",
                    "input": {"command": "Get-Location", "description": "Read cwd"},
                    "tool_use_id": "toolu_test",
                }
            )
            self.assertEqual(decision["behavior"], "allow")
            self.assertEqual(decision["updatedInput"], {})
            self.assertEqual(decision["toolUseID"], "toolu_test")
            self.assertEqual(decision["decisionClassification"], "user_temporary")
            activity = next(message for message in app.native_messages if message.get("nativeId") == "claude-permission-toolu_test")
            self.assertIn("Claude permission allowed", activity["text"])
        finally:
            app.destroy()

    def test_claude_permission_bridge_http_roundtrip(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            app._native_request_dialog = lambda *_args, **_kwargs: "allow"
            url, token = app._ensure_claude_permission_bridge()
            body = json.dumps(
                {
                    "tool_name": "PowerShell",
                    "input": {"command": "Get-Location"},
                    "tool_use_id": "toolu_http",
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            )

            deadline = time.time() + 3
            response_payload: dict = {}

            def post_request() -> None:
                nonlocal response_payload
                with urllib.request.urlopen(request, timeout=5) as response:
                    response_payload = json.loads(response.read().decode("utf-8"))

            thread = threading.Thread(target=post_request)
            thread.start()
            while time.time() < deadline and thread.is_alive():
                app.update()
                time.sleep(0.01)
            thread.join(timeout=1)
            self.assertEqual(response_payload["behavior"], "allow")
            self.assertEqual(response_payload["toolUseID"], "toolu_http")
        finally:
            app.destroy()

    def test_claude_ask_user_question_returns_answers_in_updated_input(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            app._native_request_dialog = lambda *_args, **_kwargs: "Yes"
            decision = app._ask_claude_permission(
                {
                    "tool_name": "AskUserQuestion",
                    "input": {
                        "questions": [
                            {
                                "question": "Continue?",
                                "header": "Continue",
                                "options": [
                                    {"label": "Yes", "description": "Continue the task."},
                                    {"label": "No", "description": "Stop now."},
                                ],
                                "multiSelect": False,
                            }
                        ]
                    },
                    "tool_use_id": "toolu_question",
                }
            )
            self.assertEqual(decision["behavior"], "allow")
            self.assertEqual(decision["toolUseID"], "toolu_question")
            self.assertEqual(decision["updatedInput"]["answers"], {"Continue?": "Yes"})
            self.assertEqual(decision["updatedInput"]["questions"][0]["question"], "Continue?")
            activity = next(message for message in app.native_messages if message.get("nativeId") == "claude-question-toolu_question")
            self.assertIn("Claude question answered", activity["text"])
        finally:
            app.destroy()

    def test_claude_exit_plan_mode_uses_plan_review_dialog(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            original_plan = "# Plan\n\n1. Inspect files"
            edited_plan = "# Plan\n\n1. Inspect files\n2. Run tests"
            app._native_plan_review_dialog = lambda plan, path="": edited_plan
            decision = app._ask_claude_permission(
                {
                    "tool_name": "ExitPlanMode",
                    "input": {
                        "plan": original_plan,
                        "planFilePath": "C:/Users/batty/.claude/plans/test.md",
                    },
                    "tool_use_id": "toolu_plan",
                }
            )
            self.assertEqual(decision["behavior"], "allow")
            self.assertEqual(decision["toolUseID"], "toolu_plan")
            self.assertEqual(decision["updatedInput"]["plan"], edited_plan)
            self.assertEqual(decision["updatedInput"]["planFilePath"], "C:/Users/batty/.claude/plans/test.md")
            activity = next(message for message in app.native_messages if message.get("nativeId") == "claude-plan-toolu_plan")
            self.assertIn("Claude plan edited and approved", activity["text"])
            self.assertIn("Run tests", activity["text"])
        finally:
            app.destroy()

    def test_claude_plan_mode_status_event_renders_activity(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            app._handle_stream_event(
                "claude",
                {"type": "system", "subtype": "status", "permissionMode": "plan"},
            )
            activity = next(message for message in app.native_messages if message.get("nativeId") == "claude-permission-mode")
            self.assertIn("Claude plan mode active", activity["text"])
        finally:
            app.destroy()

    def test_claude_stream_events_capture_rich_diff_plan_and_image_activity(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            app.native_messages = []
            app.native_file_changes = []
            app._handle_stream_event(
                "claude",
                {
                    "type": "assistant",
                    "message": {
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
                        ]
                    },
                },
            )
            app._handle_stream_event(
                "claude",
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_image",
                                "content": [
                                    {"type": "image", "source": {"type": "file", "path": "C:/Temp/screen.png"}}
                                ],
                            }
                        ]
                    },
                },
            )

            edit = next(message for message in app.native_messages if message.get("nativeId") == "toolu_edit")
            plan = next(message for message in app.native_messages if message.get("nativeId") == "toolu_plan")
            image = next(message for message in app.native_messages if message.get("nativeId") == "toolu_image:result")
            self.assertEqual(edit["kind"], "diff")
            self.assertIn("+old = False", edit["diff"])
            self.assertEqual(app.native_file_changes[0]["path"], "src/app.py")
            self.assertEqual(plan["kind"], "plan")
            self.assertEqual(image["kind"], "image")
            self.assertEqual(image["imageRefs"][0]["path"], "C:/Temp/screen.png")
        finally:
            app.destroy()

    def test_claude_rate_limit_event_updates_selected_profile(self) -> None:
        old_save = hub.save_profiles
        hub.save_profiles = lambda _profiles: None
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            profile = hub.normalize_profile(
                {
                    "id": "claude:limits",
                    "name": "Claude Limits",
                    "provider": "claude",
                    "codexHome": str(Path(tempfile.gettempdir()) / "claude-limits"),
                },
                0,
            )
            app.profiles = [profile]
            app.coding_profile_id = hub.profile_id(profile)
            app._handle_stream_event(
                "claude",
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {
                        "status": "allowed_warning",
                        "rateLimitType": "seven_day",
                        "utilization": 0.98,
                        "resetsAt": 1783134000,
                    },
                },
            )
            self.assertEqual(profile["weeklyLimitUsedPercent"], "98.0")
            self.assertTrue(str(profile["weeklyLimitResetUtc"]).endswith("Z"))
            self.assertEqual(profile["weeklyResetEstimateSource"], "claude-rate-limit-event")
            activity = next(message for message in app.native_messages if message.get("nativeId") == "claude-rate-limit")
            self.assertIn("seven_day", activity["text"])
            self.assertIn("98% used", activity["text"])
        finally:
            hub.save_profiles = old_save
            app.destroy()

    def test_cursor_native_transport_uses_installed_agent(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            with tempfile.TemporaryDirectory() as tmp:
                profile = hub.normalize_profile(
                    {
                        "id": "cursor:transport",
                        "name": "Cursor Transport",
                        "provider": "cursor",
                        "codexHome": tmp,
                    },
                    0,
                )
                app.cursor_agent_path = "cursor-agent.cmd"
                transport = app._create_native_transport(profile, Path.cwd())
                self.assertEqual(transport.provider, "cursor")
                self.assertEqual(transport.executable, "cursor-agent.cmd")
        finally:
            app.destroy()

    def test_reset_markers_repeat_weekly_and_all_visible_stats_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_db = hub.HISTORY_DB_FILE
            hub.HISTORY_DB_FILE = Path(tmp) / "history.sqlite3"
            try:
                profile = hub.normalize_profile(
                    {
                        "id": "codex:weekly",
                        "name": "Weekly Account",
                        "provider": "codex",
                        "codexHome": str(Path(tmp) / "home"),
                        "weeklyResetEstimateUtc": "2026-07-04T03:00:00+00:00",
                        "weeklyResetEstimateSource": "api",
                        "usageDailyBuckets": [{"startDate": "2026-07-04", "tokens": 42}],
                        "lastLimitsRefreshUtc": "2026-07-01T00:00:00+00:00",
                    },
                    0,
                )
                hub.record_profile_history(profile, refresh_reason="test")
                app = hub.AccountCalendarApp()
                app.withdraw()
                app.profiles = [profile]
                app.selected_profile = "all"
                app.calendar_year = 2026
                app.calendar_month = 7
                app.selected_date = "2026-07-04"
                markers = app.reset_markers()
                self.assertIn("2026-07-04", markers)
                self.assertIn("2026-07-11", markers)
                app.render()
                app.update_idletasks()
                self.assertGreater(len(app.breakdown_scroll.inner.winfo_children()), 0)
                app.destroy()
            finally:
                hub.HISTORY_DB_FILE = old_db

    def test_online_button_and_link_card_render_for_selected_account(self) -> None:
        app = hub.AccountCalendarApp()
        try:
            app.withdraw()
            profile = hub.normalize_profile(
                {
                    "id": "codex:online",
                    "name": "Online Account",
                    "provider": "codex",
                    "codexHome": str(Path(tempfile.gettempdir()) / "online-home"),
                    "onlineLinks": ["Team Portal | https://example.com/team"],
                },
                0,
            )
            app.profiles = [profile]
            app.selected_profile = hub.profile_id(profile)
            app.render()
            app.update_idletasks()
            self.assertEqual(app.account_action_buttons["online"].cget("state"), "normal")

            texts: list[str] = []

            def collect_text(widget) -> None:
                try:
                    texts.append(str(widget.cget("text")))
                except Exception:
                    pass
                for child in widget.winfo_children():
                    collect_text(child)

            collect_text(app.breakdown_scroll.inner)
            self.assertIn("Online links", texts)
            self.assertIn("Web login needed", texts)
            self.assertTrue(any("Team Portal" in text for text in texts))
        finally:
            app.destroy()

    def test_selecting_account_repaints_right_detail_without_pool_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_db = hub.HISTORY_DB_FILE
            hub.HISTORY_DB_FILE = Path(tmp) / "history.sqlite3"
            app = None
            try:
                first = hub.normalize_profile(
                    {
                        "id": "codex:first-detail",
                        "name": "First Detail",
                        "provider": "codex",
                        "codexHome": str(Path(tmp) / "first"),
                        "usageDailyBuckets": [{"startDate": "2026-07-01", "tokens": 100}],
                        "weeklyResetEstimateUtc": "2026-07-03T00:00:00+00:00",
                        "lastLimitsRefreshUtc": "2026-07-01T00:00:00+00:00",
                    },
                    0,
                )
                second = hub.normalize_profile(
                    {
                        "id": "codex:second-detail",
                        "name": "Second Detail",
                        "provider": "codex",
                        "codexHome": str(Path(tmp) / "second"),
                        "usageDailyBuckets": [{"startDate": "2026-07-01", "tokens": 200}],
                        "lastLimitsRefreshUtc": "2026-07-01T00:00:00+00:00",
                    },
                    1,
                )
                hub.record_profile_history(first, refresh_reason="test")
                hub.record_profile_history(second, refresh_reason="test")
                app = hub.AccountCalendarApp()
                app.withdraw()
                app.profiles = [first, second]
                app.selected_profile = "all"
                app.selected_date = "2026-07-01"
                app.calendar_year = 2026
                app.calendar_month = 7
                app.render()
                app.select_profile(hub.profile_id(first))
                app.update_idletasks()

                self.assertEqual(app.detail_title.cget("text"), "First Detail")
                self.assertIn("Codex", app.detail_subtitle.cget("text"))

                texts: list[str] = []

                def collect_text(widget) -> None:
                    try:
                        texts.append(str(widget.cget("text")))
                    except Exception:
                        pass
                    for child in widget.winfo_children():
                        collect_text(child)

                collect_text(app.breakdown_scroll.inner)
                self.assertIn("First Detail", texts)
                self.assertNotIn("All visible accounts", texts)
                self.assertNotIn("Pooled dashboard stats from visible profile history", texts)

                metric_texts: list[str] = []
                collect_text(app.detail_metrics)
                metric_texts.extend(texts)
                self.assertIn("100", metric_texts)
                self.assertNotIn("300", metric_texts)
            finally:
                if app is not None:
                    app.destroy()
                hub.HISTORY_DB_FILE = old_db


if __name__ == "__main__":
    unittest.main()
