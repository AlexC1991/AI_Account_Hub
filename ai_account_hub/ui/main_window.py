"""Frameless main window: title bar + persistent app header + stacked screens.

Screen switching is a QStackedWidget.setCurrentIndex() — both screens stay
alive and keep their state; nothing is torn down and rebuilt when you move
between Coding and Accounts (the core flow requirement).
"""

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
from ai_account_hub.ui.tray_widget import TrayController


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

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # The Accounts dashboard is the single screen. (The native Coding
        # workbench is parked out of the shipped tree until it is finished.)
        self.stack = QStackedWidget()
        self.accounts = AccountsScreen(self.theme)
        self.stack.addWidget(self.accounts)   # index 0

        self.menu_bar = self._build_menu_bar()
        self.titlebar = TitleBar(self, self.theme.tokens["accent"], self.menu_bar)
        root.addWidget(self.titlebar)
        root.addWidget(self._build_header())
        root.addWidget(self.stack, 1)

        profiles = data.load_profiles()
        self.accounts.set_profiles(profiles)
        self._setup_system_tray()
        self._sync_tray_profiles(profiles)

        self.theme.changed.connect(lambda _n: self.titlebar.set_accent(self.theme.tokens["accent"]))
        self.theme.changed.connect(lambda _n: self._logo.set_accent(self.theme.tokens["accent"]))
        self.theme.changed.connect(lambda _n: self._sync_tray_theme())
        self.accounts.activity.connect(self._set_status)
        self.accounts.profiles_changed.connect(self._sync_tray_profiles)
        self._select_section("accounts")
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
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)

        edit_menu = bar.addMenu("Edit")
        self._menus.append(edit_menu)
        edit_menu.addAction("Add account", lambda: self._account_action("add"))
        edit_menu.addAction("Edit selected", lambda: self._account_action("edit"))
        edit_menu.addAction("Rename selected", lambda: self._account_action("rename"))
        edit_menu.addAction("Delete selected", lambda: self._account_action("delete"))

        window_menu = bar.addMenu("Window")
        self._menus.append(window_menu)
        coding_action = window_menu.addAction("Coding (in development)", lambda: None)
        coding_action.setEnabled(False)  # Coding view isn't ready yet
        window_menu.addAction("Accounts", lambda: self._select_section("accounts"))
        window_menu.addSeparator()
        window_menu.addAction("Minimize", self.showMinimized)
        window_menu.addAction("Show Best Next", self._show_best_next)
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
        self._tray_controller = TrayController(self, self.theme, self._auto_on)
        self._tray_controller.restore_requested.connect(self._restore_from_tray)
        self._tray_controller.refresh_requested.connect(self._refresh_from_tray)
        self._tray_controller.switch_requested.connect(self._switch_from_tray)
        self._tray_controller.auto_refresh_requested.connect(self._set_auto_refresh)
        self._tray_controller.exit_requested.connect(self.close)
        self._tray_controller.popup_opening.connect(self._sync_tray_profiles)

    def _sync_tray_theme(self) -> None:
        icon = network_icon(self.theme.tokens["accent"])
        self.setWindowIcon(icon)
        if self._tray_controller is not None:
            self._tray_controller.set_theme(self.theme.tokens)

    def _active_profile_ids(self, profiles: list[dict]) -> set[str]:
        active_ids: set[str] = set()
        coding_pid = str(getattr(self.accounts, "_coding_active_pid", "") or "")
        if coding_pid:
            active_ids.add(coding_pid)

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
        if self._tray_controller is not None:
            self._tray_controller.set_profiles(current, self._active_profile_ids(current))

    def _show_best_next(self) -> None:
        if self._tray_controller is not None:
            self._tray_controller.show_popup()

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

        # segmented Coding | Accounts — flush slider with an animated thumb. The
        # Coding view still needs a lot of work, so it is greyed out and inert
        # for now; Accounts is the only usable section.
        self._seg = SegmentedSlider([("Coding", "coding"), ("Accounts", "accounts")], self.theme.tokens)
        self._seg.changed.connect(self._select_section)
        self._seg.set_disabled({"coding"}, "Coding view is still in development")
        self.theme.changed.connect(lambda _n: self._seg.set_theme(self.theme.tokens))
        self.theme.changed.connect(lambda _n: self._seg.set_disabled({"coding"}, "Coding view is still in development"))
        row.addWidget(self._seg)
        row.addStretch(1)

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
        # Coding is disabled while it's under construction — always fall back to
        # Accounts so nothing can navigate into the half-finished coding view.
        if key == "coding":
            key = "accounts"
        self._active = key
        # instant, stateful switch — no rebuild (Accounts is the only screen)
        self.stack.setCurrentIndex(0)
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
        self._sync_tray_profiles(profiles)

    def _set_status(self, text: str) -> None:
        self._status_text = str(text)

    def _tick(self) -> None:
        self.accounts.tick()
        if self._tray_controller is not None:
            self._tray_controller.tick()
        if self._auto_on and dt.datetime.now() >= self._next_auto_refresh:
            self._next_auto_refresh = dt.datetime.now() + dt.timedelta(minutes=self._auto_minutes)
            self.accounts.refresh_all(reason="auto")

    def closeEvent(self, event) -> None:
        self._clock.stop()
        self.accounts.close_workers()
        if self._tray_controller is not None:
            self._tray_controller.close()
        super().closeEvent(event)
