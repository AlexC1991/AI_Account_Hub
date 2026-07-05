"""Accounts dashboard screen: the persistent 3-column layout and all its actions."""

from __future__ import annotations

import datetime as _dt
import json

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ai_account_hub import data
from ai_account_hub import core as L
from ai_account_hub.ui.widgets import AccentBar, Avatar, SeverityBar, StatusPill, make_button

from ai_account_hub.ui.screens.accounts_screen.workers import ActionWorker, RefreshWorker
from ai_account_hub.ui.screens.accounts_screen.card import AccountCard, _label


class AccountsScreen(QWidget):
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

    # ---------- data (update in place) ----------
    def set_profiles(self, profiles: list[dict]) -> None:
        self._profiles = list(profiles)
        if self._selected and self._find(self._selected) is None:
            self._selected = None
        self.refresh()

    def refresh(self) -> None:
        self._rebuild_cards()
        self._update_summary()
        if hasattr(self, "calendar"):
            self.calendar.set_profiles(self._visible_profiles())
        self._update_stats()
        self._update_detail()

    def _update_stats(self) -> None:
        day = self._selected_day
        visible = self._visible_profiles()
        try:
            all_entries = L.history_usage_entries(visible)
        except Exception:
            all_entries = []
        # Top cards summarise the CURRENT MONTH (one deduped record per
        # account-day); "Pool tokens" stays all-time. day_entries feeds the
        # per-account right-rail tiles below.
        month_prefix = str(day)[:7]  # "YYYY-MM"
        month_entries = [e for e in all_entries if str(e.get("day", "")).startswith(month_prefix)]
        month_tokens = sum(int(e.get("tokens") or 0) for e in month_entries)
        month_minutes = sum(int(e.get("minutes") or 0) for e in month_entries if e.get("minutes") is not None)
        month_accounts = len({e.get("profileId") for e in month_entries if e.get("profileId")})
        pool_tokens = sum(int(e.get("tokens") or 0) for e in all_entries)
        pool_accounts = len({e.get("profileId") for e in all_entries if e.get("profileId")})

        def _fmt_minutes(m: int) -> str:
            if not m:
                return "—"
            return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"

        def _plural(n: int, word: str) -> str:
            return f"{n} {word}" if n == 1 else f"{n} {word}s"

        self.stat_cards["day_tokens"].setText(data.compact_number(month_tokens))
        self.stat_captions["day_tokens"].setText(f"{_plural(month_accounts, 'account')} · {_plural(len(month_entries), 'record')}")
        self.stat_cards["day_active"].setText(_fmt_minutes(month_minutes))
        self.stat_captions["day_active"].setText("Provider minutes where exposed")
        self.stat_cards["pool_tokens"].setText(data.compact_number(pool_tokens))
        self.stat_captions["pool_tokens"].setText(f"{_plural(pool_accounts, 'account')} · {_plural(len(all_entries), 'record')}")
        markers = getattr(self.calendar, "_markers", {}) if hasattr(self, "calendar") else {}
        # Count reset markers in the current month only (the calendar dict spans
        # the visible 6-week range), so the "This month" caption is accurate.
        self.stat_cards["reset_markers"].setText(
            str(sum(len(v) for k, v in markers.items() if str(k).startswith(month_prefix)))
        )
        # right-rail 2x2 tiles for the selected account. History rows are keyed
        # by the shared backend's profile_id (L.profile_id) — which differs from
        # data.profile_id for Codex (codexHome vs "codex:name") — so filter by
        # that id, otherwise Codex tiles always read 0.
        sel_prof = self._find(self._selected) if self._selected is not None else None
        sel_pid = L.profile_id(sel_prof) if sel_prof is not None else None
        sel_month = month_entries if sel_pid is None else [e for e in month_entries if e.get("profileId") == sel_pid]
        active_values = [int(e.get("minutes") or 0) for e in sel_month if e.get("minutes") is not None]
        self.detail_tiles["day_tokens"].setText(data.compact_number(sum(int(e.get("tokens") or 0) for e in sel_month)))
        self.detail_tiles["day_active"].setText(_fmt_minutes(sum(active_values)))
        self.detail_tiles["usage_records"].setText(str(len(sel_month)))
        # "Resets available" = the account's OpenAI/Codex reset credits (usable
        # via "Use reset"), not calendar marker events. Sum across visible
        # accounts when none is selected; providers without credits show "—".
        def _reset_credits(p: dict) -> int:
            raw = str(p.get("resetCreditsAvailable") or "").strip()
            return int(raw) if raw.isdigit() else 0
        if self._selected is None:
            self.detail_tiles["reset_events"].setText(str(sum(_reset_credits(p) for p in visible)))
        else:
            prof = self._find(self._selected)
            raw = str((prof or {}).get("resetCreditsAvailable") or "").strip()
            self.detail_tiles["reset_events"].setText(raw if raw else "—")

    def _resets_on_day(self, iso: str) -> list[dict]:
        """Weekly-limit resets that land on the given day, matching the calendar
        chip logic (UTC-date weekly recurrence), with the precise occurrence
        instant so the modal can show the exact reset time."""
        try:
            target = _dt.date.fromisoformat(iso)
        except ValueError:
            return []
        out: list[dict] = []
        for profile in self._visible_profiles():
            raw = profile.get("weeklyResetEstimateUtc") or profile.get("weeklyLimitResetUtc")
            parsed = L.parse_iso_datetime(raw)
            if parsed is None:
                continue
            # Match the calendar chip, which is placed on the reset's LOCAL date.
            delta = (target - parsed.astimezone().date()).days
            if delta % 7 != 0:
                continue
            out.append({
                "profile": profile,
                "occ_utc": parsed + _dt.timedelta(days=delta),
                "estimated": bool(str(profile.get("weeklyResetEstimateUtc") or "").strip()),
            })
        out.sort(key=lambda r: r["occ_utc"])
        return out

    def _reset_section(self, iso: str, v) -> None:
        """Append a 'LIMIT RESETS' block (one styled row per account resetting
        that day: exact local time, countdown, estimated/reported tag)."""
        resets = self._resets_on_day(iso)
        if not resets:
            return
        t = self._tm.tokens
        v.addWidget(_label("LIMIT RESETS", "sectionLabel"))
        now = _dt.datetime.now(_dt.timezone.utc)
        for r in resets:
            p = r["profile"]
            occ_local = r["occ_utc"].astimezone()
            card = QFrame()
            card.setObjectName("resetRow")
            card.setStyleSheet(
                f"#resetRow{{background:{t['panel2']};border:1px solid {t['border']};border-radius:8px;}}"
            )
            rl = QHBoxLayout(card)
            rl.setContentsMargins(10, 8, 10, 8)
            rl.setSpacing(10)
            rl.addWidget(Avatar(
                data.provider_color(p), data.provider_monogram(p),
                size=28, radius=7, icon_path=data.provider_icon_path(p),
            ))
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(_label(f"{p.get('name', 'Account')} · weekly limit reset", bold=True))
            time_str = occ_local.strftime("%I:%M %p").lstrip("0")
            tz = occ_local.strftime("%Z")
            detail = occ_local.strftime("%A, %B %d") + " · " + time_str + (f" {tz}" if tz else " (local)")
            col.addWidget(_label(detail, "muted"))
            rl.addLayout(col, 1)
            rcol = QVBoxLayout()
            rcol.setSpacing(2)
            if r["occ_utc"] <= now:
                cd = _label("already reset", "faint")
            else:
                cd = _label("in " + L.format_countdown(r["occ_utc"].isoformat()), bold=True)
            cd.setAlignment(Qt.AlignRight)
            rcol.addWidget(cd)
            tag = _label("estimated" if r["estimated"] else "reported by provider", "faint")
            tag.setAlignment(Qt.AlignRight)
            rcol.addWidget(tag)
            rl.addLayout(rcol)
            v.addWidget(card)

    def _show_day_detail(self, iso: str) -> None:
        # The top stat cards stay on "today"; this modal shows the clicked day's
        # breakdown (so a day click no longer silently swaps the header stats).
        from PySide6.QtWidgets import QDialog, QVBoxLayout as V, QScrollArea
        try:
            entries = L.history_usage_entries(self._visible_profiles(), iso_day=iso)
        except Exception:
            entries = []
        total = sum(int(e.get("tokens") or 0) for e in entries)
        dlg = QDialog(self)
        dlg.setWindowTitle("Day detail")
        dlg.setMinimumWidth(560)
        v = V(dlg)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(8)
        try:
            heading = _dt.date.fromisoformat(iso).strftime("%A, %B %d, %Y")
        except ValueError:
            heading = iso
        title = _label(heading, bold=True, size=17)
        v.addWidget(title)
        v.addWidget(_label(f"{data.compact_number(total)} tokens · {len({e.get('profileId') for e in entries})} accounts", "faint"))
        self._reset_section(iso, v)
        if not entries:
            v.addWidget(_label("No usage recorded for this day.", "muted"))
        else:
            v.addWidget(_label("USAGE BY ACCOUNT", "sectionLabel"))
            # One row per account: sum every usage record for that account on
            # the day (the raw entries can hold several records per account).
            agg: dict[str, dict] = {}
            for e in entries:
                p = e.get("profile") or {}
                key = str(e.get("profileId") or p.get("name") or id(p))
                slot = agg.setdefault(key, {"tokens": 0, "profile": p})
                slot["tokens"] += int(e.get("tokens") or 0)
            rows = sorted(agg.values(), key=lambda x: -x["tokens"])
            top = max((r["tokens"] for r in rows), default=1) or 1
            host = QWidget()
            hv = V(host)
            hv.setSpacing(8)
            for item in rows:
                p = item["profile"]
                tokens = item["tokens"]
                row = QHBoxLayout()
                row.addWidget(
                    Avatar(
                        data.provider_color(p),
                        data.provider_monogram(p),
                        size=26,
                        radius=6,
                        icon_path=data.provider_icon_path(p),
                    )
                )
                row.addWidget(_label(str(p.get("name", "Account")), bold=True), 1)
                row.addWidget(_label(data.compact_number(tokens), "muted"))
                bar = AccentBar(self._tm.tokens)
                bar.set_fraction(tokens / top)
                cell = QWidget()
                cv = V(cell)
                cv.setContentsMargins(0, 0, 0, 0)
                cv.setSpacing(4)
                cv.addLayout(row)
                cv.addWidget(bar)
                hv.addWidget(cell)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(host)
            v.addWidget(scroll, 1)
        close = make_button("Close", "primary")
        close.clicked.connect(dlg.accept)
        v.addWidget(close, 0, Qt.AlignRight)
        dlg.exec()

    def _rebuild_cards(self) -> None:
        # Cards are cheap and identity-keyed; rebuild only the list, never the
        # columns/chrome (those persist for the whole app lifetime).
        for card in self._cards.values():
            card.setParent(None)
        self._cards.clear()
        ordered = self._sorted_profiles()
        term = self.search.text().strip().lower()
        for profile in ordered:
            if term and term not in str(profile.get("name", "")).lower():
                continue
            card = AccountCard(profile, self._tm.tokens, self._card_template)
            card.clicked.connect(self.select)
            self._cards[card.pid] = card
            self._list_layout.insertWidget(self._list_layout.count() - 1, card)
        for pid, card in self._cards.items():
            card.set_selected(pid == self._selected)
        self.total_label.setText(f"Total {len(self._profiles)}")
        self._apply_desktop_active()

    def _toggle_sort_dir(self) -> None:
        self._sort_desc = not self._sort_desc
        self._settings["sortDescending"] = self._sort_desc
        data.save_settings(self._settings)
        self._update_sort_dir_icon()
        self._resort()

    def _sort_mode_changed(self, _index: int) -> None:
        # Remaining-capacity and recency views are most useful with the largest
        # value first. Text/state views retain the conventional ascending order.
        self._sort_desc = self.sort_by.currentText() in {
            "Session left", "Weekly left", "Last refresh",
        }
        self._settings["sortDescending"] = self._sort_desc
        self._update_sort_dir_icon()
        self._resort()

    def _update_sort_dir_icon(self) -> None:
        self.sort_dir_btn.setText("↓" if self._sort_desc else "↑")
        self.sort_dir_btn.setToolTip(
            "Order: High → low" if self._sort_desc else "Order: Low → high"
        )

    def _sorted_profiles(self) -> list[dict]:
        mode = self.sort_by.currentText()
        if mode == "Manual":
            return list(self._profiles)
        reverse = self._sort_desc

        if mode in {"Weekly left", "Session left"}:
            field = (
                "weeklyLimitUsedPercent"
                if mode == "Weekly left"
                else "shortLimitUsedPercent"
            )
            known: list[tuple[float, str, dict]] = []
            unknown: list[dict] = []
            for profile in self._profiles:
                left = data.percent_left(profile.get(field))
                if left is None:
                    unknown.append(profile)
                else:
                    known.append((
                        left,
                        str(profile.get("name") or "").lower(),
                        profile,
                    ))
            known.sort(key=lambda item: (item[0], item[1]), reverse=reverse)
            unknown.sort(key=lambda profile: str(profile.get("name") or "").lower())
            return [item[2] for item in known] + unknown

        def keyfn(p: dict):
            if mode == "Name":
                return str(p.get("name", "")).lower()
            if mode == "Provider":
                return data.provider_key(p)
            if mode == "State":
                return {"not_ready": 0, "error": 1, "login": 2, "ready": 3, "idle": 4}.get(data.account_state(p), 5)
            if mode == "Last refresh":
                parsed = L.parse_iso_datetime(p.get("lastLimitsRefreshUtc"))
                return parsed.timestamp() if parsed else 0
            return str(p.get("name", "")).lower()
        try:
            return sorted(self._profiles, key=keyfn, reverse=reverse)
        except TypeError:
            return list(self._profiles)

    def _visible_profiles(self) -> list[dict]:
        term = self.search.text().strip().lower()
        return [
            profile
            for profile in self._sorted_profiles()
            if not term or term in str(profile.get("name", "")).lower()
        ]

    def _apply_filter(self, _text: str) -> None:
        self._rebuild_cards()
        self._update_summary()
        self.calendar.set_profiles(self._visible_profiles())
        self._update_stats()
        if self._selected is None:
            self._update_detail()

    def _resort(self) -> None:
        self._settings["sortMode"] = self.sort_by.currentText()
        data.save_settings(self._settings)
        self._rebuild_cards()

    def _change_card_template(self, _index: int) -> None:
        self._card_template = self.card_view.currentText()
        self._settings["cardTemplate"] = self._card_template
        data.save_settings(self._settings)
        self._rebuild_cards()

    def _move_selected(self, direction: int) -> None:
        if self._selected is None:
            return
        index = next((i for i, profile in enumerate(self._profiles) if data.profile_id(profile) == self._selected), -1)
        target = index + direction
        if index < 0 or target < 0 or target >= len(self._profiles):
            return
        self._profiles[index], self._profiles[target] = self._profiles[target], self._profiles[index]
        self.sort_by.setCurrentText("Manual")
        data.save_profiles(self._profiles)
        self.profiles_changed.emit(list(self._profiles))
        self._rebuild_cards()

    def _update_summary(self) -> None:
        visible = self._visible_profiles()
        total = len(visible)
        ready = sum(1 for p in visible if data.account_state(p) == "ready")
        self.ready_pill.setText(f"{ready}/{total} ready")
        self.summary_sub.setText(f"{ready} ready · {total - ready} not ready")
        self.summary_bar.set_percent_left(100.0 * ready / total if total else None)

    def select(self, pid: str) -> None:
        self._selected = pid
        self.summary_card.setProperty("selected", "false")
        self.summary_card.style().unpolish(self.summary_card)
        self.summary_card.style().polish(self.summary_card)
        for cid, card in self._cards.items():
            card.set_selected(cid == pid)
        self._update_detail()
        self._update_stats()

    def select_all(self) -> None:
        self._selected = None
        self.summary_card.setProperty("selected", "true")
        self.summary_card.style().unpolish(self.summary_card)
        self.summary_card.style().polish(self.summary_card)
        for card in self._cards.values():
            card.set_selected(False)
        self._update_detail()
        self._update_stats()

    def _find(self, pid: str) -> dict | None:
        return next((p for p in self._profiles if data.profile_id(p) == pid), None)

    def _update_detail(self) -> None:
        profile = self._find(self._selected or "")
        if profile is None:
            visible = self._visible_profiles()
            self.detail_avatar.set_identity(self._tm.tokens["accent"], "ALL")
            self.detail_name.setText("All visible accounts")
            self.detail_sub.setText("Pooled dashboard stats")
            total = len(visible)
            ready = sum(1 for item in visible if data.account_state(item) == "ready")
            self.detail_pill.setText(f"{ready}/{total} ready")
            self.detail_pill.set_kind("ready" if ready == total and total else "warn")
            history = L.history_usage_entries(visible)
            total_tokens = sum(int(entry.get("tokens") or 0) for entry in history)
            total_minutes = sum(int(entry.get("minutes") or 0) for entry in history if entry.get("minutes") is not None)
            self.kv_rows["Account"].setText(f"{total} profiles")
            self.kv_rows["Plan"].setText(f"{ready} ready · {total - ready} unavailable")
            self.kv_rows["Capability"].setText(f"{data.compact_number(total_tokens)} pooled")
            self.kv_rows["Desktop"].setText("—")
            self.kv_rows["Desktop"].setToolTip("")
            self.kv_rows["Weekly left"].setText(L.combined_limit_left_text(visible, "weeklyLimitUsedPercent"))
            self.kv_rows["Weekly reset"].setText(f"{total_minutes // 60}h {total_minutes % 60:02d}m active")
            self.kv_rows["Session left"].setText(L.combined_limit_left_text(visible, "shortLimitUsedPercent"))
            self.kv_rows["Session reset"].setText(f"{len(history)} history records")
            self.kv_rows["Path"].setText("Visible profile history")
            self.action_host.setVisible(False)
            return
        self.action_host.setVisible(True)
        self.detail_avatar.set_identity(
            data.provider_color(profile),
            data.provider_monogram(profile),
            data.provider_icon_path(profile),
        )
        self.detail_name.setText(str(profile.get("name", "Account")))
        self.detail_sub.setText(f"{data.provider_label(profile)} · {data.account_plan(profile)}")
        state = data.account_state(profile)
        self.detail_pill.setText(data.STATE_LABEL.get(state, state.title()))
        self.detail_pill.set_kind(data.STATE_PILL.get(state, "idle"))
        self.kv_rows["Account"].setText(str(profile.get("accountEmail") or profile.get("accountName") or "—"))
        self.kv_rows["Plan"].setText(data.account_plan(profile))
        capability = L.provider_capability(profile)
        self.kv_rows["Capability"].setText(str(capability.get("label") or "—"))
        if data.provider_key(profile) == "claude":
            desktop_state = data.engine().claude_desktop_state_status(profile)
            self.kv_rows["Desktop"].setText(str(desktop_state.get("label") or "—"))
            self.kv_rows["Desktop"].setToolTip(str(desktop_state.get("detail") or ""))
        else:
            self.kv_rows["Desktop"].setText("—")
            self.kv_rows["Desktop"].setToolTip("")
        weekly_left = data.percent_left(profile.get("weeklyLimitUsedPercent"))
        session_left = data.percent_left(profile.get("shortLimitUsedPercent"))
        self.kv_rows["Weekly left"].setText("—" if weekly_left is None else f"{weekly_left:.0f}%")
        self.kv_rows["Weekly reset"].setText(L.format_countdown(profile.get("weeklyResetEstimateUtc") or profile.get("weeklyLimitResetUtc")))
        self.kv_rows["Session left"].setText("—" if session_left is None else f"{session_left:.0f}%")
        self.kv_rows["Session reset"].setText(L.format_countdown(profile.get("shortLimitResetUtc")))
        home = str(profile.get("codexHome") or profile.get("claudeConfigDir") or "—")
        self.kv_rows["Path"].setText(home if len(home) < 40 else "…" + home[-38:])
        self._rebuild_actions(profile)

    def _supported_actions(self, profile: dict) -> set[str]:
        provider = data.provider_key(profile)
        if data.claude_desktop_only(profile):
            # Keep Open CLI visible but disabled so the capability difference
            # is explicit. All remaining actions operate on Desktop state only.
            return {
                "desktop", "cli", "refresh", "online",
                "desktop_login",
            }
        return {
            "codex": {
                "use_in_coding", "desktop", "cli", "login", "device", "status",
                "doctor", "refresh", "online", "dry_run", "restore", "use_reset",
                "set_timer", "clear_timer", "home", "seed",
            },
            "claude": {
                "use_in_coding", "desktop", "cli", "login", "status", "doctor",
                "refresh", "online", "home", "desktop_login",
            },
            "cursor": {
                "use_in_coding", "desktop", "cli", "login", "logout", "status",
                "doctor", "refresh", "online", "home",
            },
            "antigravity": {
                "use_in_coding", "desktop", "cli", "login", "status", "doctor",
                "refresh", "online", "home",
            },
        }.get(provider, {"refresh", "online", "home"})

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
            else:
                child = item.layout()
                if child is not None:
                    self._clear_layout(child)

    def _rebuild_actions(self, profile: dict | None) -> None:
        """Rebuild the action groups for the selected account, packing only the
        supported buttons so there are never empty grid cells."""
        self._clear_layout(self._action_layout)
        self.action_buttons = {}
        if profile is None:
            return
        supported = self._supported_actions(profile)
        for title, specs in self._action_groups:
            visible_specs = [spec for spec in specs if spec[2] in supported]
            if not visible_specs:
                continue
            self._action_layout.addWidget(_label(title, "sectionLabel"))
            grid = QGridLayout()
            grid.setSpacing(6)
            # Choose the column count so rows stay balanced for providers with
            # fewer actions (e.g. 4 buttons → 2×2, not a row of 3 + a stray one),
            # and stretch every column equally so buttons are uniform width.
            cols = self._action_columns(len(visible_specs))
            for c in range(cols):
                grid.setColumnStretch(c, 1)
            reset_available = self._reset_available(profile)
            for i, (text, variant, key) in enumerate(visible_specs):
                if key == "use_reset":
                    # Highlight (success) + enabled only when the account has a
                    # reset credit; otherwise it reads as inactive/disabled.
                    variant = "success" if reset_available else "dim"
                btn = make_button(text, variant)
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                if key == "use_reset":
                    btn.setEnabled(reset_available)
                    btn.setToolTip(
                        "A reset credit is available — use it to reset the 5h limit."
                        if reset_available else
                        "No reset credit available on this account yet."
                    )
                if key == "cli" and data.claude_desktop_only(profile):
                    btn.setEnabled(False)
                    btn.setToolTip(
                        "Claude Code CLI requires a paid Claude Code account. "
                        "This profile manages Claude Desktop only."
                    )
                btn.clicked.connect(lambda _c=False, k=key: self._run_action(k))
                self.action_buttons[key] = btn
                grid.addWidget(btn, i // cols, i % cols)
            self._action_layout.addLayout(grid)

    @staticmethod
    def _action_columns(n: int) -> int:
        # 3 columns max: 4 columns is too narrow for the rail and clips the
        # longer Codex labels ("Clear timer", "Open home", "Seed config"). A
        # 4-button group uses 2×2 so Claude/Antigravity don't get a stray single.
        if n <= 3:
            return max(1, n)
        if n == 4:
            return 2
        return 3

    def _reset_available(self, profile: dict) -> bool:
        raw = str(profile.get("resetCreditsAvailable") or "").strip()
        return raw.isdigit() and int(raw) > 0

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
