"""Standalone Hub window: Statistics, Accounts, tray, and timers."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenuBar, QMessageBox, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from ai_account_hub import data
from ai_account_hub.ui.theme import ThemeManager
from ai_account_hub.ui.tokens import DEFAULT_THEME, THEMES
from ai_account_hub.ui.widgets import NetworkLogo, SegmentedSlider, Spinner, TitleBar, make_button, network_icon
from ai_account_hub.ui.screens.accounts_screen import AccountsScreen
from ai_account_hub.ui.screens.statistics_screen import StatisticsScreen
from ai_account_hub.ui.tray_widget import TrayController, TrayWidgetSettingsDialog
from ai_account_hub.ui.storage_dialog import LocalDataDialog
from ai_account_hub.ui.account_notifications import (
    AccountNotificationMonitor,
    NotificationSettingsDialog,
)
from ai_account_hub.core.community_api import CONSENT_VERSION, CommunityApiError
from ai_account_hub.ui.community_sharing import (
    CommunityConsentDialog,
    CommunityPayloadDialog,
    CommunitySharingControl,
    CommunitySharingPopover,
    CommunityStatusDialog,
    CommunityUploadWorker,
)


class MainWindow(QWidget):
    def __init__(self, app) -> None:
        super().__init__()
        self._tray_controller: TrayController | None = None
        self._restore_maximized = False
        self.setObjectName("root")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.resize(1280, 820)
        # The Accounts dashboard intentionally keeps all three information
        # columns visible; below this width its calendar stops being useful.
        self.setMinimumSize(1180, 680)

        self.settings = data.load_settings()
        self._notification_monitor = AccountNotificationMonitor(self.settings)
        self.theme = ThemeManager(
            app,
            str(self.settings.get("theme") or DEFAULT_THEME),
            str(self.settings.get("appearanceMode") or "dark"),
        )
        self.setWindowIcon(network_icon(self.theme.tokens["accent"]))
        self._active = "accounts"
        self._auto_on = bool(self.settings.get("autoRefreshEnabled", True))
        self._auto_minutes = max(1, int(self.settings.get("autoRefreshMinutes", 10) or 10))
        self._next_auto_refresh = dt.datetime.now() + dt.timedelta(minutes=1)
        self._community_enabled = bool(
            self.settings.get("communitySharingEnabled", False)
            and int(self.settings.get("communityConsentVersion", 0) or 0) == CONSENT_VERSION
            and not data.demo_data.DEMO
        )
        self._community_last_receipt = dict(self.settings.get("communityLastReceipt") or {})
        self._community_retry_after = dt.datetime.min
        self._community_worker: CommunityUploadWorker | None = None
        self._community_worker_reason = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.stack = QStackedWidget()
        self.accounts = AccountsScreen(self.theme)
        self.statistics = StatisticsScreen(self.theme)
        if bool(self.settings.get("communitySharingEnabled", False)) and (
            int(self.settings.get("communityConsentVersion", 0) or 0) != CONSENT_VERSION
            or str(self.settings.get("communityApiMode") or "")
            != self.statistics.community_api.mode
        ):
            # Consent never crosses transport boundaries. In particular, the
            # old offline test opt-in cannot become a live staging opt-in.
            self._community_enabled = False
            self.settings["communitySharingEnabled"] = False
            self.settings["communityConsentVersion"] = 0
            data.save_settings(self.settings)
        if self._community_enabled and not self.statistics.community_api.supports_submissions:
            # A previous local-test opt-in must not carry over as an apparent
            # cloud opt-in while signed Worker ingestion remains unavailable.
            self._community_enabled = False
            self.settings["communitySharingEnabled"] = False
            data.save_settings(self.settings)
        if (
            self.statistics.community_api.mode != "test"
            and str(self._community_last_receipt.get("mode") or "") == "test"
        ):
            self._community_last_receipt = {}
            self.settings["communityLastReceipt"] = {}
            self.settings["communityLastUploadUtc"] = ""
            data.save_settings(self.settings)
        self.stack.addWidget(self.accounts)    # index 0
        self.stack.addWidget(self.statistics)  # index 1

        self.menu_bar = self._build_menu_bar()
        self.titlebar = TitleBar(self, self.theme.tokens["accent"], self.menu_bar)
        root.addWidget(self.titlebar)
        root.addWidget(self._build_header())
        root.addWidget(self.stack, 1)

        profiles = data.load_profiles()
        self.accounts.set_profiles(profiles)
        self.statistics.set_profiles(profiles)
        self._setup_system_tray()
        self._sync_tray_profiles(profiles)

        self.theme.changed.connect(lambda _n: self.titlebar.set_accent(self.theme.tokens["accent"]))
        self.theme.changed.connect(lambda _n: self._logo.set_accent(self.theme.tokens["accent"]))
        self.theme.changed.connect(lambda _n: self._sync_tray_theme())
        self.theme.changed.connect(
            lambda _n: self._community_popover.set_theme(self.theme.tokens)
        )
        self.accounts.activity.connect(self._set_status)
        self.statistics.activity.connect(self._set_status)
        self.statistics.history_updated.connect(self.accounts.refresh)
        self.statistics.history_updated.connect(self._community_history_ready)
        self.accounts.profiles_changed.connect(self._sync_tray_profiles)
        self.accounts.profiles_changed.connect(self.statistics.set_profiles)
        start_section = (
            "statistics"
            if os.environ.get("AI_HUB_DEMO_START_SECTION") == "statistics"
            else "accounts"
        )
        self._select_section(start_section)
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick)
        self._clock.start(1000)

    def _build_menu_bar(self) -> QMenuBar:
        bar = QMenuBar()
        bar.setNativeMenuBar(False)
        # PySide can release a submenu whose Python wrapper has no surviving
        # reference even while QMenuBar still shows its top-level action.
        self._menus = []

        file_menu = bar.addMenu("File")
        self._menus.append(file_menu)
        file_menu.addAction("Reload profiles", self._reload)
        file_menu.addAction("Refresh all", self.accounts.refresh_all)
        file_menu.addSeparator()
        file_menu.addAction("Open profile folder", lambda: os.startfile(str(data.LAUNCHER_ROOT)))
        file_menu.addAction("Local data...", self._open_local_data)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)

        edit_menu = bar.addMenu("Edit")
        self._menus.append(edit_menu)
        edit_menu.addAction("Add account", lambda: self._account_action("add"))
        edit_menu.addAction("Edit selected", lambda: self._account_action("edit"))
        edit_menu.addAction("Rename selected", lambda: self._account_action("rename"))
        edit_menu.addAction("Delete selected", lambda: self._account_action("delete"))
        edit_menu.addSeparator()
        edit_menu.addAction("Community sharing...", self._open_community_status)

        window_menu = bar.addMenu("Window")
        self._menus.append(window_menu)
        window_menu.addAction("Accounts", lambda: self._select_section("accounts"))
        window_menu.addAction("Statistics", lambda: self._select_section("statistics"))
        window_menu.addSeparator()
        window_menu.addAction("Minimize", self.showMinimized)
        window_menu.addAction("Show Best Next", self._show_best_next)
        window_menu.addAction("Widget settings...", self._open_tray_settings)
        window_menu.addAction("Notification settings...", self._open_notification_settings)
        window_menu.addAction("Maximize / Restore", self._toggle_maximized)

        theme_menu = bar.addMenu("Theme")
        self._menus.append(theme_menu)
        # Appearance mode (applies to every theme).
        self._mode_actions = {}
        for label, mode in (("Dark mode", "dark"), ("Light mode", "light")):
            action = theme_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.theme.mode == mode)
            action.triggered.connect(lambda _checked=False, m=mode: self._set_mode(m))
            self._mode_actions[mode] = action
        theme_menu.addSeparator()
        self._theme_actions = {}
        for name in THEMES:
            action = theme_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(name == self.theme.name)
            action.triggered.connect(lambda _checked=False, n=name: self._set_theme(n))
            self._theme_actions[name] = action

        help_menu = bar.addMenu("Help")
        self._menus.append(help_menu)
        help_menu.addAction("Open README", self._open_readme)
        setup_menu = help_menu.addMenu("Account setup")
        for label, doc in (
            ("Claude Code", "CLAUDE_ACCOUNT_SETUP.md"),
            ("Codex", "CODEX_ACCOUNT_SETUP.md"),
            ("Cursor", "CURSOR_ACCOUNT_SETUP.md"),
            ("Antigravity", "ANTIGRAVITY_ACCOUNT_SETUP.md"),
        ):
            setup_menu.addAction(label, lambda _c=False, d=doc: self._open_setup_doc(d))
        help_menu.addSeparator()
        help_menu.addAction("View demo (sample data)", self._open_demo)
        help_menu.addAction("About", self._about)
        return bar

    def _toggle_maximized(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    # ---------- Windows system tray / compact Best Next popup ----------
    def _setup_system_tray(self) -> None:
        self._tray_controller = TrayController(
            self,
            self.theme,
            self._auto_on,
            self.settings,
        )
        self._tray_controller.restore_requested.connect(self._restore_from_tray)
        self._tray_controller.refresh_requested.connect(self._refresh_from_tray)
        self._tray_controller.switch_requested.connect(self._switch_from_tray)
        self._tray_controller.auto_refresh_requested.connect(self._set_auto_refresh)
        self._tray_controller.exit_requested.connect(self.close)
        self._tray_controller.popup_opening.connect(self._sync_tray_profiles)
        self._tray_controller.settings_requested.connect(self._open_tray_settings)
        self._tray_controller.notification_settings_requested.connect(
            self._open_notification_settings
        )
        self._tray_controller.notification_activated.connect(
            self._open_notification_profile
        )

    def _sync_tray_theme(self) -> None:
        icon = network_icon(self.theme.tokens["accent"])
        self.setWindowIcon(icon)
        if self._tray_controller is not None:
            self._tray_controller.set_theme(self.theme.tokens)

    def _active_profile_ids(self, profiles: list[dict]) -> set[str]:
        active_ids: set[str] = set()
        active_name = self.accounts._desktop_active_name()
        if active_name:
            for profile in profiles:
                if (
                    data.provider_key(profile) == "codex"
                    and str(profile.get("name") or "").strip() == active_name
                ):
                    active_ids.add(data.profile_id(profile))

        try:
            marker = json.loads(
                (data.LAUNCHER_ROOT / "claude-desktop-active-profile.json").read_text(
                    encoding="utf-8-sig"
                )
            )
        except Exception:
            marker = {}
        claude_name = str(marker.get("name") or "").strip()
        if claude_name and not bool(marker.get("pendingCapture")):
            for profile in profiles:
                if (
                    data.provider_key(profile) == "claude"
                    and str(profile.get("name") or "").strip() == claude_name
                ):
                    active_ids.add(data.profile_id(profile))
        return active_ids

    def _sync_tray_profiles(self, profiles: list[dict] | None = None) -> None:
        current = list(profiles if profiles is not None else self.accounts._profiles)
        active_ids = self._active_profile_ids(current)
        if self._tray_controller is not None:
            self._tray_controller.set_profiles(current, active_ids)
            for notification in self._notification_monitor.evaluate(current, active_ids):
                self._tray_controller.show_account_notification(notification)

    def _show_best_next(self) -> None:
        if self._tray_controller is not None:
            self._tray_controller.show_popup()

    def _open_tray_settings(self) -> None:
        if self._tray_controller is None:
            return
        dialog = TrayWidgetSettingsDialog(
            self._tray_controller.widget_settings,
            list(self.accounts._profiles),
            parent=self,
        )
        if not dialog.exec():
            return
        selected = dialog.values()
        self.settings.update(selected)
        data.save_settings(self.settings)
        self._tray_controller.apply_widget_settings(selected)

    def _open_local_data(self) -> None:
        LocalDataDialog(self.theme, self).exec()

    def _open_notification_settings(self) -> None:
        if self._tray_controller is None:
            return
        dialog = NotificationSettingsDialog(
            self._notification_monitor.settings,
            parent=self,
        )
        dialog.test_requested.connect(
            lambda: self._tray_controller.show_notification(
                "AI Account Hub notifications",
                "Signal Rail notifications are working.",
                "info",
                7000,
            )
        )
        if not dialog.exec():
            return
        selected = dialog.values()
        self.settings.update(selected)
        data.save_settings(self.settings)
        self._notification_monitor.apply_settings(selected)

    def _open_notification_profile(self, pid: str) -> None:
        # Signal Rail cards carry only a profile ID; restore the dashboard first
        # and let AccountsScreen perform its normal selection/update flow.
        self._restore_from_tray()
        self._select_section("accounts")
        if pid:
            self.accounts.select(pid)

    def _restore_from_tray(self) -> None:
        if self._tray_controller is not None:
            self._tray_controller.hide_popup()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        if self._restore_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def _refresh_from_tray(self) -> None:
        self.accounts.refresh_all(reason="tray")

    def _switch_from_tray(self, pid: str) -> None:
        if self._tray_controller is not None:
            self._tray_controller.hide_popup()
        self._select_section("accounts")
        self.accounts.select(pid)
        self.accounts.run_action("desktop")

    def _minimize_to_tray(self) -> None:
        if self._tray_controller is not None:
            self._tray_controller.minimize(self)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if (
            event.type() == QEvent.Type.WindowStateChange
            and self.isMinimized()
            and self._tray_controller is not None
            and self._tray_controller.available
        ):
            self._restore_maximized = bool(event.oldState() & Qt.WindowMaximized)
            QTimer.singleShot(0, self._minimize_to_tray)

    def _set_theme(self, name: str) -> None:
        self.theme.apply(name)
        self.settings["theme"] = name
        data.save_settings(self.settings)
        for key, action in self._theme_actions.items():
            action.setChecked(key == name)
        self._refresh_theme_visuals()

    def _set_mode(self, mode: str) -> None:
        self.theme.set_mode(mode)
        self.settings["appearanceMode"] = mode
        data.save_settings(self.settings)
        for m, action in self._mode_actions.items():
            action.setChecked(m == mode)
        self._refresh_theme_visuals()

    def _refresh_theme_visuals(self) -> None:
        self._logo.set_accent(self.theme.tokens["accent"])
        self.titlebar.set_accent(self.theme.tokens["accent"])
        self.setWindowIcon(network_icon(self.theme.tokens["accent"]))
        self._refresh_spin.set_color(self.theme.tokens["accent"])
        self.accounts.apply_theme()
        self.statistics.apply_theme()

    def _open_readme(self) -> None:
        target = Path(__file__).resolve().parents[2] / "README.md"
        if target.is_file():
            os.startfile(str(target))

    def _open_setup_doc(self, filename: str) -> None:
        """Open a per-provider account-setup guide from docs/ in the OS viewer."""
        target = Path(__file__).resolve().parents[2] / "docs" / filename
        if target.is_file():
            os.startfile(str(target))

    def _open_demo(self) -> None:
        """Open a second window populated with fake sample accounts and usage
        (AI_HUB_DEMO=1) so people can see the interface — or take a screenshot —
        without exposing any real account data. The demo instance never reads or
        writes the real profiles.json (see the guards in data.py). If this window
        is already the demo, just say so instead of stacking more copies."""
        import sys
        import subprocess
        from ai_account_hub import demo_data
        if demo_data.DEMO:
            QMessageBox.information(
                self, "Demo mode",
                "This window is already showing sample demo data.",
            )
            return
        repo_root = Path(__file__).resolve().parents[2]  # ui/ -> ai_account_hub/ -> repo
        env = dict(os.environ)
        env["AI_HUB_DEMO"] = "1"
        # Open where the sample is most useful. Accounts remains available via
        # the segmented switch, so one demo teaches both supported workspaces.
        env["AI_HUB_DEMO_START_SECTION"] = "statistics"
        try:
            subprocess.Popen(
                [sys.executable, "-m", "ai_account_hub"],
                env=env,
                cwd=str(repo_root),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as error:  # never let a demo launch crash the real app
            QMessageBox.warning(
                self, "Demo mode",
                f"Could not open the demo window:\n{error}",
            )

    def _about(self) -> None:
        QMessageBox.information(
            self,
            "AI Account Hub",
            "A native passthrough workspace for Codex, Claude Code, Cursor, and Antigravity.",
        )

    def _account_action(self, key: str) -> None:
        self._select_section("accounts")
        self.accounts.run_action(key)

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("appHeader")
        header.setFixedHeight(60)
        row = QHBoxLayout(header)
        row.setContentsMargins(20, 0, 20, 0)
        row.setSpacing(14)

        logo_tile = QFrame()
        logo_tile.setObjectName("logoTile")
        logo_tile.setFixedSize(36, 36)
        tl = QVBoxLayout(logo_tile)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setAlignment(Qt.AlignCenter)
        self._logo = NetworkLogo(self.theme.tokens["accent"], size=24)
        tl.addWidget(self._logo, 0, Qt.AlignCenter)
        row.addWidget(logo_tile)

        titlebox = QVBoxLayout()
        titlebox.setSpacing(0)
        # In demo mode make the fake data unmistakable in both the title and the
        # subtitle so a demo window is never confused with the real accounts.
        demo = data.demo_data.DEMO
        t = QLabel("AI Account Hub — Demo" if demo else "AI Account Hub")
        t.setObjectName("appTitle")
        sub = QLabel(
            "Sample data — not your real accounts" if demo
            else "Accounts, limits and usage history"
        )
        sub.setObjectName("appSubtitle")
        titlebox.addWidget(t)
        titlebox.addWidget(sub)
        row.addLayout(titlebox)

        self._seg = SegmentedSlider(
            [("Accounts", "accounts"), ("Statistics", "statistics")],
            self.theme.tokens,
        )
        self._seg.changed.connect(self._select_section)
        self.theme.changed.connect(lambda _n: self._seg.set_theme(self.theme.tokens))
        row.addWidget(self._seg)
        row.addStretch(1)

        self._community_control = CommunitySharingControl(
            self._community_enabled,
            test_mode=self.statistics.community_api.mode == "test",
        )
        self._community_control.toggle_requested.connect(self._set_community_sharing)
        self._community_control.details_requested.connect(self._show_community_popover)
        row.addWidget(self._community_control)
        self._community_popover = CommunitySharingPopover(self.theme.tokens, self)
        self._community_popover.toggle_requested.connect(self._set_community_sharing)
        self._community_popover.preview_requested.connect(self._preview_community_payload)
        self._community_popover.settings_requested.connect(self._open_community_status)
        self._community_popover.withdraw_requested.connect(self._withdraw_community)

        # Refresh affordance: spinning ring + "Refreshing…" (design §2), shown
        # only while an account refresh is running.
        self._refresh_spin = Spinner(self.theme.tokens["accent"], size=14)
        self._refresh_spin.setVisible(False)
        self._refresh_status = QLabel("Refreshing…")
        self._refresh_status.setObjectName("appSubtitle")
        self._refresh_status.setVisible(False)
        row.addWidget(self._refresh_spin)
        row.addWidget(self._refresh_status)
        row.addSpacing(6)
        self.accounts.refreshing.connect(self._on_refreshing)
        self.theme.changed.connect(
            lambda _n: self._refresh_spin.set_color(self.theme.tokens["accent"])
        )

        self._reload_btn = make_button("Reload", "ghost")
        self._reload_btn.clicked.connect(self._reload)
        self._refresh_btn = make_button("Refresh all", "ghost")
        self._refresh_btn.clicked.connect(self.accounts.refresh_all)
        self._auto_btn = QPushButton()
        self._auto_btn.setObjectName("autoToggle")
        self._auto_btn.setToolTip("Automatically refresh all accounts on a timer")
        self._auto_btn.setText(self._auto_label())
        self._auto_btn.setProperty("on", "true" if self._auto_on else "false")
        self._auto_btn.setCursor(Qt.PointingHandCursor)
        self._auto_btn.clicked.connect(self._toggle_auto)
        row.addWidget(self._reload_btn)
        row.addWidget(self._refresh_btn)
        row.addWidget(self._auto_btn)
        return header

    def _select_section(self, key: str) -> None:
        if key not in {"statistics", "accounts"}:
            key = "accounts"
        self._active = key
        self.stack.setCurrentIndex(0 if key == "accounts" else 1)
        self._seg.set_active(key)  # emit=False → no recursion back into this slot

    def _auto_label(self) -> str:
        return f"Auto Refresh · {'On' if self._auto_on else 'Off'}"

    def _toggle_auto(self) -> None:
        self._set_auto_refresh(not self._auto_on)

    def _set_auto_refresh(self, enabled: bool) -> None:
        self._auto_on = bool(enabled)
        self._auto_btn.setText(self._auto_label())
        self._auto_btn.setProperty("on", "true" if self._auto_on else "false")
        ThemeManager.repolish(self._auto_btn)
        if self._tray_controller is not None:
            self._tray_controller.set_auto_refresh(self._auto_on)
        self.settings["autoRefreshEnabled"] = self._auto_on
        data.save_settings(self.settings)
        if self._auto_on:
            self._next_auto_refresh = dt.datetime.now() + dt.timedelta(seconds=30)

    def _on_refreshing(self, active: bool) -> None:
        if active:
            self._refresh_spin.start()
            self._refresh_status.setVisible(True)
        else:
            self._refresh_spin.stop()
            self._refresh_status.setVisible(False)
        if self._tray_controller is not None:
            self._tray_controller.set_refreshing(active)

    def _reload(self) -> None:
        profiles = data.load_profiles()
        self.accounts.set_profiles(profiles)
        self.statistics.set_profiles(profiles)
        self._sync_tray_profiles(profiles)

    def _set_status(self, text: str) -> None:
        self._status_text = str(text)

    def _community_payload(self) -> tuple[dict | None, str]:
        try:
            return self.statistics.community_payload(), ""
        except CommunityApiError as exc:
            return None, str(exc)

    def _community_status_snapshot(self) -> dict:
        status = dict(self._community_last_receipt)
        status.setdefault("endpoint", self.statistics.community_api.endpoint)
        status.setdefault("networkRequest", self.statistics.community_api.mode != "test")
        status.setdefault(
            "installationId",
            str(getattr(self.statistics.community_api, "installation_id", "") or ""),
        )
        return status

    def _sync_community_popover(self) -> tuple[dict | None, str]:
        payload, error = self._community_payload()
        status = self._community_status_snapshot()
        self._community_popover.set_state(
            self._community_enabled,
            status,
            payload_available=payload is not None,
            can_withdraw=bool(status.get("installationId")),
            test_mode=self.statistics.community_api.mode == "test",
        )
        return payload, error

    def _show_community_popover(self) -> None:
        self._sync_community_popover()
        self._community_popover.show_for(self._community_control)

    def _preview_community_payload(self) -> None:
        payload, error = self._community_payload()
        if payload is None:
            QMessageBox.information(
                self,
                "Community sharing",
                error or "No aggregate model activity is available to preview yet.",
            )
            return
        CommunityPayloadDialog(
            payload,
            network_request=self.statistics.community_api.mode != "test",
            parent=self,
        ).exec()

    def _set_community_sharing(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._community_enabled:
            self._community_control.set_enabled(enabled)
            self._sync_community_popover()
            return
        if enabled:
            if not self.statistics.community_api.supports_submissions:
                self._community_control.set_enabled(False)
                self._sync_community_popover()
                QMessageBox.information(
                    self,
                    "Community sharing",
                    "Cloudflare staging is currently read-only. Signed installation uploads are not enabled yet.",
                )
                return
            payload, error = self._community_payload()
            dialog = CommunityConsentDialog(
                payload,
                error,
                test_mode=self.statistics.community_api.mode == "test",
                parent=self,
            )
            if not dialog.exec():
                self._community_control.set_enabled(False)
                self._sync_community_popover()
                return
            self._community_enabled = True
            self.settings["communitySharingEnabled"] = True
            self.settings["communityConsentVersion"] = CONSENT_VERSION
            self.settings["communityApiMode"] = self.statistics.community_api.mode
            data.save_settings(self.settings)
            self._community_control.set_enabled(True)
            self._sync_community_popover()
            self._upload_community("enabled")
            return
        self._community_enabled = False
        self.settings["communitySharingEnabled"] = False
        data.save_settings(self.settings)
        self._community_control.set_enabled(False)
        self._sync_community_popover()
        self._set_status("Community sharing turned off.")

    def _upload_community(self, reason: str = "automatic") -> None:
        if (
            not self._community_enabled
            or data.demo_data.DEMO
            or self._community_worker is not None
        ):
            return
        payload, error = self._community_payload()
        if payload is None:
            self._community_retry_after = dt.datetime.now() + dt.timedelta(hours=1)
            self._set_status(f"Community upload skipped: {error}")
            return
        self._community_worker_reason = reason
        worker = CommunityUploadWorker(
            self.statistics.community_api,
            payload=payload,
            parent=self,
        )
        self._community_worker = worker
        worker.succeeded.connect(self._community_upload_succeeded)
        worker.failed.connect(self._community_upload_failed)
        worker.finished.connect(lambda: self._community_worker_finished(worker))
        self._set_status("Signing and sending the anonymous community summary...")
        worker.start()

    def _community_upload_succeeded(self, receipt: dict) -> None:
        self._community_last_receipt = dict(receipt)
        self.settings["communityLastUploadUtc"] = str(receipt.get("acceptedAtUtc") or "")
        self.settings["communityLastReceipt"] = self._community_last_receipt
        self.settings["communityApiMode"] = self.statistics.community_api.mode
        data.save_settings(self.settings)
        self._sync_community_popover()
        duplicate = " Existing daily receipt reused." if receipt.get("duplicate") else ""
        publication = str(receipt.get("publicationSource") or "")
        publication_note = (
            " Collecting until the public privacy threshold is met."
            if publication == "real-pending"
            else " Public aggregates were refreshed."
            if publication == "real-community"
            else ""
        )
        self._set_status(
            f"Community summary accepted ({receipt.get('recordCount', 0)} model records)."
            f"{duplicate}{publication_note}"
        )

    def _community_upload_failed(self, message: str) -> None:
        self._community_retry_after = dt.datetime.now() + dt.timedelta(hours=1)
        self._set_status(f"Community Worker rejected the daily summary: {message}")
        if self._community_worker_reason == "enabled":
            QMessageBox.warning(
                self,
                "Community sharing",
                "Sharing remains enabled and will retry later.\n\n" + message,
            )

    def _community_worker_finished(self, worker: CommunityUploadWorker) -> None:
        if self._community_worker is worker:
            self._community_worker = None
            self._community_worker_reason = ""
        worker.deleteLater()

    def _open_community_status(self) -> None:
        payload, _error = self._community_payload()
        status = self._community_status_snapshot()
        dialog = CommunityStatusDialog(
            self._community_enabled,
            status,
            payload,
            self,
        )
        dialog.exec()
        if dialog.withdraw_requested:
            self._withdraw_community()

    def _withdraw_community(self) -> None:
        if self._community_worker is not None:
            QMessageBox.information(
                self,
                "Community sharing",
                "A community request is already in progress. Please try again shortly.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Withdraw community data",
            "Delete this installation's accepted raw Community submissions and local signing identity?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        worker = CommunityUploadWorker(
            self.statistics.community_api,
            withdraw=True,
            parent=self,
        )
        self._community_worker = worker
        self._community_worker_reason = "withdraw"
        worker.succeeded.connect(self._community_withdraw_succeeded)
        worker.failed.connect(self._community_withdraw_failed)
        worker.finished.connect(lambda: self._community_worker_finished(worker))
        self._set_status("Withdrawing this installation's community data...")
        worker.start()

    def _community_withdraw_succeeded(self, result: dict) -> None:
        self._community_enabled = False
        self._community_last_receipt = {}
        self.settings["communitySharingEnabled"] = False
        self.settings["communityLastUploadUtc"] = ""
        self.settings["communityLastReceipt"] = {}
        data.save_settings(self.settings)
        self._community_control.set_enabled(False)
        self._sync_community_popover()
        deleted = int(result.get("deletedSubmissions") or 0)
        self._set_status(f"Community data withdrawn ({deleted} raw submission(s) deleted).")
        QMessageBox.information(
            self,
            "Community sharing",
            f"Withdrawal completed. {deleted} accepted raw submission(s) were deleted.",
        )

    def _community_withdraw_failed(self, message: str) -> None:
        self._set_status(f"Community withdrawal failed: {message}")
        QMessageBox.warning(self, "Community sharing", "Withdrawal failed.\n\n" + message)

    def _community_upload_due(self) -> bool:
        if (
            not self._community_enabled
            or self._community_worker is not None
            or dt.datetime.now() < self._community_retry_after
        ):
            return False
        raw = str(self.settings.get("communityLastUploadUtc") or "")
        try:
            last_day = dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            return True
        return last_day < dt.datetime.now().date()

    def _community_history_ready(self) -> None:
        """Retry a due summary once local analytics has finished loading."""

        if not self._community_enabled:
            return
        self._community_retry_after = dt.datetime.min
        if self._community_upload_due():
            self._upload_community("history-ready")

    def _tick(self) -> None:
        self.accounts.tick()
        if self._tray_controller is not None:
            self._tray_controller.tick()
        # A rollover verification is already in progress and must finish even
        # when general Auto Refresh is disabled.
        rollover_started = self.accounts.refresh_due_rollovers()
        if self._auto_on and dt.datetime.now() >= self._next_auto_refresh:
            self._next_auto_refresh = dt.datetime.now() + dt.timedelta(minutes=self._auto_minutes)
            if not rollover_started:
                self.accounts.refresh_all(reason="auto")
        if self._community_upload_due():
            self._upload_community("automatic")

    def closeEvent(self, event) -> None:
        self._clock.stop()
        self.accounts.close_workers()
        self.statistics.close_worker()
        if self._community_worker is not None and self._community_worker.isRunning():
            self._community_worker.wait(12000)
        if self._tray_controller is not None:
            self._tray_controller.close()
        super().closeEvent(event)
