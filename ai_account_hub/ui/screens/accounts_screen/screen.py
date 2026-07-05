"""Accounts dashboard screen: the persistent 3-column shell + construction. The
data-update and action-dispatch method groups live in the data/actions mixins."""

from __future__ import annotations

import datetime as _dt
import json

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QScrollArea, QVBoxLayout, QWidget,
)

from ai_account_hub import data
from ai_account_hub import core as L
from ai_account_hub.ui.widgets import Avatar, SeverityBar, StatusPill, make_button

from ai_account_hub.ui.screens.accounts_screen.workers import ActionWorker, RefreshWorker
from ai_account_hub.ui.screens.accounts_screen.card import AccountCard, _label

from ai_account_hub.ui.screens.accounts_screen.data import _DataMixin
from ai_account_hub.ui.screens.accounts_screen.actions import _ActionsMixin


class AccountsScreen(_DataMixin, _ActionsMixin, QWidget):
    use_in_coding_requested = Signal(str)  # profile id
    profiles_changed = Signal(list)
    activity = Signal(str)
    refreshing = Signal(bool)  # True when a Refresh-all run starts, False when it ends

    def __init__(self, theme_manager) -> None:
        super().__init__()
        self._tm = theme_manager
        self._action_worker: ActionWorker | None = None
        self._profiles: list[dict] = []
        self._cards: dict[str, AccountCard] = {}
        self._selected: str | None = None
        self._coding_active_pid: str | None = None
        self._worker: RefreshWorker | None = None
        self._log_lines: list[str] = []
        self._settings = data.load_settings()
        self._card_template = str(self._settings.get("cardTemplate") or "Balanced")
        self._selected_day = _dt.date.today().isoformat()
        self._columns: list[QWidget] = []
        self._body: QWidget | None = None
        self._desktop_capture_pid: str | None = None
        self._desktop_capture_deadline: _dt.datetime | None = None
        self._desktop_capture_started_at: _dt.datetime | None = None
        self._desktop_capture_last_state = ""
        self._desktop_capture_timer = QTimer(self)
        self._desktop_capture_timer.setInterval(2500)
        self._desktop_capture_timer.timeout.connect(self._poll_desktop_login_capture)
        self._build()
        self._tm.changed.connect(lambda _n: self.apply_theme())

    # ---------- layout (built once) ----------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QWidget()
        self._body = body
        body.setStyleSheet(f"background:{self._tm.tokens['border']};")
        grid = QHBoxLayout(body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(1)  # 1px hairline gap = column dividers via bg (design 3)
        grid.addWidget(self._build_left(), 0)
        grid.addWidget(self._build_center(), 1)
        grid.addWidget(self._build_right(), 0)
        outer.addWidget(body, 1)

    def _desktop_active_name(self) -> str:
        """Name of the account currently active in Codex Desktop, or ''."""
        try:
            path = L.DESKTOP_ACTIVE_PROFILE_PATH
            if path.exists():
                info = json.loads(path.read_text(encoding="utf-8"))
                return str(info.get("name") or info.get("profileName") or "").strip()
        except Exception:
            pass
        return ""

    def set_coding_active(self, pid: str) -> None:
        """Track which account is active in the Coding workbench (any provider)."""
        pid = pid or None
        if pid == self._coding_active_pid:
            return
        self._coding_active_pid = pid
        self._apply_desktop_active()

    def _apply_desktop_active(self) -> None:
        """Mark exactly one 'In use' account. The account loaded in the Coding
        workbench is the authoritative "account you're using" (works for every
        provider incl. Claude/Cursor/Antigravity). Only if the workbench has no
        active account do we fall back to the Codex Desktop marker."""
        coding_pid = self._coding_active_pid
        active = self._desktop_active_name()
        for card in self._cards.values():
            if coding_pid:
                in_use = card.pid == coding_pid
            else:
                in_use = (
                    bool(active)
                    and data.provider_key(card.profile) == "codex"
                    and str(card.profile.get("name", "")).strip() == active
                )
            card.set_in_use(in_use)

    def _column(self, width: int | None = None) -> tuple[QWidget, QVBoxLayout]:
        col = QWidget()
        self._columns.append(col)
        col.setStyleSheet(f"background:{self._tm.tokens['bg']};")
        if width:
            col.setFixedWidth(width)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)
        return col, lay

    def _build_left(self) -> QWidget:
        col, lay = self._column(312)

        head = QHBoxLayout()
        head.addWidget(_label("Profiles", bold=True, size=13))
        head.addStretch(1)
        self.total_label = _label("Total 0", "faint")
        head.addWidget(self.total_label)
        lay.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search profiles")
        self.search.textChanged.connect(self._apply_filter)
        lay.addWidget(self.search)

        # Sort row: field selector + a compact interactive order icon
        # (↑ Low→high / ↓ High→low). Matches the reference's Sort|Order pair,
        # with consistent 30px control heights so the column reads cleanly.
        sorts = QHBoxLayout()
        sorts.setSpacing(8)
        self.sort_by = QComboBox()
        self.sort_by.addItems(list(L.SORT_CHOICES))
        configured_sort = str(self._settings.get("sortMode") or "Manual")
        if configured_sort in L.SORT_CHOICES:
            self.sort_by.setCurrentText(configured_sort)
        self.sort_by.setFixedHeight(30)
        saved_direction = self._settings.get("sortDescending")
        self._sort_desc = (
            bool(saved_direction)
            if isinstance(saved_direction, bool)
            else configured_sort in {"Session left", "Weekly left", "Last refresh"}
        )
        self.sort_by.currentIndexChanged.connect(self._sort_mode_changed)
        self.sort_dir_btn = make_button("↑", "ghost")
        self.sort_dir_btn.setFixedSize(40, 30)
        self.sort_dir_btn.clicked.connect(self._toggle_sort_dir)
        self._update_sort_dir_icon()
        sorts.addWidget(self.sort_by, 1)
        sorts.addWidget(self.sort_dir_btn)
        lay.addLayout(sorts)

        views = QHBoxLayout()
        views.setSpacing(8)
        vlab = _label("View", "faint")
        vlab.setFixedWidth(34)
        views.addWidget(vlab)
        self.card_view = QComboBox()
        self.card_view.addItems(list(L.CARD_TEMPLATE_CHOICES))
        self.card_view.setCurrentText(self._card_template)
        self.card_view.currentIndexChanged.connect(self._change_card_template)
        self.card_view.setFixedHeight(30)
        views.addWidget(self.card_view, 1)
        lay.addLayout(views)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        for text, variant, key in (("+ Add", "primary", "add"), ("Edit", "ghost", "edit"),
                                    ("Rename", "ghost", "rename"), ("Delete", "danger", "delete")):
            btn = make_button(text, variant)
            btn.setFixedHeight(30)
            btn.clicked.connect(lambda _c=False, k=key: self._run_action(k))
            actions.addWidget(btn)
        lay.addLayout(actions)

        summary = QFrame()
        summary.setObjectName("card")
        summary.setProperty("selected", "true")
        summary.setCursor(Qt.PointingHandCursor)
        summary.mousePressEvent = lambda event: self.select_all() if event.button() == Qt.LeftButton else None
        self.summary_card = summary
        sl = QVBoxLayout(summary)
        sl.setContentsMargins(12, 10, 12, 10)
        row = QHBoxLayout()
        row.addWidget(_label("All visible accounts", bold=True))
        row.addStretch(1)
        self.ready_pill = StatusPill("0/0 ready", "ready")
        row.addWidget(self.ready_pill)
        sl.addLayout(row)
        self.summary_sub = _label("0 ready · 0 not ready", "faint")
        sl.addWidget(self.summary_sub)
        self.summary_bar = SeverityBar(self._tm.tokens)
        sl.addWidget(self.summary_bar)
        lay.addWidget(summary)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        # Leave breathing room for the intentionally slim overlay scrollbar.
        self._list_layout.setContentsMargins(0, 0, 12, 0)
        self._list_layout.setSpacing(8)
        self._list_layout.addStretch(1)
        scroll.setWidget(self._list_host)
        lay.addWidget(scroll, 1)
        return col

    def _build_center(self) -> QWidget:
        col, lay = self._column()
        col.setMinimumWidth(470)

        stats = QHBoxLayout()
        stats.setSpacing(10)
        self.stat_cards: dict[str, QLabel] = {}
        self.stat_captions: dict[str, QLabel] = {}
        for key, title, caption in (
            ("day_tokens", "Month tokens", "This month"),
            ("day_active", "Month active", "This month"),
            ("pool_tokens", "Pool tokens", "All recorded history"),
            ("reset_markers", "Reset markers", "This month"),
        ):
            card = QFrame()
            card.setObjectName("card")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(14, 12, 14, 12)
            cl.addWidget(_label(title, "faint"))
            big = QLabel("—")
            big.setObjectName("bigNumber")
            self.stat_cards[key] = big
            cl.addWidget(big)
            cap = _label(caption, "faint")
            self.stat_captions[key] = cap
            cl.addWidget(cap)
            stats.addWidget(card)
        lay.addLayout(stats)

        from ai_account_hub.ui.calendar_widget import CalendarWidget
        self.calendar = CalendarWidget(self._tm)
        self.calendar.day_clicked.connect(self._show_day_detail)
        lay.addWidget(self.calendar, 1)
        return col

    def _build_right(self) -> QWidget:
        col, lay = self._column(340)

        head = QHBoxLayout()
        head.setSpacing(9)
        self.detail_avatar = Avatar(self._tm.tokens["text3"], "—")
        head.addWidget(self.detail_avatar, 0, Qt.AlignTop)
        idbox = QVBoxLayout()
        idbox.setSpacing(2)
        self.detail_name = _label("Select an account", bold=True, size=14)
        self.detail_sub = _label("", "faint")
        idbox.addWidget(self.detail_name)
        idbox.addWidget(self.detail_sub)
        head.addLayout(idbox, 1)
        self.detail_pill = StatusPill("", "idle")
        head.addWidget(self.detail_pill, 0, Qt.AlignTop)
        lay.addLayout(head)

        # 2x2 stat tiles
        tiles = QGridLayout()
        tiles.setSpacing(8)
        self.detail_tiles: dict[str, QLabel] = {}
        for i, (key, title) in enumerate((
            ("day_tokens", "Month tokens"), ("day_active", "Month active"),
            ("usage_records", "Usage records"), ("reset_events", "Resets available"),
        )):
            tile = QFrame()
            tile.setObjectName("card")
            tl = QVBoxLayout(tile)
            tl.setContentsMargins(11, 9, 11, 9)
            tl.addWidget(_label(title, "faint"))
            val = _label("—", bold=True, size=17)
            self.detail_tiles[key] = val
            tl.addWidget(val)
            tiles.addWidget(tile, i // 2, i % 2)
        lay.addLayout(tiles)

        # key/value card
        kv = QFrame()
        kv.setObjectName("card")
        self._kv_layout = QVBoxLayout(kv)
        self._kv_layout.setContentsMargins(12, 10, 12, 10)
        self._kv_layout.setSpacing(4)
        self.kv_rows: dict[str, QLabel] = {}
        for label in ("Account", "Plan", "Capability", "Desktop", "Weekly left", "Weekly reset", "Session left", "Session reset", "Path"):
            row = QHBoxLayout()
            row.addWidget(_label(label, "faint"))
            row.addStretch(1)
            val = _label("—", "muted")
            self.kv_rows[label] = val
            row.addWidget(val)
            self._kv_layout.addLayout(row)
        lay.addWidget(kv)

        # Provider-aware action groups. Only the actions a provider supports are
        # shown, and they are packed (no empty grid cells) so the rail never
        # looks half-finished. Rebuilt per selected account in _rebuild_actions.
        self._action_groups = (
            ("SESSION", [("Coding", "ghost", "use_in_coding"), ("Open Desktop", "primary", "desktop"), ("Open CLI", "ghost", "cli")]),
            ("AUTH", [("Login", "ghost", "login"), ("Device", "ghost", "device"), ("Logout", "ghost", "logout"),
                      ("Desktop Login", "ghost", "desktop_login"),
                      ("Status", "ghost", "status"), ("Doctor", "ghost", "doctor"), ("Refresh", "success", "refresh")]),
            ("DIAGNOSTICS & RESET", [("Online", "success", "online"), ("Dry run", "dim", "dry_run"), ("Restore", "ghost", "restore"),
                                     ("Use reset", "dim", "use_reset"), ("Set 5h", "ghost", "set_timer"), ("Clear timer", "ghost", "clear_timer"),
                                     ("Open home", "ghost", "home"), ("Seed config", "ghost", "seed")]),
        )
        self.action_host = QWidget()
        self._action_layout = QVBoxLayout(self.action_host)
        self._action_layout.setContentsMargins(0, 0, 0, 0)
        self._action_layout.setSpacing(6)
        self.action_buttons: dict[str, object] = {}
        lay.addWidget(self.action_host)

        # activity log
        loghead = QHBoxLayout()
        loghead.addWidget(_label("ACTIVITY LOG", "sectionLabel"))
        loghead.addStretch(1)
        self._log_refresh_link = QLabel("Refresh")
        self._log_refresh_link.setStyleSheet(f"color:{self._tm.tokens['accent']};font-size:11px;font-weight:600;")
        self._log_refresh_link.setCursor(Qt.PointingHandCursor)
        self._log_refresh_link.mousePressEvent = lambda _e: self.refresh_all()
        loghead.addWidget(self._log_refresh_link)
        lay.addLayout(loghead)
        self.log_view = QLabel("No activity yet.")
        self.log_view.setObjectName("panel")
        self.log_view.setStyleSheet(
            f"background:{self._tm.tokens['panel']};border:1px solid {self._tm.tokens['border']};"
            f"border-radius:8px;padding:8px;font-family:Consolas,'Courier New',monospace;font-size:10px;color:{self._tm.tokens['text2']};"
        )
        self.log_view.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.log_view.setWordWrap(True)
        self.log_view.setMinimumHeight(80)
        lay.addWidget(self.log_view)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(350)
        scroll.setWidget(col)
        return scroll

