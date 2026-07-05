from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
import datetime as _dt
from types import SimpleNamespace
from pathlib import Path


TEST_ROOT = tempfile.TemporaryDirectory()
os.environ["AI_HUB_LAUNCHER_ROOT"] = TEST_ROOT.name
os.environ["AI_HUB_DISCOVERY_BOOTSTRAPPED"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTextEdit

from ai_account_hub import data
from ai_account_hub import core as L
from ai_account_hub.ui.main_window import MainWindow
from ai_account_hub.coding_bridge import CodingBridge
from ai_account_hub.engine import HubEngine
from ai_account_hub.ui.modals import AddProfileDialog


def _write_cookie_db(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute("create table cookies (host_key text, name text, expires_utc integer, value text)")
        con.execute(
            "insert into cookies(host_key, name, expires_utc, value) values (?, ?, ?, ?)",
            (".claude.ai", "sessionKey", 99999999999999999, value),
        )
        con.commit()
    finally:
        con.close()


def _write_claude_code_state(home: Path, account_uuid: str, email: str = "user@example.com") -> None:
    home.mkdir(parents=True, exist_ok=True)
    payload = {
        "oauthAccount": {
            "accountUuid": account_uuid,
            "emailAddress": email,
            "organizationUuid": f"org-{account_uuid}",
            "organizationType": "claude_pro",
        }
    }
    (home / ".claude.json").write_text(json.dumps(payload), encoding="utf-8")


def _profiles() -> list[dict]:
    root = Path(TEST_ROOT.name)
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    codex_home = root / "codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "auth.json").write_text("{}", encoding="utf-8")
    claude_home = root / "claude"
    claude_home.mkdir(parents=True, exist_ok=True)
    _write_claude_code_state(claude_home, "claude-test-account")
    return [
        L.normalize_profile(
            {
                "id": "codex:test",
                "name": "Codex Test",
                "provider": "codex",
                "codexHome": str(codex_home),
                "workspace": str(workspace),
                "accountPlan": "Plus",
                "shortLimitUsedPercent": "25",
                "weeklyLimitUsedPercent": "40",
            },
            0,
        ),
        L.normalize_profile(
            {
                "id": "claude:test",
                "name": "Claude Test",
                "provider": "claude",
                "codexHome": str(claude_home),
                "claudeConfigDir": str(claude_home),
                "workspace": str(workspace),
                "accountPlan": "Pro",
                "shortLimitUsedPercent": "10",
                "weeklyLimitUsedPercent": "20",
                "usageSummary": {"claudeAuthStatus": {"loggedIn": True}},
            },
            1,
        ),
    ]


def test_first_run_starts_with_no_seeded_profiles(monkeypatch, tmp_path) -> None:
    launcher = tmp_path / "launcher"
    profiles_file = launcher / "profiles.json"
    monkeypatch.setattr(L.mod, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L.mod, "PROFILES_FILE", profiles_file)

    assert L.load_profiles() == []
    assert json.loads(profiles_file.read_text(encoding="utf-8")) == []
    assert data.load_profiles() == []


def _window() -> tuple[QApplication, MainWindow]:
    app = QApplication.instance() or QApplication([])
    data._ENGINE = None
    L.save_profiles(_profiles())
    L.save_settings(
        {
            "theme": "Midnight Slate",
            "autoRefreshEnabled": True,
            "autoRefreshMinutes": 10,
            "sortMode": "Manual",
            "cardTemplate": "Balanced",
        }
    )
    window = MainWindow(app)
    window.show()
    app.processEvents()
    return app, window


def test_window_restores_menus_icons_and_all_visible_mode() -> None:
    app, window = _window()
    try:
        assert [action.text() for action in window.menu_bar.actions()] == [
            "File",
            "Edit",
            "Window",
            "Theme",
            "Help",
        ]
        help_actions = window._menus[-1].actions()
        setup_action = next(action for action in help_actions if action.text() == "Account setup")
        assert [action.text() for action in setup_action.menu().actions()] == [
            "Claude Code",
            "Codex",
            "Cursor",
            "Antigravity",
        ]
        assert (Path(__file__).resolve().parents[1] / "Docs" / "CLAUDE_ACCOUNT_SETUP.md").is_file()
        assert window.accounts._selected is None
        assert window.accounts.action_host.isHidden()
        assert all(not card.avatar._pixmap.isNull() for card in window.accounts._cards.values())

        codex_id = data.profile_id(window.accounts._profiles[0])
        window.accounts.select(codex_id)
        assert not window.accounts.action_host.isHidden()
        assert "use_reset" in window.accounts.action_buttons
        assert not window.accounts.action_buttons["use_reset"].isEnabled()
        assert "logout" not in window.accounts.action_buttons

        window.accounts.select_all()
        assert window.accounts.action_host.isHidden()
        assert "200%" in window.accounts.kv_rows["Weekly left"].text()
    finally:
        window.close()
        app.processEvents()


def test_claude_account_detail_shows_desktop_state_and_actions() -> None:
    app, window = _window()
    try:
        claude = window.accounts._profiles[1]
        window.accounts.select(data.profile_id(claude))
        assert window.accounts.kv_rows["Desktop"].text() == "Not captured"
        assert "matching Claude account" in window.accounts.kv_rows["Desktop"].toolTip()
        assert "desktop_login" in window.accounts.action_buttons
        assert "desktop_capture" not in window.accounts.action_buttons
    finally:
        window.close()
        app.processEvents()


def test_add_dialog_does_not_offer_test_only_claude_desktop_profile() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = AddProfileDialog(None, 0)
    offered = [
        dialog.provider.itemData(index)
        for index in range(dialog.provider.count())
    ]

    assert dialog.provider.findText("Claude Desktop (free)") == -1
    assert all(
        not (isinstance(item, (tuple, list)) and len(item) > 1 and item[1] == "desktop")
        for item in offered
    )
    dialog.close()
    app.processEvents()


def test_add_dialog_explains_paid_claude_two_login_flow() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = AddProfileDialog(None, 0)
    index = dialog.provider.findText("Claude Code (paid)")
    assert index >= 0
    dialog.provider.setCurrentIndex(index)

    assert dialog.plan.isEnabled()
    assert "use Login for Claude Code" in dialog.provider_note.text()
    assert "Desktop Login for Claude Desktop" in dialog.provider_note.text()

    dialog.close()
    app.processEvents()


def test_desktop_only_profile_blocks_cli_and_is_hidden_from_coding() -> None:
    app, window = _window()
    try:
        desktop_only = L.normalize_profile(
            {
                "id": "claude:desktop-only-ui",
                "name": "Claude Gmail",
                "provider": "claude",
                "claudeProfileType": "desktop",
                "accountPlan": "Free",
                "claudeDesktopCaptured": False,
            },
            2,
        )
        profiles = [*window.accounts._profiles, desktop_only]
        window.accounts.set_profiles(profiles)
        window.coding.set_profiles(profiles)
        window.accounts.select(data.profile_id(desktop_only))

        assert "use_in_coding" not in window.accounts.action_buttons
        assert "login" not in window.accounts.action_buttons
        assert "status" not in window.accounts.action_buttons
        assert "doctor" not in window.accounts.action_buttons
        assert "desktop_login" in window.accounts.action_buttons
        assert "desktop_capture" not in window.accounts.action_buttons
        assert "cli" in window.accounts.action_buttons
        assert not window.accounts.action_buttons["cli"].isEnabled()
        assert all(data.profile_id(item) != data.profile_id(desktop_only) for item in window.coding._profiles)
    finally:
        window.close()
        app.processEvents()


def test_activity_log_renders_newest_first_without_splitting_multiline_entries() -> None:
    app, window = _window()
    try:
        window.accounts._append_log("old event")
        window.accounts._append_log("new event\ncontinued detail")
        text = window.accounts.log_view.text()
        assert text.index("new event") < text.index("old event")
        assert text.index("new event") < text.index("continued detail") < text.index("old event")
    finally:
        window.close()
        app.processEvents()


def test_capacity_sorts_default_to_most_left_and_keep_unknown_last() -> None:
    app, window = _window()
    try:
        root = Path(TEST_ROOT.name)
        profiles = [
            L.normalize_profile(
                {
                    "id": "codex:high",
                    "name": "High left",
                    "provider": "codex",
                    "codexHome": str(root / "sort-high"),
                    "weeklyLimitUsedPercent": "10",
                    "shortLimitUsedPercent": "20",
                },
                0,
            ),
            L.normalize_profile(
                {
                    "id": "codex:mid",
                    "name": "Middle left",
                    "provider": "codex",
                    "codexHome": str(root / "sort-mid"),
                    "weeklyLimitUsedPercent": "60",
                    "shortLimitUsedPercent": "70",
                },
                1,
            ),
            L.normalize_profile(
                {
                    "id": "codex:empty",
                    "name": "Exhausted",
                    "provider": "codex",
                    "codexHome": str(root / "sort-empty"),
                    "weeklyLimitUsedPercent": "100",
                    "shortLimitUsedPercent": "100",
                    "limitReachedType": "session",
                },
                2,
            ),
            L.normalize_profile(
                {
                    "id": "claude:unknown",
                    "name": "Unknown",
                    "provider": "claude",
                    "claudeProfileType": "desktop",
                },
                3,
            ),
        ]
        window.accounts.set_profiles(profiles)

        window.accounts.sort_by.setCurrentText("Session left")
        assert window.accounts._sort_desc is True
        assert window.accounts.sort_dir_btn.text() == "↓"
        assert [p["name"] for p in window.accounts._sorted_profiles()] == [
            "High left", "Middle left", "Exhausted", "Unknown",
        ]

        window.accounts._toggle_sort_dir()
        assert [p["name"] for p in window.accounts._sorted_profiles()] == [
            "Exhausted", "Middle left", "High left", "Unknown",
        ]

        window.accounts.sort_by.setCurrentText("Weekly left")
        assert window.accounts._sort_desc is True
        assert [p["name"] for p in window.accounts._sorted_profiles()] == [
            "High left", "Middle left", "Exhausted", "Unknown",
        ]
    finally:
        window.close()
        app.processEvents()


def test_coding_controls_use_native_values_and_account_switches() -> None:
    app, window = _window()
    try:
        coding = window.coding
        codex = window.accounts._profiles[0]
        claude = window.accounts._profiles[1]
        coding.set_active_account(data.profile_id(codex))
        codex_state = coding._composer_state[data.profile_id(codex)]
        assert codex_state["access"] in {"workspace", "read-only", "full-access"}
        assert coding._provider_controls_row.count() == 3

        coding.set_active_account(data.profile_id(claude))
        claude_state = coding._composer_state[data.profile_id(claude)]
        assert claude_state["access"] in {"default", "accept-edits", "plan", "full-access"}
        assert not coding.switch_avatar._pixmap.isNull()
        assert "Claude Code" in coding.session_caption.text()
    finally:
        window.close()
        app.processEvents()


def test_busy_send_queues_text_and_attachments() -> None:
    app, window = _window()
    try:
        coding = window.coding
        claude = window.accounts._profiles[1]
        coding.set_active_account(data.profile_id(claude))
        attachment = Path(TEST_ROOT.name) / "note.txt"
        attachment.write_text("test", encoding="utf-8")
        coding._attachments = [attachment]
        coding.input.setPlainText("Follow up")
        coding._bridge._busy = True
        coding._send()
        assert coding._queued is not None
        assert coding._queued["text"] == "Follow up"
        assert coding._queued["attachments"] == [attachment]
        assert not coding._queued_row.isHidden()
    finally:
        window.coding._bridge._busy = False
        window.close()
        app.processEvents()


def test_history_renders_incrementally_and_keeps_rich_blocks() -> None:
    app, window = _window()
    try:
        coding = window.coding
        generation = coding._history_generation
        messages = [
            {"role": "user", "text": "Please change it", "nativeId": "u1"},
            {"role": "assistant", "text": "**Done.**", "nativeId": "a1"},
            {
                "role": "activity",
                "kind": "diff",
                "title": "File changes",
                "status": "completed",
                "nativeId": "d1",
                "text": "Updated app.py",
                "diff": "--- a/app.py\n+++ b/app.py\n@@\n-old\n+new",
            },
        ]
        coding._on_history_loaded(generation, messages)
        deadline = time.time() + 2
        while coding._history_pending and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert coding._history_total == 3
        assert coding._history_pending == []
        diff_cards = [
            coding._conv_layout.itemAt(index).widget()
            for index in range(coding._conv_layout.count())
            if coding._conv_layout.itemAt(index).widget() is not None
            and coding._conv_layout.itemAt(index).widget().objectName() == "card"
        ]
        assert len(diff_cards) == 1
        assert diff_cards[0].maximumWidth() == 900
        diff_text = diff_cards[0].findChild(QTextEdit)
        assert diff_text is not None
        assert "+new" in diff_text.toPlainText()
    finally:
        window.close()
        app.processEvents()


def test_ready_countdown_does_not_render_ready_in_now() -> None:
    profile = _profiles()[0]
    profile["cooldownUntilUtc"] = L.iso_utc_now()
    assert L.ready_countdown(profile) == ""


def test_native_transport_receives_model_effort_access_and_resume_id() -> None:
    profile = _profiles()[1]
    bridge = CodingBridge()
    bridge._session_id = "claude-session"
    engine = SimpleNamespace(claude_code_path="claude.exe")
    transport = bridge._make_transport(
        data.native(),
        engine,
        profile,
        Path(profile["workspace"]),
        "claude",
        {"model": "opus", "effort": "high", "access": "plan"},
    )
    try:
        assert transport.session_id == "claude-session"
        assert transport.model == "opus"
        assert transport.effort == "high"
        assert transport.access_mode == "plan"
    finally:
        bridge.close()


def test_claude_all_models_week_is_not_overwritten_by_model_row() -> None:
    parsed = L.parse_claude_usage_text(
        "Current session: 44% used · resets Jul 3, 1am (Australia/Sydney)\n"
        "Current week (all models): 22% used · resets Jul 4, 1pm (Australia/Sydney)\n"
        "Current week (Fable): 0% used"
    )
    assert parsed["weeklyUsedPercent"] == 22.0
    assert parsed["weeklyModelUsedPercent"] == {"Fable": 0.0}


def test_claude_desktop_switch_swaps_saved_state(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "claude-desktop-switch"
    launcher = root / "launcher"
    default_home = root / "roaming" / "Claude"
    profile_home = root / "profiles" / "claude-one"
    workspace = root / "workspace"
    for path in (default_home / "Network", profile_home, workspace):
        path.mkdir(parents=True, exist_ok=True)
    account_uuid = "same-account"
    _write_claude_code_state(profile_home, account_uuid)
    (default_home / "config.json").write_text('{"oauth:tokenCache":"default","lastKnownAccountUuid":"default-account"}', encoding="utf-8")
    _write_cookie_db(default_home / "Network" / "Cookies", "default-cookie")

    monkeypatch.setattr(L, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L, "CLAUDE_ROAMING_HOME", default_home)

    profile = L.normalize_profile(
        {
            "id": "claude:desktop-test",
            "name": "Claude Desktop Test",
            "provider": "claude",
            "codexHome": str(profile_home),
            "claudeConfigDir": str(profile_home),
            "workspace": str(workspace),
        },
        0,
    )

    class DummyEngine(HubEngine):
        def __init__(self) -> None:
            self.claude_desktop_path = str(root / "Claude.exe")
            self.started = False

        def _stop_claude_desktop(self) -> str:
            return "stopped"

        def _start_claude_desktop(self, selected: dict) -> None:
            self.started = selected is profile

    engine = DummyEngine()
    selected_state = engine._claude_desktop_state_root(profile)
    (selected_state / "Network").mkdir(parents=True, exist_ok=True)
    (selected_state / "config.json").write_text(
        '{"oauth:tokenCache":"selected","lastKnownAccountUuid":"same-account"}',
        encoding="utf-8",
    )
    _write_cookie_db(selected_state / "Network" / "Cookies", "selected-cookie")

    ok, message = engine.action_desktop(profile)

    assert ok
    assert engine.started
    assert "Switched Claude Desktop" in message
    assert "selected" in (default_home / "config.json").read_text(encoding="utf-8")
    con = sqlite3.connect(default_home / "Network" / "Cookies")
    try:
        cookie_value = con.execute("select value from cookies where name='sessionKey'").fetchone()[0]
    finally:
        con.close()
    assert cookie_value == "selected-cookie"
    marker = json.loads((launcher / "claude-desktop-active-profile.json").read_text(encoding="utf-8"))
    assert marker["name"] == "Claude Desktop Test"


def test_claude_desktop_switch_skips_rewrite_when_profile_is_already_active(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "claude-desktop-already-active"
    launcher = root / "launcher"
    default_home = root / "roaming" / "Claude"
    profile_home = root / "profiles" / "claude-one"
    workspace = root / "workspace"
    for path in (default_home / "Network", profile_home, workspace):
        path.mkdir(parents=True, exist_ok=True)
    account_uuid = "same-account"
    _write_claude_code_state(profile_home, account_uuid)
    (default_home / "config.json").write_text(
        '{"oauth:tokenCache":"default","lastKnownAccountUuid":"same-account"}',
        encoding="utf-8",
    )
    _write_cookie_db(default_home / "Network" / "Cookies", "default-cookie")

    monkeypatch.setattr(L, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L, "CLAUDE_ROAMING_HOME", default_home)

    profile = L.normalize_profile(
        {
            "id": "claude:already-active-test",
            "name": "Claude Already Active",
            "provider": "claude",
            "codexHome": str(profile_home),
            "claudeConfigDir": str(profile_home),
            "workspace": str(workspace),
        },
        0,
    )

    class DummyEngine(HubEngine):
        def __init__(self) -> None:
            self.claude_desktop_path = str(root / "Claude.exe")
            self.started = False
            self.stopped = False

        def _stop_claude_desktop(self) -> str:
            self.stopped = True
            return "stopped"

        def _start_claude_desktop(self, selected: dict) -> None:
            self.started = selected is profile

    engine = DummyEngine()
    selected_state = engine._claude_desktop_state_root(profile)
    (selected_state / "Network").mkdir(parents=True, exist_ok=True)
    (selected_state / "config.json").write_text(
        '{"oauth:tokenCache":"selected","lastKnownAccountUuid":"same-account"}',
        encoding="utf-8",
    )
    _write_cookie_db(selected_state / "Network" / "Cookies", "selected-cookie")
    engine._write_claude_desktop_marker(profile, selected_state)

    ok, message = engine.action_desktop(profile)

    assert ok
    assert engine.started
    assert not engine.stopped
    assert "already synced" in message
    assert "default" in (default_home / "config.json").read_text(encoding="utf-8")


def test_claude_desktop_only_switch_restores_saved_login_without_code_identity(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "claude-desktop-only-switch"
    launcher = root / "launcher"
    default_home = root / "roaming" / "Claude"
    profile_home = root / "profiles" / "claude-free"
    workspace = root / "workspace"
    for path in (default_home / "Network", profile_home, workspace):
        path.mkdir(parents=True, exist_ok=True)
    (default_home / "config.json").write_text(
        '{"oauth:tokenCache":"other","lastKnownAccountUuid":"other-account"}',
        encoding="utf-8",
    )
    _write_cookie_db(default_home / "Network" / "Cookies", "other-cookie")

    monkeypatch.setattr(L, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L, "CLAUDE_ROAMING_HOME", default_home)

    profile = L.normalize_profile(
        {
            "id": "claude:desktop-only-switch",
            "name": "Claude Gmail",
            "provider": "claude",
            "claudeProfileType": "desktop",
            "claudeDesktopAccountUuid": "saved-free-account",
            "claudeDesktopCaptured": True,
            "codexHome": str(profile_home),
            "claudeConfigDir": str(profile_home),
            "workspace": str(workspace),
            "accountPlan": "Free",
        },
        0,
    )

    class DummyEngine(HubEngine):
        def __init__(self) -> None:
            self.claude_desktop_path = str(root / "Claude.exe")
            self.started = False
            self.stopped = False

        def _stop_claude_desktop(self) -> str:
            self.stopped = True
            return "stopped"

        def _start_claude_desktop(self, selected: dict) -> None:
            self.started = selected is profile

    engine = DummyEngine()
    state = engine._claude_desktop_state_root(profile)
    (state / "Network").mkdir(parents=True, exist_ok=True)
    (state / "config.json").write_text(
        '{"oauth:tokenCache":"saved","lastKnownAccountUuid":"saved-free-account"}',
        encoding="utf-8",
    )
    _write_cookie_db(state / "Network" / "Cookies", "saved-free-cookie")

    ok, message = engine.action_desktop(profile)

    assert ok
    assert engine.stopped
    assert engine.started
    assert "Switched Claude Desktop" in message
    assert engine._claude_desktop_account_uuid(default_home) == "saved-free-account"
    con = sqlite3.connect(default_home / "Network" / "Cookies")
    try:
        cookie_value = con.execute("select value from cookies where name='sessionKey'").fetchone()[0]
    finally:
        con.close()
    assert cookie_value == "saved-free-cookie"
    assert not (profile_home / ".claude.json").exists()


def test_two_claude_code_accounts_keep_cli_and_desktop_state_isolated(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "two-claude-code-accounts"
    launcher = root / "launcher"
    default_home = root / "roaming" / "Claude"
    home_a = root / "profiles" / "claude-code-a"
    home_b = root / "profiles" / "claude-code-b"
    workspace = root / "workspace"
    for path in (default_home / "Network", home_a, home_b, workspace):
        path.mkdir(parents=True, exist_ok=True)
    _write_claude_code_state(home_a, "paid-account-a", "a@example.com")
    _write_claude_code_state(home_b, "paid-account-b", "b@example.com")
    (default_home / "config.json").write_text(
        '{"oauth:tokenCache":"live-a","lastKnownAccountUuid":"paid-account-a"}',
        encoding="utf-8",
    )
    _write_cookie_db(default_home / "Network" / "Cookies", "live-a-cookie")

    monkeypatch.setattr(L, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L, "CLAUDE_ROAMING_HOME", default_home)

    profile_a = L.normalize_profile(
        {
            "id": "claude:paid-a",
            "name": "Claude Code A",
            "provider": "claude",
            "claudeProfileType": "code",
            "codexHome": str(home_a),
            "claudeConfigDir": str(home_a),
            "workspace": str(workspace),
            "accountPlan": "Pro",
        },
        0,
    )
    profile_b = L.normalize_profile(
        {
            "id": "claude:paid-b",
            "name": "Claude Code B",
            "provider": "claude",
            "claudeProfileType": "code",
            "codexHome": str(home_b),
            "claudeConfigDir": str(home_b),
            "workspace": str(workspace),
            "accountPlan": "Pro",
        },
        1,
    )

    class DummyEngine(HubEngine):
        def __init__(self) -> None:
            self.claude_desktop_path = str(root / "Claude.exe")
            self.claude_code_path = str(root / "claude.exe")
            self.started: list[dict] = []
            self.stop_count = 0

        def _stop_claude_desktop(self) -> str:
            self.stop_count += 1
            return "stopped"

        def _start_claude_desktop(self, selected: dict) -> None:
            self.started.append(selected)

    engine = DummyEngine()
    state_a = engine._claude_desktop_state_root(profile_a)
    state_b = engine._claude_desktop_state_root(profile_b)
    for state, uuid, token in (
        (state_a, "paid-account-a", "saved-a"),
        (state_b, "paid-account-b", "saved-b"),
    ):
        (state / "Network").mkdir(parents=True, exist_ok=True)
        (state / "config.json").write_text(
            json.dumps({"oauth:tokenCache": token, "lastKnownAccountUuid": uuid}),
            encoding="utf-8",
        )
        _write_cookie_db(state / "Network" / "Cookies", f"{token}-cookie")
    engine._write_claude_desktop_marker(profile_a, state_a)

    captured_envs: list[str] = []

    def fake_run_capture(executable, args, cwd, env=None, timeout=60):
        captured_envs.append(str((env or {}).get("CLAUDE_CONFIG_DIR") or ""))
        return SimpleNamespace(returncode=0, stdout='{"loggedIn":true}', stderr="")

    monkeypatch.setattr(L, "run_capture", fake_run_capture)
    engine._run_claude_auth_status(profile_a)
    engine._run_claude_auth_status(profile_b)

    ok_b, message_b = engine.claude_switch_desktop(profile_b, [profile_a, profile_b])
    assert ok_b, message_b
    assert engine._claude_desktop_account_uuid(default_home) == "paid-account-b"

    ok_a, message_a = engine.claude_switch_desktop(profile_a, [profile_a, profile_b])
    assert ok_a, message_a
    assert engine._claude_desktop_account_uuid(default_home) == "paid-account-a"

    assert captured_envs == [str(home_a), str(home_b)]
    assert engine._claude_code_account_uuid(profile_a) == "paid-account-a"
    assert engine._claude_code_account_uuid(profile_b) == "paid-account-b"
    assert engine.started == [profile_b, profile_a]
    assert engine.stop_count == 2
    assert engine._claude_desktop_state_has_login(state_a)
    assert engine._claude_desktop_state_has_login(state_b)


def test_claude_switch_rescues_pending_desktop_login_before_replacing_it(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "claude-pending-login-rescue"
    launcher = root / "launcher"
    default_home = root / "roaming" / "Claude"
    gmail_home = root / "profiles" / "claude-gmail"
    proton_home = root / "profiles" / "claude-proton"
    workspace = root / "workspace"
    for path in (default_home / "Network", gmail_home, proton_home, workspace):
        path.mkdir(parents=True, exist_ok=True)
    (default_home / "config.json").write_text(
        '{"oauth:tokenCache":"gmail","oauth:tokenCacheV2":"gmail-v2",'
        '"lastKnownAccountUuid":"gmail-account"}',
        encoding="utf-8",
    )
    _write_cookie_db(default_home / "Network" / "Cookies", "gmail-cookie")
    _write_claude_code_state(proton_home, "proton-account")

    monkeypatch.setattr(L, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L, "CLAUDE_ROAMING_HOME", default_home)

    gmail = L.normalize_profile(
        {
            "id": "claude:pending-gmail",
            "name": "Gmail Claude",
            "provider": "claude",
            "claudeProfileType": "desktop",
            "codexHome": str(gmail_home),
            "claudeConfigDir": str(gmail_home),
            "workspace": str(workspace),
            "accountPlan": "Free",
        },
        0,
    )
    proton = L.normalize_profile(
        {
            "id": "claude:target-proton",
            "name": "Claude Code Proton",
            "provider": "claude",
            "claudeProfileType": "code",
            "codexHome": str(proton_home),
            "claudeConfigDir": str(proton_home),
            "workspace": str(workspace),
            "accountPlan": "Pro",
        },
        1,
    )

    class DummyEngine(HubEngine):
        def __init__(self) -> None:
            self.claude_desktop_path = str(root / "Claude.exe")
            self.stop_count = 0
            self.started = None

        def _stop_claude_desktop(self) -> str:
            self.stop_count += 1
            return "stopped"

        def _start_claude_desktop(self, selected: dict) -> None:
            self.started = selected

    engine = DummyEngine()
    gmail_state = engine._claude_desktop_state_root(gmail)
    engine._write_claude_desktop_marker(gmail, gmail_state, pending=True)
    proton_state = engine._claude_desktop_state_root(proton)
    (proton_state / "Network").mkdir(parents=True, exist_ok=True)
    (proton_state / "config.json").write_text(
        '{"oauth:tokenCache":"proton","lastKnownAccountUuid":"proton-account"}',
        encoding="utf-8",
    )
    _write_cookie_db(proton_state / "Network" / "Cookies", "proton-cookie")

    ok, message = engine.claude_switch_desktop(proton, [gmail, proton])

    assert ok
    assert engine.started is proton
    assert engine.stop_count == 1
    assert "Completed pending Desktop Login capture for Gmail Claude" in message
    assert gmail["claudeDesktopCaptured"] is True
    assert gmail["claudeDesktopAccountUuid"] == "gmail-account"
    assert L.effective_state(gmail) == "ready"
    assert engine._claude_desktop_state_has_login(gmail_state)
    assert engine._claude_desktop_account_uuid(default_home) == "proton-account"


def test_claude_desktop_switch_rejects_old_login_identity(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "claude-desktop-mismatch"
    launcher = root / "launcher"
    default_home = root / "roaming" / "Claude"
    profile_home = root / "profiles" / "claude-one"
    workspace = root / "workspace"
    for path in (default_home / "Network", profile_home, workspace):
        path.mkdir(parents=True, exist_ok=True)
    _write_claude_code_state(profile_home, "wanted-account")

    monkeypatch.setattr(L, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L, "CLAUDE_ROAMING_HOME", default_home)

    profile = L.normalize_profile(
        {
            "id": "claude:mismatch-test",
            "name": "Claude Mismatch Test",
            "provider": "claude",
            "codexHome": str(profile_home),
            "claudeConfigDir": str(profile_home),
            "workspace": str(workspace),
        },
        0,
    )

    class DummyEngine(HubEngine):
        def __init__(self) -> None:
            self.claude_desktop_path = str(root / "Claude.exe")
            self.started = False
            self.stopped = False

        def _stop_claude_desktop(self) -> str:
            self.stopped = True
            return "stopped"

        def _start_claude_desktop(self, selected: dict) -> None:
            self.started = True

    engine = DummyEngine()
    selected_state = engine._claude_desktop_state_root(profile)
    (selected_state / "Network").mkdir(parents=True, exist_ok=True)
    (selected_state / "config.json").write_text(
        '{"oauth:tokenCache":"old","lastKnownAccountUuid":"old-account"}',
        encoding="utf-8",
    )
    _write_cookie_db(selected_state / "Network" / "Cookies", "old-cookie")

    ok, message = engine.action_desktop(profile)

    assert not ok
    assert not engine.stopped
    assert not engine.started
    assert "does not match its Claude Code profile" in message
    assert not (default_home / "Network" / "Cookies").exists()


def test_claude_capture_desktop_rejects_mismatched_current_login(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "claude-desktop-capture-mismatch"
    launcher = root / "launcher"
    default_home = root / "roaming" / "Claude"
    profile_home = root / "profiles" / "claude-one"
    workspace = root / "workspace"
    for path in (default_home / "Network", profile_home, workspace):
        path.mkdir(parents=True, exist_ok=True)
    _write_claude_code_state(profile_home, "wanted-account")
    (default_home / "config.json").write_text(
        '{"oauth:tokenCache":"wrong","lastKnownAccountUuid":"wrong-account"}',
        encoding="utf-8",
    )
    _write_cookie_db(default_home / "Network" / "Cookies", "wrong-cookie")

    monkeypatch.setattr(L, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L, "CLAUDE_ROAMING_HOME", default_home)

    profile = L.normalize_profile(
        {
            "id": "claude:capture-mismatch-test",
            "name": "Claude Capture Mismatch",
            "provider": "claude",
            "codexHome": str(profile_home),
            "claudeConfigDir": str(profile_home),
            "workspace": str(workspace),
        },
        0,
    )

    class DummyEngine(HubEngine):
        def __init__(self) -> None:
            self.claude_desktop_path = str(root / "Claude.exe")

        def _stop_claude_desktop(self) -> str:
            return "stopped"

    engine = DummyEngine()
    ok, message = engine.claude_capture_desktop(profile)

    assert not ok
    assert "Refusing to save Claude Desktop" in message
    assert not engine._claude_desktop_state_root(profile).exists()


def test_claude_capture_desktop_saves_matching_current_login(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "claude-desktop-capture-match"
    launcher = root / "launcher"
    default_home = root / "roaming" / "Claude"
    profile_home = root / "profiles" / "claude-one"
    workspace = root / "workspace"
    for path in (default_home / "Network", profile_home, workspace):
        path.mkdir(parents=True, exist_ok=True)
    _write_claude_code_state(profile_home, "wanted-account")
    (default_home / "config.json").write_text(
        '{"oauth:tokenCache":"right","lastKnownAccountUuid":"wanted-account"}',
        encoding="utf-8",
    )
    _write_cookie_db(default_home / "Network" / "Cookies", "right-cookie")

    monkeypatch.setattr(L, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L, "CLAUDE_ROAMING_HOME", default_home)

    profile = L.normalize_profile(
        {
            "id": "claude:capture-match-test",
            "name": "Claude Capture Match",
            "provider": "claude",
            "codexHome": str(profile_home),
            "claudeConfigDir": str(profile_home),
            "workspace": str(workspace),
        },
        0,
    )

    class DummyEngine(HubEngine):
        def __init__(self) -> None:
            self.claude_desktop_path = str(root / "Claude.exe")

        def _stop_claude_desktop(self) -> str:
            return "stopped"

    engine = DummyEngine()
    ok, message = engine.claude_capture_desktop(profile)

    assert ok
    assert "Saved Claude Desktop login" in message
    saved = engine._claude_desktop_state_root(profile)
    assert "wanted-account" in (saved / "config.json").read_text(encoding="utf-8")
    con = sqlite3.connect(saved / "Network" / "Cookies")
    try:
        cookie_value = con.execute("select value from cookies where name='sessionKey'").fetchone()[0]
    finally:
        con.close()
    assert cookie_value == "right-cookie"


def test_claude_desktop_only_capture_binds_identity_without_code_cli(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "claude-desktop-only-capture"
    launcher = root / "launcher"
    default_home = root / "roaming" / "Claude"
    profile_home = root / "profiles" / "claude-gmail"
    workspace = root / "workspace"
    for path in (default_home / "Network", profile_home, workspace):
        path.mkdir(parents=True, exist_ok=True)
    (default_home / "config.json").write_text(
        '{"oauth:tokenCache":"desktop","oauth:tokenCacheV2":"desktop-v2",'
        '"lastKnownAccountUuid":"desktop-free-account"}',
        encoding="utf-8",
    )
    _write_cookie_db(default_home / "Network" / "Cookies", "desktop-cookie")

    monkeypatch.setattr(L, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L, "CLAUDE_ROAMING_HOME", default_home)

    profile = L.normalize_profile(
        {
            "id": "claude:desktop-only-capture",
            "name": "Claude Gmail",
            "provider": "claude",
            "claudeProfileType": "desktop",
            "codexHome": str(profile_home),
            "claudeConfigDir": str(profile_home),
            "workspace": str(workspace),
            "accountPlan": "Free",
        },
        0,
    )

    class DummyEngine(HubEngine):
        def __init__(self) -> None:
            self.claude_desktop_path = str(root / "Claude.exe")
            self.claude_code_path = ""

        def _stop_claude_desktop(self) -> str:
            return "stopped"

    engine = DummyEngine()
    ok, message = engine.claude_capture_desktop(profile)

    assert ok
    assert "Saved Claude Desktop login" in message
    assert profile["claudeDesktopAccountUuid"] == "desktop-free-account"
    assert profile["claudeDesktopCaptured"] is True
    assert profile["usageSummary"]["desktopOnly"] is True
    status = engine.claude_desktop_state_status(profile)
    assert status["state"] == "ready"
    assert status["label"] == "Saved"

    cli_ok, cli_message = engine.action_cli(profile)
    assert not cli_ok
    assert "CLI is disabled" in cli_message


def test_claude_desktop_only_refresh_does_not_call_code_cli(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "claude-desktop-only-refresh"
    launcher = root / "launcher"
    default_home = root / "roaming" / "Claude"
    profile_home = root / "profiles" / "claude-google"
    workspace = root / "workspace"
    for path in (default_home, profile_home, workspace):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(L, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L, "CLAUDE_ROAMING_HOME", default_home)

    profile = L.normalize_profile(
        {
            "id": "claude:desktop-only-refresh",
            "name": "Claude Google",
            "provider": "claude",
            "claudeProfileType": "desktop",
            "claudeDesktopAccountUuid": "desktop-google-account",
            "claudeDesktopCaptured": True,
            "codexHome": str(profile_home),
            "claudeConfigDir": str(profile_home),
            "workspace": str(workspace),
            "accountPlan": "Free",
        },
        0,
    )

    class DummyEngine(HubEngine):
        def __init__(self) -> None:
            self.claude_desktop_path = str(root / "Claude.exe")
            self.claude_code_path = ""

        def _run_claude_auth_status(self, profile=None) -> str:
            raise AssertionError("Desktop-only refresh must not call Claude Code")

        def _run_claude_usage_probe(self, selected: dict) -> dict:
            raise AssertionError("Desktop-only refresh must not call Claude Code")

    engine = DummyEngine()
    state = engine._claude_desktop_state_root(profile)
    (state / "Network").mkdir(parents=True, exist_ok=True)
    (state / "config.json").write_text(
        '{"oauth:tokenCache":"saved","lastKnownAccountUuid":"desktop-google-account"}',
        encoding="utf-8",
    )
    _write_cookie_db(state / "Network" / "Cookies", "saved-cookie")

    result = engine.refresh_profile(profile)

    assert result["ok"] is True
    assert result["desktopOnly"] is True
    assert profile["lastLimitsError"] == ""
    assert "limits and usage are unavailable" in profile["lastUsageError"]
    assert profile["weeklyLimitUsedPercent"] == ""


def test_claude_capture_desktop_rejects_oauth_uuid_without_session_cookie(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "claude-desktop-capture-oauth-only"
    launcher = root / "launcher"
    default_home = root / "roaming" / "Claude"
    profile_home = root / "profiles" / "claude-one"
    workspace = root / "workspace"
    for path in (default_home, profile_home, workspace):
        path.mkdir(parents=True, exist_ok=True)
    _write_claude_code_state(profile_home, "wanted-account")
    (default_home / "config.json").write_text(
        '{"oauth:tokenCache":"right","oauth:tokenCacheV2":"right-v2","lastKnownAccountUuid":"wanted-account"}',
        encoding="utf-8",
    )

    monkeypatch.setattr(L, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L, "CLAUDE_ROAMING_HOME", default_home)

    profile = L.normalize_profile(
        {
            "id": "claude:capture-oauth-only-test",
            "name": "Claude Capture OAuth Only",
            "provider": "claude",
            "codexHome": str(profile_home),
            "claudeConfigDir": str(profile_home),
            "workspace": str(workspace),
        },
        0,
    )

    class DummyEngine(HubEngine):
        def __init__(self) -> None:
            self.claude_desktop_path = str(root / "Claude.exe")

        def _stop_claude_desktop(self) -> str:
            return "stopped"

    engine = DummyEngine()
    assert engine.claude_desktop_login_capture_status(profile)["state"] == "waiting_session"

    ok, message = engine.claude_capture_desktop(profile)

    assert not ok
    assert "not logged in" in message


def test_claude_login_capture_status_uses_recent_desktop_logged_in_signal(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "claude-desktop-log-signal"
    launcher = root / "launcher"
    default_home = root / "roaming" / "Claude"
    profile_home = root / "profiles" / "claude-one"
    workspace = root / "workspace"
    for path in (default_home / "logs", profile_home, workspace):
        path.mkdir(parents=True, exist_ok=True)
    _write_claude_code_state(profile_home, "wanted-account")
    (default_home / "config.json").write_text(
        '{"oauth:tokenCache":"right","oauth:tokenCacheV2":"right-v2","lastKnownAccountUuid":"wanted-account"}',
        encoding="utf-8",
    )
    since = _dt.datetime.now() - _dt.timedelta(seconds=5)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    (default_home / "logs" / "main.log").write_text(
        f"{stamp} [info] claude.ai account active and logged in\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(L, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L, "CLAUDE_ROAMING_HOME", default_home)

    profile = L.normalize_profile(
        {
            "id": "claude:log-signal-test",
            "name": "Claude Log Signal",
            "provider": "claude",
            "codexHome": str(profile_home),
            "claudeConfigDir": str(profile_home),
            "workspace": str(workspace),
        },
        0,
    )

    class DummyEngine(HubEngine):
        def __init__(self) -> None:
            self.claude_desktop_path = str(root / "Claude.exe")

    engine = DummyEngine()
    status = engine.claude_desktop_login_capture_status(profile, since=since)

    assert status["state"] == "ready_needs_stop"
    assert status["ok"] is True


def test_claude_login_capture_status_uses_fresh_identity_after_grace_period(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "claude-desktop-fresh-identity"
    launcher = root / "launcher"
    default_home = root / "roaming" / "Claude"
    profile_home = root / "profiles" / "claude-free"
    workspace = root / "workspace"
    for path in (default_home, profile_home, workspace):
        path.mkdir(parents=True, exist_ok=True)
    (default_home / "config.json").write_text(
        '{"oauth:tokenCache":"fresh","oauth:tokenCacheV2":"fresh-v2",'
        '"lastKnownAccountUuid":"fresh-free-account"}',
        encoding="utf-8",
    )

    monkeypatch.setattr(L, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L, "CLAUDE_ROAMING_HOME", default_home)

    profile = L.normalize_profile(
        {
            "id": "claude:fresh-identity",
            "name": "Claude Free",
            "provider": "claude",
            "claudeProfileType": "desktop",
            "codexHome": str(profile_home),
            "claudeConfigDir": str(profile_home),
            "workspace": str(workspace),
        },
        0,
    )

    class DummyEngine(HubEngine):
        def __init__(self) -> None:
            self.claude_desktop_path = str(root / "Claude.exe")

    engine = DummyEngine()
    status = engine.claude_desktop_login_capture_status(
        profile,
        since=_dt.datetime.now() - _dt.timedelta(seconds=12),
    )

    assert status["state"] == "ready_needs_stop"
    assert status["ok"] is True
    assert "verify the saved session" in status["message"]


def test_shared_claude_desktop_status_rejects_oauth_uuid_without_session_cookie(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "claude-desktop-status-oauth-only"
    default_home = root / "roaming" / "Claude"
    if default_home.exists():
        shutil.rmtree(default_home)
    default_home.mkdir(parents=True, exist_ok=True)
    (default_home / "config.json").write_text(
        '{"oauth:tokenCache":"right","oauth:tokenCacheV2":"right-v2","lastKnownAccountUuid":"wanted-account"}',
        encoding="utf-8",
    )

    monkeypatch.setattr(L.mod, "CLAUDE_ROAMING_HOME", default_home)
    monkeypatch.setattr(L.mod, "locate_claude_desktop_path", lambda: str(root / "Claude.exe"))
    if hasattr(L.mod, "_CLAUDE_STATUS_CACHE"):
        L.mod._CLAUDE_STATUS_CACHE.clear()

    status = L.claude_desktop_login_status()

    assert status["ready"] is False
    assert status["hasOAuthCache"] is True
    assert status["hasAccountUuid"] is True
    assert status["hasSessionCookie"] is False
    assert "no Desktop session cookie" in status["summary"]


def test_shared_claude_desktop_status_accepts_running_logged_in_signal(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "claude-desktop-status-log-signal"
    default_home = root / "roaming" / "Claude"
    if default_home.exists():
        shutil.rmtree(default_home)
    (default_home / "logs").mkdir(parents=True, exist_ok=True)
    (default_home / "config.json").write_text(
        '{"oauth:tokenCache":"right","oauth:tokenCacheV2":"right-v2","lastKnownAccountUuid":"wanted-account"}',
        encoding="utf-8",
    )
    (default_home / "logs" / "main.log").write_text(
        "2026-07-04 15:35:00 [info] claude.ai account active and logged in\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(L.mod, "CLAUDE_ROAMING_HOME", default_home)
    monkeypatch.setattr(L.mod, "locate_claude_desktop_path", lambda: str(root / "Claude.exe"))
    if hasattr(L.mod, "_CLAUDE_STATUS_CACHE"):
        L.mod._CLAUDE_STATUS_CACHE.clear()

    status = L.claude_desktop_login_status()

    assert status["ready"] is True
    assert status["hasLoggedInSignal"] is True
    assert "running app reports logged in" in status["summary"]


def test_claude_desktop_login_opens_clean_and_marks_pending_capture(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "claude-desktop-auto-login"
    launcher = root / "launcher"
    default_home = root / "roaming" / "Claude"
    profile_home = root / "profiles" / "claude-one"
    workspace = root / "workspace"
    for path in (default_home / "Network", profile_home, workspace):
        path.mkdir(parents=True, exist_ok=True)
    _write_claude_code_state(profile_home, "wanted-account")
    (default_home / "config.json").write_text(
        '{"oauth:tokenCache":"old","lastKnownAccountUuid":"old-account"}',
        encoding="utf-8",
    )
    _write_cookie_db(default_home / "Network" / "Cookies", "old-cookie")

    monkeypatch.setattr(L, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L, "CLAUDE_ROAMING_HOME", default_home)

    profile = L.normalize_profile(
        {
            "id": "claude:auto-login-test",
            "name": "Claude Auto Login",
            "provider": "claude",
            "codexHome": str(profile_home),
            "claudeConfigDir": str(profile_home),
            "workspace": str(workspace),
        },
        0,
    )

    class DummyEngine(HubEngine):
        def __init__(self) -> None:
            self.claude_desktop_path = str(root / "Claude.exe")
            self.started = False

        def _stop_claude_desktop(self) -> str:
            return "stopped"

        def _start_claude_desktop(self, selected: dict) -> None:
            self.started = selected is profile

    engine = DummyEngine()
    ok, message = engine.claude_desktop_login(profile)

    assert ok
    assert engine.started
    assert "capture it automatically" in message
    assert not (default_home / "config.json").exists()
    assert not (default_home / "Network" / "Cookies").exists()
    marker = json.loads((launcher / "claude-desktop-active-profile.json").read_text(encoding="utf-8"))
    assert marker["pendingCapture"] is True
    assert marker["name"] == "Claude Auto Login"


def test_claude_desktop_only_login_opens_clean_without_code_identity(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "claude-desktop-only-login"
    launcher = root / "launcher"
    default_home = root / "roaming" / "Claude"
    profile_home = root / "profiles" / "claude-free"
    workspace = root / "workspace"
    for path in (default_home, profile_home, workspace):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(L, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L, "CLAUDE_ROAMING_HOME", default_home)

    profile = L.normalize_profile(
        {
            "id": "claude:desktop-only-login",
            "name": "Claude Free",
            "provider": "claude",
            "claudeProfileType": "desktop",
            "codexHome": str(profile_home),
            "claudeConfigDir": str(profile_home),
            "workspace": str(workspace),
        },
        0,
    )

    class DummyEngine(HubEngine):
        def __init__(self) -> None:
            self.claude_desktop_path = str(root / "Claude.exe")
            self.started = False

        def _stop_claude_desktop(self) -> str:
            return "stopped"

        def _start_claude_desktop(self, selected: dict) -> None:
            self.started = selected is profile

    engine = DummyEngine()
    ok, message = engine.claude_desktop_login(profile)

    assert ok
    assert engine.started
    assert "clean Claude Desktop login" in message
    marker = json.loads((launcher / "claude-desktop-active-profile.json").read_text(encoding="utf-8"))
    assert marker["pendingCapture"] is True
    assert marker["profileType"] == "desktop"
    assert marker["expectedAccountHash"] == "missing"


def test_claude_auto_capture_desktop_saves_live_login_without_stopping(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "claude-desktop-auto-capture"
    launcher = root / "launcher"
    default_home = root / "roaming" / "Claude"
    profile_home = root / "profiles" / "claude-one"
    workspace = root / "workspace"
    for path in (default_home / "Network", profile_home, workspace):
        path.mkdir(parents=True, exist_ok=True)
    _write_claude_code_state(profile_home, "wanted-account")
    (default_home / "config.json").write_text(
        '{"oauth:tokenCache":"right","lastKnownAccountUuid":"wanted-account"}',
        encoding="utf-8",
    )
    _write_cookie_db(default_home / "Network" / "Cookies", "right-cookie")

    monkeypatch.setattr(L, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L, "CLAUDE_ROAMING_HOME", default_home)

    profile = L.normalize_profile(
        {
            "id": "claude:auto-capture-test",
            "name": "Claude Auto Capture",
            "provider": "claude",
            "codexHome": str(profile_home),
            "claudeConfigDir": str(profile_home),
            "workspace": str(workspace),
        },
        0,
    )

    class DummyEngine(HubEngine):
        def __init__(self) -> None:
            self.claude_desktop_path = str(root / "Claude.exe")
            self.stopped = False

        def _stop_claude_desktop(self) -> str:
            self.stopped = True
            return "stopped"

    engine = DummyEngine()
    assert engine.claude_desktop_login_capture_status(profile)["state"] == "ready"
    ok, message = engine.claude_capture_desktop(profile, stop_desktop=False)

    assert ok
    assert not engine.stopped
    assert "Desktop remains open" in message
    saved = engine._claude_desktop_state_root(profile)
    assert "wanted-account" in (saved / "config.json").read_text(encoding="utf-8")
    marker = json.loads((launcher / "claude-desktop-active-profile.json").read_text(encoding="utf-8"))
    assert marker["pendingCapture"] is False


def test_claude_sync_back_does_not_overwrite_saved_login_with_logged_out_state(monkeypatch) -> None:
    root = Path(TEST_ROOT.name) / "claude-desktop-logout-preserve"
    launcher = root / "launcher"
    default_home = root / "roaming" / "Claude"
    profile_home = root / "profiles" / "claude-one"
    workspace = root / "workspace"
    for path in (default_home, profile_home, workspace):
        path.mkdir(parents=True, exist_ok=True)
    _write_claude_code_state(profile_home, "wanted-account")
    (default_home / "config.json").write_text('{"lastKnownAccountUuid":"wanted-account"}', encoding="utf-8")

    monkeypatch.setattr(L, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L, "CLAUDE_ROAMING_HOME", default_home)

    profile = L.normalize_profile(
        {
            "id": "claude:logout-preserve-test",
            "name": "Claude Logout Preserve",
            "provider": "claude",
            "codexHome": str(profile_home),
            "claudeConfigDir": str(profile_home),
            "workspace": str(workspace),
        },
        0,
    )

    class DummyEngine(HubEngine):
        def __init__(self) -> None:
            self.claude_desktop_path = str(root / "Claude.exe")

    engine = DummyEngine()
    saved = engine._claude_desktop_state_root(profile)
    (saved / "Network").mkdir(parents=True, exist_ok=True)
    (saved / "config.json").write_text(
        '{"oauth:tokenCache":"saved","lastKnownAccountUuid":"wanted-account"}',
        encoding="utf-8",
    )
    _write_cookie_db(saved / "Network" / "Cookies", "saved-cookie")
    engine._write_claude_desktop_marker(profile, saved)

    message = engine._sync_active_claude_desktop_back()

    assert "logged out" in message
    assert "saved" in (saved / "config.json").read_text(encoding="utf-8")
    con = sqlite3.connect(saved / "Network" / "Cookies")
    try:
        cookie_value = con.execute("select value from cookies where name='sessionKey'").fetchone()[0]
    finally:
        con.close()
    assert cookie_value == "saved-cookie"
