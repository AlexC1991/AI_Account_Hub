"""Compact Best Next popup shown from the AI Account Hub system tray."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ai_account_hub import core as L
from ai_account_hub import data
from ai_account_hub.ui.tokens import severity_color
from ai_account_hub.ui.widgets import (
    Avatar,
    ElidedLabel,
    NetworkLogo,
    SeverityBar,
    StatusPill,
    make_button,
    network_icon,
)


def remaining_capacity(profile: dict) -> tuple[float | None, float | None, float]:
    """Return weekly, session and conservative usable capacity percentages."""

    weekly = data.percent_left(profile.get("weeklyLimitUsedPercent"))
    session = data.percent_left(profile.get("shortLimitUsedPercent"))
    known = [value for value in (weekly, session) if value is not None]
    if len(known) == 2:
        usable = min(known)
    elif known:
        usable = known[0]
    else:
        # Providers such as Cursor expose readiness but no comparable quota.
        # Keep them behind measured ready accounts without inventing a value.
        usable = -1.0
    return weekly, session, usable


def _in_use_set(value: str | set[str] | None) -> set[str]:
    if isinstance(value, set):
        return {str(item) for item in value if str(item)}
    return {str(value)} if value else set()


def rank_profiles(profiles: list[dict], in_use_id: str | set[str] = "") -> list[dict]:
    """Rank usable accounts first, then choose the healthiest remaining quota."""

    state_rank = {"ready": 4, "idle": 3, "not_ready": 2, "login": 1, "error": 0}
    in_use_ids = _in_use_set(in_use_id)
    ordered = sorted(
        (profile for profile in profiles if isinstance(profile, dict)),
        key=lambda profile: str(profile.get("name") or "").lower(),
    )

    def score(profile: dict) -> tuple[int, int, float, float, float]:
        state = data.account_state(profile)
        weekly, session, usable = remaining_capacity(profile)
        available_next = int(state == "ready" and data.profile_id(profile) not in in_use_ids)
        return (
            state_rank.get(state, -1),
            available_next,
            usable,
            weekly if weekly is not None else -1.0,
            session if session is not None else -1.0,
        )

    return sorted(ordered, key=score, reverse=True)


def _status(profile: dict, in_use_id: str | set[str]) -> tuple[str, str]:
    state = data.account_state(profile)
    if state == "ready" and data.profile_id(profile) in _in_use_set(in_use_id):
        return "In use", "inuse"
    label = L.status_label(state)
    if state == "not_ready":
        countdown = _compact_countdown(L.ready_countdown(profile))
        if countdown:
            label = f"{label} {countdown}"
    return label, data.STATE_PILL.get(state, "idle")


def _compact_countdown(value: str) -> str:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        hours, minutes = int(parts[0]), int(parts[1])
        return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
    return text


def _left_text(value: float | None) -> str:
    return "Not exposed" if value is None else f"{value:.0f}%"


class _TrayAccountRow(QPushButton):
    selected = Signal(str)

    def __init__(self, profile: dict, in_use_id: str | set[str]) -> None:
        super().__init__()
        self.profile = profile
        self.pid = data.profile_id(profile)
        self.setObjectName("trayAccountRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(46)

        row = QHBoxLayout(self)
        row.setContentsMargins(9, 4, 9, 4)
        row.setSpacing(8)

        row.addWidget(
            Avatar(
                data.provider_color(profile),
                data.provider_monogram(profile),
                size=26,
                radius=7,
                icon_path=data.provider_icon_path(profile),
            )
        )

        copy = QVBoxLayout()
        copy.setSpacing(1)
        name = ElidedLabel(str(profile.get("name") or "Account"))
        name.setObjectName("trayAccountName")
        weekly, session, _usable = remaining_capacity(profile)
        if weekly is None and session is None:
            detail = f"{data.provider_label(profile)} | {data.account_plan(profile)}"
        else:
            detail = f"Week {_left_text(weekly)} | Session {_left_text(session)}"
        sub = ElidedLabel(detail)
        sub.setObjectName("faint")
        copy.addWidget(name)
        copy.addWidget(sub)
        row.addLayout(copy, 1)

        status_text, status_kind = _status(profile, in_use_id)
        pill = StatusPill(status_text, status_kind)
        pill.setMaximumWidth(112)
        row.addWidget(pill, 0, Qt.AlignVCenter)
        self.clicked.connect(lambda _checked=False: self.selected.emit(self.pid))


class BestNextTrayPopup(QWidget):
    """Small theme-aware account chooser anchored to the Windows tray."""

    refresh_requested = Signal()
    dashboard_requested = Signal()
    switch_requested = Signal(str)

    def __init__(self, theme_manager) -> None:
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("trayPopup")
        self.setFixedWidth(360)
        self.setMaximumHeight(370)
        self._tm = theme_manager
        self._profiles: list[dict] = []
        self._ranked: list[dict] = []
        self._selected_id = ""
        self._in_use_ids: set[str] = set()
        self._refreshing = False
        self._render_signature: tuple = ()
        self._next_rows: list[_TrayAccountRow] = []
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("trayHeader")
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(12, 7, 10, 7)
        header_row.setSpacing(9)
        self.logo = NetworkLogo(self._tm.tokens["accent"], size=24)
        header_row.addWidget(self.logo)

        header_copy = QVBoxLayout()
        header_copy.setSpacing(0)
        title = QLabel("Best account now")
        title.setObjectName("trayTitle")
        self.summary = QLabel("No accounts loaded")
        self.summary.setObjectName("faint")
        header_copy.addWidget(title)
        header_copy.addWidget(self.summary)
        header_row.addLayout(header_copy, 1)

        self.refresh_button = make_button("Refresh", "ghost")
        self.refresh_button.setFixedHeight(28)
        self.refresh_button.clicked.connect(lambda _checked=False: self.refresh_requested.emit())
        header_row.addWidget(self.refresh_button)
        outer.addWidget(header)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(8, 8, 8, 8)
        body_layout.setSpacing(6)

        self.hero = QFrame()
        self.hero.setObjectName("card")
        self.hero.setProperty("selected", "true")
        hero_layout = QVBoxLayout(self.hero)
        hero_layout.setContentsMargins(9, 8, 9, 8)
        hero_layout.setSpacing(5)

        identity = QHBoxLayout()
        identity.setSpacing(9)
        self.hero_avatar = Avatar(
            data.provider_color({"provider": "codex"}),
            data.provider_monogram({"provider": "codex"}),
            size=30,
            radius=8,
        )
        identity.addWidget(self.hero_avatar)
        hero_copy = QVBoxLayout()
        hero_copy.setSpacing(1)
        self.hero_name = ElidedLabel("No accounts yet")
        self.hero_name.setObjectName("trayHeroName")
        self.hero_sub = ElidedLabel("Add an account from the dashboard")
        self.hero_sub.setObjectName("faint")
        hero_copy.addWidget(self.hero_name)
        hero_copy.addWidget(self.hero_sub)
        identity.addLayout(hero_copy, 1)
        self.hero_status = StatusPill("Idle", "idle")
        self.hero_status.setMaximumWidth(112)
        identity.addWidget(self.hero_status, 0, Qt.AlignTop)
        hero_layout.addLayout(identity)

        self.weekly_label, self.weekly_value, self.weekly_bar = self._metric("Weekly left")
        hero_layout.addWidget(self.weekly_label)
        hero_layout.addWidget(self.weekly_bar)
        self.session_label, self.session_value, self.session_bar = self._metric("5h session left")
        hero_layout.addWidget(self.session_label)
        hero_layout.addWidget(self.session_bar)

        self.switch_button = make_button("Switch & open", "primary")
        self.switch_button.setFixedHeight(28)
        self.switch_button.clicked.connect(self._emit_switch)
        hero_layout.addWidget(self.switch_button)
        body_layout.addWidget(self.hero)

        self.next_label = QLabel("NEXT OPTIONS")
        self.next_label.setObjectName("sectionLabel")
        body_layout.addWidget(self.next_label)
        self.next_host = QWidget()
        self.next_layout = QVBoxLayout(self.next_host)
        self.next_layout.setContentsMargins(0, 0, 0, 0)
        self.next_layout.setSpacing(4)
        body_layout.addWidget(self.next_host)
        outer.addWidget(body)

        footer = QFrame()
        footer.setObjectName("trayFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(8, 5, 8, 5)
        footer_layout.setSpacing(7)
        footer_layout.addStretch(1)
        dashboard = make_button("Open dashboard", "ghost")
        dashboard.setFixedHeight(28)
        dashboard.clicked.connect(lambda _checked=False: self.dashboard_requested.emit())
        footer_layout.addWidget(dashboard)
        outer.addWidget(footer)

    def _metric(self, caption: str) -> tuple[QWidget, QLabel, SeverityBar]:
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        label = QLabel(caption.upper())
        label.setObjectName("faint")
        value = QLabel("-")
        value.setObjectName("trayMetricValue")
        row.addWidget(label)
        row.addStretch(1)
        row.addWidget(value)
        bar = SeverityBar(self._tm.tokens, height=5)
        return host, value, bar

    def set_profiles(self, profiles: list[dict], in_use_id: str | set[str] = "") -> None:
        self._profiles = list(profiles)
        self._in_use_ids = _in_use_set(in_use_id)
        self._ranked = rank_profiles(self._profiles, self._in_use_ids)
        available_ids = {data.profile_id(profile) for profile in self._ranked}
        if self._selected_id not in available_ids:
            self._selected_id = data.profile_id(self._ranked[0]) if self._ranked else ""
        self._render()

    def select_best(self) -> None:
        self._ranked = rank_profiles(self._profiles, self._in_use_ids)
        self._selected_id = data.profile_id(self._ranked[0]) if self._ranked else ""
        self._render()

    def set_refreshing(self, active: bool) -> None:
        self._refreshing = bool(active)
        self.refresh_button.setEnabled(not active)
        self._update_summary()

    def set_theme(self, tokens: dict[str, str]) -> None:
        self.logo.set_accent(tokens["accent"])
        self.weekly_bar.set_theme(tokens)
        self.session_bar.set_theme(tokens)
        self._render()

    def tick(self) -> None:
        if self.isVisible() and self._profile_signature() != self._render_signature:
            self._ranked = rank_profiles(self._profiles, self._in_use_ids)
            self._render()

    def _selected_profile(self) -> dict | None:
        return next(
            (profile for profile in self._profiles if data.profile_id(profile) == self._selected_id),
            None,
        )

    def _select(self, pid: str) -> None:
        self._selected_id = pid
        self._render()

    def _render(self) -> None:
        self._update_summary()
        profile = self._selected_profile()
        if profile is None:
            self.hero_name.setText("No accounts yet")
            self.hero_sub.setText("Add an account from the dashboard")
            self.hero_status.setText("Idle")
            self.hero_status.set_kind("idle")
            self.weekly_value.setText("-")
            self.session_value.setText("-")
            self.weekly_bar.set_percent_left(None)
            self.session_bar.set_percent_left(None)
            self.switch_button.setEnabled(False)
        else:
            self.hero_avatar.set_identity(
                data.provider_color(profile),
                data.provider_monogram(profile),
                data.provider_icon_path(profile),
            )
            self.hero_name.setText(str(profile.get("name") or "Account"))
            self.hero_sub.setText(f"{data.provider_label(profile)} | {data.account_plan(profile)}")
            status_text, status_kind = _status(profile, self._in_use_ids)
            self.hero_status.setText(status_text)
            self.hero_status.set_kind(status_kind)

            weekly, session, _usable = remaining_capacity(profile)
            self._set_metric(self.weekly_value, self.weekly_bar, weekly)
            self._set_metric(self.session_value, self.session_bar, session)
            ready = data.account_state(profile) == "ready"
            self.switch_button.setEnabled(ready)
            self.switch_button.setText("Switch & open" if ready else "Unavailable")

        self._rebuild_next_rows()
        self._render_signature = self._profile_signature()
        self.adjustSize()

    def _profile_signature(self) -> tuple:
        return tuple(
            (
                data.profile_id(profile),
                data.account_state(profile),
                _status(profile, self._in_use_ids)[0],
                str(profile.get("weeklyLimitUsedPercent") or ""),
                str(profile.get("shortLimitUsedPercent") or ""),
                data.profile_id(profile) in self._in_use_ids,
            )
            for profile in self._profiles
        )

    def _set_metric(self, label: QLabel, bar: SeverityBar, value: float | None) -> None:
        label.setText(_left_text(value))
        label.setStyleSheet(
            f"color:{severity_color(self._tm.tokens, value)};font-size:11px;font-weight:700;"
        )
        bar.set_percent_left(value)

    def _update_summary(self) -> None:
        if self._refreshing:
            self.summary.setText("Refreshing account limits...")
            return
        total = len(self._profiles)
        ready = sum(1 for profile in self._profiles if data.account_state(profile) == "ready")
        self.summary.setText(f"{ready} ready | {total} account{'s' if total != 1 else ''}")

    def _rebuild_next_rows(self) -> None:
        while self.next_layout.count():
            item = self.next_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._next_rows.clear()

        alternatives = [
            profile for profile in self._ranked
            if data.profile_id(profile) != self._selected_id
        ][:2]
        self.next_label.setVisible(bool(alternatives))
        self.next_host.setVisible(bool(alternatives))
        for profile in alternatives:
            row = _TrayAccountRow(profile, self._in_use_ids)
            row.selected.connect(self._select)
            self._next_rows.append(row)
            self.next_layout.addWidget(row)

    def _emit_switch(self) -> None:
        if self._selected_id:
            self.switch_requested.emit(self._selected_id)


class TrayController(QObject):
    """Own the native tray icon, menu, popup placement and activation flow."""

    restore_requested = Signal()
    refresh_requested = Signal()
    switch_requested = Signal(str)
    auto_refresh_requested = Signal(bool)
    exit_requested = Signal()
    popup_opening = Signal()

    def __init__(self, parent: QWidget, theme_manager, auto_refresh: bool) -> None:
        super().__init__(parent)
        self._parent = parent
        self._tm = theme_manager
        self.tray: QSystemTrayIcon | None = None
        self.menu: QMenu | None = None
        self.popup: BestNextTrayPopup | None = None
        self.auto_action = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._build(bool(auto_refresh))

    @property
    def available(self) -> bool:
        return self.tray is not None and self.tray.isVisible()

    def _build(self, auto_refresh: bool) -> None:
        self.popup = BestNextTrayPopup(self._tm)
        self.popup.dashboard_requested.connect(lambda: self.restore_requested.emit())
        self.popup.refresh_requested.connect(lambda: self.refresh_requested.emit())
        self.popup.switch_requested.connect(lambda pid: self.switch_requested.emit(pid))

        icon = network_icon(self._tm.tokens["accent"])
        self.tray = QSystemTrayIcon(icon, self._parent)
        self.tray.setToolTip("AI Account Hub")
        self.tray.activated.connect(self._activated)

        self.menu = QMenu(self._parent)
        open_action = self.menu.addAction("Open AI Account Hub")
        open_action.triggered.connect(lambda _checked=False: self.restore_requested.emit())
        best_action = self.menu.addAction("Show Best Next")
        best_action.triggered.connect(lambda _checked=False: self.show_popup())
        self.menu.addSeparator()
        refresh_action = self.menu.addAction("Refresh all accounts")
        refresh_action.triggered.connect(lambda _checked=False: self.refresh_requested.emit())
        self.auto_action = self.menu.addAction("Auto-refresh")
        self.auto_action.setCheckable(True)
        self.auto_action.setChecked(auto_refresh)
        self.auto_action.triggered.connect(
            lambda checked=False: self.auto_refresh_requested.emit(bool(checked))
        )
        self.menu.addSeparator()
        exit_action = self.menu.addAction("Exit AI Account Hub")
        exit_action.triggered.connect(lambda _checked=False: self.exit_requested.emit())
        self.tray.setContextMenu(self.menu)
        self.tray.show()

    def set_profiles(self, profiles: list[dict], in_use_ids: set[str]) -> None:
        ready = sum(1 for profile in profiles if data.account_state(profile) == "ready")
        if self.tray is not None:
            self.tray.setToolTip(f"AI Account Hub - {ready}/{len(profiles)} ready")
        if self.popup is not None:
            self.popup.set_profiles(profiles, in_use_ids)

    def set_theme(self, tokens: dict[str, str]) -> None:
        icon = network_icon(tokens["accent"])
        if self.tray is not None:
            self.tray.setIcon(icon)
        if self.popup is not None:
            self.popup.set_theme(tokens)

    def set_refreshing(self, active: bool) -> None:
        if self.popup is not None:
            self.popup.set_refreshing(active)

    def set_auto_refresh(self, enabled: bool) -> None:
        if self.auto_action is not None:
            self.auto_action.setChecked(bool(enabled))

    def tick(self) -> None:
        if self.popup is not None:
            self.popup.tick()

    def toggle_popup(self) -> None:
        if self.popup is None:
            return
        if self.popup.isVisible():
            self.popup.hide()
        else:
            self.show_popup()

    def show_popup(self) -> None:
        if self.popup is None:
            return
        self.popup_opening.emit()
        self.popup.select_best()
        self.popup.adjustSize()
        self._position_popup()
        self.popup.show()
        self.popup.raise_()
        self.popup.activateWindow()

    def hide_popup(self) -> None:
        if self.popup is not None:
            self.popup.hide()

    def minimize(self, window: QWidget) -> bool:
        if not self.available:
            return False
        self.hide_popup()
        window.hide()
        return True

    def close(self) -> None:
        if self.popup is not None:
            self.popup.close()
        if self.tray is not None:
            self.tray.hide()

    def _activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_popup()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.restore_requested.emit()

    def _position_popup(self) -> None:
        if self.popup is None:
            return
        tray_rect = self.tray.geometry() if self.tray is not None else None
        screen = QApplication.screenAt(tray_rect.center()) if tray_rect and tray_rect.isValid() else None
        screen = screen or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        screen_rect = screen.geometry()
        width = self.popup.width()
        height = self.popup.height()
        right_side = not tray_rect or not tray_rect.isValid() or tray_rect.center().x() >= screen_rect.center().x()
        bottom_side = not tray_rect or not tray_rect.isValid() or tray_rect.center().y() >= screen_rect.center().y()
        x = available.right() - width - 8 if right_side else available.left() + 8
        y = available.bottom() - height - 8 if bottom_side else available.top() + 8
        x = max(available.left() + 4, min(x, available.right() - width - 4))
        y = max(available.top() + 4, min(y, available.bottom() - height - 4))
        self.popup.move(x, y)
