"""Refresh, desktop-login capture, and account-action dispatch for the Accounts
screen, plus tick/theme/lifecycle (mixed into AccountsScreen)."""

from __future__ import annotations

import datetime as _dt


from ai_account_hub import data
from ai_account_hub import core as L

from ai_account_hub.ui.screens.accounts_screen.workers import ActionWorker, RefreshWorker


class _ActionsMixin:
    # ---------- real refresh (background thread, update in place) ----------
    def refresh_all(self, reason: str = "refresh-all") -> None:
        from ai_account_hub import demo_data
        if demo_data.DEMO:
            self._append_log("Demo mode: refresh is disabled (showing sample data).")
            return
        if self._worker is not None and self._worker.isRunning():
            return
        if not self._profiles:
            return
        self._append_log("Refreshing all accounts…")
        self.refreshing.emit(True)
        self._worker = RefreshWorker(self._profiles, reason=reason)
        self._worker.progress.connect(self._append_log)
        self._worker.one_done.connect(self._on_one_refreshed)
        self._worker.finished_all.connect(self._on_refresh_done)
        self._worker.start()

    def _on_one_refreshed(self, pid: str, _ok: bool) -> None:
        # update just this card + summary + (if selected) the detail rail, in place
        self._rebuild_cards()
        self._update_summary()
        if pid == self._selected:
            self._update_detail()

    def _on_refresh_done(self) -> None:
        self._append_log("Refresh complete.")
        self.refreshing.emit(False)
        self.profiles_changed.emit(list(self._profiles))
        self.refresh()

    def _append_log(self, line: str) -> None:
        stamp = _dt.datetime.now().strftime("%H:%M:%S")
        entry: list[str] = []
        for sub in str(line).splitlines() or [""]:
            entry.append(f"[{stamp}] {sub}" if not entry else f"          {sub}")
        self._log_lines.append("\n".join(entry))
        self._log_lines = self._log_lines[-60:]
        self.log_view.setText("\n".join(reversed(self._log_lines)))
        self.activity.emit(str(line))

    def _begin_desktop_login_capture(self, profile: dict) -> None:
        from PySide6.QtWidgets import QMessageBox

        ok, message = data.engine().claude_desktop_login(profile, self._profiles)
        self._append_log(message)
        if not ok:
            QMessageBox.warning(self, "Action failed", message)
            return
        self._desktop_capture_pid = data.profile_id(profile)
        self._desktop_capture_started_at = _dt.datetime.now()
        self._desktop_capture_deadline = self._desktop_capture_started_at + _dt.timedelta(minutes=10)
        self._desktop_capture_last_state = ""
        self._desktop_capture_timer.start()
        self._append_log(f"Watching Claude Desktop login for {profile.get('name')}; capture will happen automatically.")

    def _stop_desktop_login_capture_watch(self) -> None:
        self._desktop_capture_timer.stop()
        self._desktop_capture_pid = None
        self._desktop_capture_deadline = None
        self._desktop_capture_started_at = None
        self._desktop_capture_last_state = ""

    def _poll_desktop_login_capture(self) -> None:
        if not self._desktop_capture_pid:
            self._stop_desktop_login_capture_watch()
            return
        profile = self._find(self._desktop_capture_pid)
        if profile is None:
            self._append_log("Stopped Claude Desktop login watch: selected profile no longer exists.")
            self._stop_desktop_login_capture_watch()
            return
        if self._desktop_capture_deadline and _dt.datetime.now() > self._desktop_capture_deadline:
            self._append_log(f"Timed out waiting for Claude Desktop login for {profile.get('name')}. Press Desktop Login to try again.")
            self._stop_desktop_login_capture_watch()
            return

        status = data.engine().claude_desktop_login_capture_status(profile, since=self._desktop_capture_started_at)
        state = str(status.get("state") or "")
        message = str(status.get("message") or "")
        if state in {"ready", "ready_needs_stop"} and self._action_worker is not None and self._action_worker.isRunning():
            if self._desktop_capture_last_state != "ready_busy":
                self._append_log(message)
                self._append_log("Claude Desktop login is ready; waiting for the current action to finish before capture.")
                self._desktop_capture_last_state = "ready_busy"
            return

        if state and state != self._desktop_capture_last_state:
            self._append_log(message)
            self._desktop_capture_last_state = state

        if state in {"ready", "ready_needs_stop"}:
            self._append_log(f"Capturing Claude Desktop login for {profile.get('name')}…")
            self._stop_desktop_login_capture_watch()
            self._start_blocking(lambda p=profile: data.engine().claude_capture_desktop(p, stop_desktop=True, relaunch_after=True))
            return

        if bool(status.get("done")) and not bool(status.get("ok")):
            self._stop_desktop_login_capture_watch()

    # ---------- account action dispatch (all buttons) ----------
    def run_action(self, key: str) -> None:
        self._run_action(key)

    def _run_action(self, key: str) -> None:
        from PySide6.QtWidgets import QMessageBox
        eng = data.engine()
        profile = self._find(self._selected or "")

        # actions that don't need a selected account
        if key == "add":
            self._add_profile_dialog()
            return
        if profile is None:
            QMessageBox.information(self, "No account", "Select an account first.")
            return

        # fast, inline actions
        if key == "use_in_coding":
            if not data.coding_capable(profile):
                QMessageBox.information(
                    self,
                    "Desktop-only account",
                    "This account can be switched in Claude Desktop, but it cannot be used by Claude Code.",
                )
                return
            self.use_in_coding_requested.emit(self._selected or "")
            self._append_log(f"Handed {profile.get('name')} to Coding.")
            return
        if key == "set_timer":
            until = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=5)
            profile["cooldownUntilUtc"] = until.isoformat()
            data.save_profiles(self._profiles)
            self.profiles_changed.emit(list(self._profiles))
            self._append_log(f"Started 5-hour local timer for {profile.get('name')}.")
            self._rebuild_cards(); self._update_detail()
            return
        if key == "clear_timer":
            profile["cooldownUntilUtc"] = ""
            data.save_profiles(self._profiles)
            self.profiles_changed.emit(list(self._profiles))
            self._append_log(f"Cleared local timer for {profile.get('name')}.")
            self._rebuild_cards(); self._update_detail()
            return
        if key in {"edit", "rename", "delete"}:
            self._edit_action(key, profile)
            return
        if key == "online":
            self._online_menu(profile)
            return
        if key == "refresh":
            if self._worker is not None and self._worker.isRunning():
                return
            self._append_log(f"Refreshing {profile.get('name')}…")
            self.refreshing.emit(True)
            self._worker = RefreshWorker([profile], reason="manual")
            self._worker.progress.connect(self._append_log)
            self._worker.one_done.connect(self._on_one_refreshed)
            self._worker.finished_all.connect(self._on_refresh_done)
            self._worker.start()
            return
        if key == "desktop_login":
            self._begin_desktop_login_capture(profile)
            return

        # map key -> engine call (fast launch actions run inline; slow ones threaded)
        launch = {
            "login": lambda: eng.action_login(profile, device=False),
            "device": lambda: eng.action_login(profile, device=True),
            "logout": lambda: eng.action_logout(profile),
            "cli": lambda: eng.action_cli(profile),
            "home": lambda: eng.action_home(profile),
            "seed": lambda: eng.action_seed(profile),
        }
        blocking = {
            "status": lambda: eng.action_status(profile),
            "doctor": lambda: eng.action_doctor(profile),
            "use_reset": lambda: eng.use_reset_credit(profile),
            "desktop": (
                lambda: eng.codex_switch_desktop(profile)
                if data.provider_key(profile) == "codex"
                else (
                    eng.claude_switch_desktop(profile, self._profiles)
                    if data.provider_key(profile) == "claude"
                    else eng.action_desktop(profile)
                )
            ),
            "dry_run": lambda: eng.codex_dry_run(profile),
            "restore": lambda: eng.codex_restore_backup(),
        }
        if key in launch:
            ok, message = launch[key]()
            self._append_log(message)
            if not ok:
                QMessageBox.warning(self, "Action failed", message)
            return
        if key in blocking:
            if key == "use_reset":
                if QMessageBox.warning(
                    self, "Use reset credit",
                    f"Use one real Codex rate-limit reset credit for {profile.get('name')}?",
                    QMessageBox.Ok | QMessageBox.Cancel,
                ) != QMessageBox.Ok:
                    return
            self._append_log(f"Running {key} for {profile.get('name')}…")
            self._start_blocking(blocking[key])
            return

    def _start_blocking(self, fn) -> None:
        if self._action_worker is not None and self._action_worker.isRunning():
            return
        self._action_worker = ActionWorker(fn)
        self._action_worker.done.connect(self._on_action_done)
        self._action_worker.start()

    def _on_action_done(self, ok: bool, message: str) -> None:
        self._append_log(message)
        data.save_profiles(self._profiles)
        self.profiles_changed.emit(list(self._profiles))
        self._rebuild_cards(); self._update_summary(); self._update_detail()
        if len(message) > 90 or "\n" in message:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Result", message)

    def _online_menu(self, profile: dict) -> None:
        from PySide6.QtWidgets import QMenu
        links = data.engine().online_links(profile)
        if not links:
            self._append_log(f"No online links configured for {profile.get('name')}.")
            return
        menu = QMenu(self)
        for link in links:
            act = menu.addAction(str(link.get("label") or "Open"))
            act.triggered.connect(lambda _c=False, l=link: self._append_log(data.engine().open_online_link(profile, l)[1]))
        btn = self.action_buttons.get("online")
        if btn is not None:
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _edit_action(self, key: str, profile: dict) -> None:
        from PySide6.QtWidgets import QInputDialog, QMessageBox
        if key == "delete":
            if QMessageBox.question(self, "Delete account", f"Delete '{profile.get('name')}'?") != QMessageBox.Yes:
                return
            self._profiles = [p for p in self._profiles if data.profile_id(p) != data.profile_id(profile)]
            data.save_profiles(self._profiles)
            self._selected = None
            self.profiles_changed.emit(list(self._profiles))
            self._append_log(f"Deleted {profile.get('name')}.")
            self.refresh()
            return
        if key == "rename":
            new, ok = QInputDialog.getText(self, "Rename account", "New name:", text=str(profile.get("name", "")))
            if ok and new.strip():
                profile["name"] = new.strip()
                data.save_profiles(self._profiles)
                self.profiles_changed.emit(list(self._profiles))
                self._append_log(f"Renamed to {new.strip()}.")
                self._rebuild_cards(); self._update_detail()
            return
        # edit: full form modal
        from ai_account_hub.ui.modals import EditProfileDialog
        dlg = EditProfileDialog(self, profile)
        if dlg.exec():
            data.save_profiles(self._profiles)
            self.profiles_changed.emit(list(self._profiles))
            self._append_log(f"Updated {profile.get('name')}.")
            self._rebuild_cards(); self._update_detail()

    def _add_profile_dialog(self) -> None:
        from ai_account_hub.ui.modals import AddProfileDialog
        dlg = AddProfileDialog(self, len(self._profiles))
        if not dlg.exec() or dlg.result_profile is None:
            return
        profile = dlg.result_profile
        self._profiles.append(profile)
        self._selected = data.profile_id(profile)
        data.save_profiles(self._profiles)
        self.profiles_changed.emit(list(self._profiles))
        self._append_log(f"Added profile: {profile.get('name')} ({data.provider_label(profile)}).")
        self.refresh()

    def tick(self) -> None:
        for card in self._cards.values():
            card.update_runtime()
        self._apply_desktop_active()
        self._update_summary()
        if self._selected is not None:
            profile = self._find(self._selected)
            if profile is not None:
                state = data.account_state(profile)
                self.detail_pill.setText(L.status_badge_text(profile, state))
                self.detail_pill.set_kind(data.STATE_PILL.get(state, "idle"))
                self.kv_rows["Weekly reset"].setText(
                    L.format_countdown(profile.get("weeklyResetEstimateUtc") or profile.get("weeklyLimitResetUtc"))
                )
                self.kv_rows["Session reset"].setText(L.format_countdown(profile.get("shortLimitResetUtc")))

    def apply_theme(self) -> None:
        t = self._tm.tokens
        self.setStyleSheet(f"background:{t['border']};")
        if self._body is not None:
            self._body.setStyleSheet(f"background:{t['border']};")
        for col in self._columns:
            col.setStyleSheet(f"background:{t['bg']};")
        self.summary_bar.set_theme(t)
        for card in self._cards.values():
            card.set_theme(t)
        # Re-apply the inline-styled chrome that doesn't read the global QSS.
        self._log_refresh_link.setStyleSheet(f"color:{t['accent']};font-size:11px;font-weight:600;")
        self.log_view.setStyleSheet(
            f"background:{t['panel']};border:1px solid {t['border']};border-radius:8px;"
            f"padding:8px;font-family:Consolas,'Courier New',monospace;font-size:10px;color:{t['text2']};"
        )
        self._update_stats()
        self._update_detail()
        self.calendar._render_grid()

    def close_workers(self) -> None:
        for worker in (self._worker, self._action_worker):
            if worker is not None and worker.isRunning():
                worker.requestInterruption()
                worker.wait(2000)
