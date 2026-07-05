"""General Qt UI tests: window shell, account detail, sorting, coding controls,
history rendering, native transport wiring."""

from _qt_common import *  # noqa: F401,F403
from _qt_common import _profiles, _window, _write_claude_code_state, _write_cookie_db  # noqa: F401

def test_first_run_starts_with_no_seeded_profiles(monkeypatch, tmp_path) -> None:
    launcher = tmp_path / "launcher"
    profiles_file = launcher / "profiles.json"
    monkeypatch.setattr(L.mod, "LAUNCHER_ROOT", launcher)
    monkeypatch.setattr(L.mod, "PROFILES_FILE", profiles_file)

    assert L.load_profiles() == []
    assert json.loads(profiles_file.read_text(encoding="utf-8")) == []
    assert data.load_profiles() == []


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


