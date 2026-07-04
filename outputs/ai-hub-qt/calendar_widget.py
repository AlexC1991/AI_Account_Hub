"""Month + week calendar for the Accounts screen, backed by real usage history.

Reads token totals per day from the shared sqlite usage history (via the
legacy ``history_usage_entries`` helper) and weekly-reset markers from the
profiles. Persistent widget: prev/next/today/mode toggles re-render its grid
in place (rebuild only the day cells, never the chrome) and never touch the
rest of the screen.
"""

from __future__ import annotations

import calendar as _cal
import datetime as _dt

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

import data
import legacy_backend as L
from widgets import ElidedLabel, make_button

_WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


class CalendarWidget(QFrame):
    day_clicked = Signal(str)  # iso date

    def __init__(self, theme_manager) -> None:
        super().__init__()
        self.setObjectName("card")
        self._tm = theme_manager
        self._mode = "month"      # or "week"
        self._anchor = _dt.date.today()
        self._selected_iso = self._anchor.isoformat()
        self._profiles: list[dict] = []
        self._totals: dict[str, int] = {}
        self._markers: dict[str, list[str]] = {}
        self._build()

    def set_profiles(self, profiles: list[dict]) -> None:
        self._profiles = list(profiles)
        self._reload_data()
        self._render_grid()

    # ---------- chrome (built once) ----------
    def _build(self) -> None:
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(14, 12, 14, 14)
        self._root.setSpacing(10)

        head = QHBoxLayout()
        prev = make_button("‹", "ghost", object_name="chevron")
        prev.clicked.connect(lambda: self._shift(-1))
        head.addWidget(prev)
        self._title = QLabel("")
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setStyleSheet("font-size:15px;font-weight:700;")
        self._title.setMinimumWidth(180)
        head.addWidget(self._title, 1)
        nxt = make_button("›", "ghost", object_name="chevron")
        nxt.clicked.connect(lambda: self._shift(1))
        head.addWidget(nxt)
        today = make_button("Today", "ghost", object_name="todayBtn")
        today.clicked.connect(self._go_today)
        head.addWidget(today)
        head.addSpacing(8)
        self._btn_month = make_button("Month", "ghost")
        self._btn_week = make_button("Week", "ghost")
        self._btn_month.clicked.connect(lambda: self._set_mode("month"))
        self._btn_week.clicked.connect(lambda: self._set_mode("week"))
        head.addWidget(self._btn_month)
        head.addWidget(self._btn_week)
        self._root.addLayout(head)
        self._sync_mode_buttons()

        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setSpacing(6)
        self._root.addWidget(self._grid_host, 1)

    # ---------- data ----------
    def _visible_days(self) -> list[_dt.date]:
        if self._mode == "week":
            start = self._anchor - _dt.timedelta(days=(self._anchor.weekday() + 1) % 7)  # Sunday start
            return [start + _dt.timedelta(days=i) for i in range(7)]
        first = self._anchor.replace(day=1)
        start = first - _dt.timedelta(days=(first.weekday() + 1) % 7)
        weeks = 6
        return [start + _dt.timedelta(days=i) for i in range(weeks * 7)]

    def _reload_data(self) -> None:
        self._totals = {}
        try:
            for entry in L.history_usage_entries(self._profiles):
                day = str(entry.get("day") or "")
                if day:
                    self._totals[day] = self._totals.get(day, 0) + int(entry.get("tokens") or 0)
        except Exception:
            self._totals = {}
        self._markers = self._reset_markers(self._visible_days())

    def _reset_markers(self, days: list[_dt.date]) -> dict[str, list[str]]:
        markers: dict[str, list[str]] = {}
        if not days:
            return markers
        start_day, end_day = min(days), max(days)
        for profile in self._profiles:
            reset_raw = profile.get("weeklyResetEstimateUtc") or profile.get("weeklyLimitResetUtc")
            parsed = L.parse_iso_datetime(reset_raw)
            if parsed is None:
                continue
            label = f"{profile.get('name', 'Account')} weekly reset"
            # Place the chip on the reset's LOCAL date so it agrees with the local
            # time shown in the day-detail modal (a UTC-evening reset falls on the
            # next local day for east-of-UTC users).
            occ = parsed.astimezone().date()
            while occ > start_day:
                occ -= _dt.timedelta(days=7)
            while occ < start_day:
                occ += _dt.timedelta(days=7)
            while occ <= end_day:
                markers.setdefault(occ.isoformat(), []).append(label)
                occ += _dt.timedelta(days=7)
        return markers

    # ---------- render (rebuild grid cells only) ----------
    def _render_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        t = self._tm.tokens
        days = self._visible_days()
        self._markers = self._reset_markers(days)
        if self._mode == "week":
            self._title.setText(f"{days[0].strftime('%b %d')} – {days[-1].strftime('%b %d, %Y')}")
            self._render_week(days, t)
            return
        self._title.setText(self._anchor.strftime("%B %Y"))
        self._grid.setSpacing(6)
        self._grid_host.setStyleSheet("")
        for c, wd in enumerate(_WEEKDAYS):
            lab = QLabel(wd.upper())
            lab.setAlignment(Qt.AlignCenter)
            lab.setStyleSheet(f"color:{t['text3']};font-size:10px;font-weight:700;")
            self._grid.addWidget(lab, 0, c)
        today = _dt.date.today()
        for idx, day in enumerate(days):
            r, c = idx // 7 + 1, idx % 7
            self._grid.addWidget(self._day_cell(day, day.month == self._anchor.month, day == today, t), r, c)
        self._grid.setRowStretch(0, 0)
        for r in range(1, 7):
            self._grid.setRowStretch(r, 1)

    def _render_week(self, days: list[_dt.date], t: dict) -> None:
        """One bordered strip: a header row (weekday + circular date badge) and
        a body row (big token total + caption + chips) sharing 7-column hairline
        dividers, per design 3b."""
        self._grid.setSpacing(0)
        self._grid_host.setObjectName("calStrip")
        self._grid_host.setStyleSheet(
            f"#calStrip{{background:{t['panel']};border:1px solid {t['border']};border-radius:10px;}}"
        )
        today = _dt.date.today()
        for col, day in enumerate(days):
            self._grid.addWidget(self._week_header_cell(day, day == today, col, t), 0, col)
            self._grid.addWidget(self._week_body_cell(day, day == today, col, t), 1, col)
        self._grid.setRowStretch(0, 0)
        self._grid.setRowStretch(1, 1)

    def _week_header_cell(self, day: _dt.date, is_today: bool, col: int, t: dict) -> QFrame:
        cell = QFrame()
        cell.setObjectName("calCell")
        left = "" if col == 0 else f"border-left:1px solid {t['border']};"
        cell.setStyleSheet(f"#calCell{{{left}border-bottom:1px solid {t['border']};background:transparent;}}")
        v = QVBoxLayout(cell)
        v.setContentsMargins(6, 8, 6, 8)
        v.setSpacing(5)
        wd = QLabel(_WEEKDAYS[col].upper())
        wd.setAlignment(Qt.AlignCenter)
        wd.setStyleSheet(f"color:{t['text3']};font-size:10px;font-weight:700;")
        v.addWidget(wd)
        badge = QLabel(str(day.day))
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(26, 26)
        if is_today:
            badge.setStyleSheet(
                f"background:{t['accent']};color:{t['accentText']};border-radius:13px;"
                f"font-size:12px;font-weight:700;"
            )
        else:
            badge.setStyleSheet(f"color:{t['text']};font-size:13px;font-weight:600;")
        brow = QHBoxLayout()
        brow.setContentsMargins(0, 0, 0, 0)
        brow.addStretch(1)
        brow.addWidget(badge)
        brow.addStretch(1)
        v.addLayout(brow)
        return cell

    def _week_body_cell(self, day: _dt.date, is_today: bool, col: int, t: dict) -> QFrame:
        iso = day.isoformat()
        cell = QFrame()
        cell.setObjectName("calCell")
        cell.setCursor(Qt.PointingHandCursor)
        left = "" if col == 0 else f"border-left:1px solid {t['border']};"
        bg = _soft(t["accent"], 0.10) if is_today else "transparent"
        cell.setStyleSheet(f"#calCell{{{left}background:{bg};}}")
        cell.setMinimumHeight(150)
        v = QVBoxLayout(cell)
        v.setContentsMargins(10, 12, 10, 12)
        v.setSpacing(3)
        tokens = self._totals.get(iso, 0)
        big = QLabel(f"{data.compact_number(tokens)} tok")
        big.setStyleSheet(f"color:{t['text'] if tokens else t['text3']};font-size:19px;font-weight:700;")
        v.addWidget(big)
        cap = QLabel("TOKENS")
        cap.setStyleSheet(f"color:{t['text3']};font-size:9px;font-weight:700;")
        v.addWidget(cap)
        for label in self._markers.get(iso, [])[:2]:
            chip = ElidedLabel("● " + L.calendar_reset_chip_label(label))
            chip.setStyleSheet(
                f"background:{_soft(t['accent'])};color:{t['accent']};border-radius:5px;padding:2px 6px;font-size:9px;"
            )
            v.addWidget(chip)
        v.addStretch(1)
        cell.mousePressEvent = lambda event, d=iso: self._choose_day(event, d)
        return cell

    def _day_cell(self, day: _dt.date, in_month: bool, is_today: bool, t: dict) -> QFrame:
        iso = day.isoformat()
        cell = QFrame()
        cell.setCursor(Qt.PointingHandCursor)
        bg = t["panel"] if in_month else t["bg"]
        # Only TODAY is highlighted (soft accent fill + accent border). Clicking a
        # day opens the day-detail modal instead of leaving a persistent
        # selection border that competed visually with "today".
        if is_today:
            bg = _soft(t["accent"], 0.18)
            border = t["accent"]
        else:
            border = t["border"]
        border_width = 2 if is_today else 1
        # Scope to the cell's objectName. A type selector (QFrame{...}) still
        # cascades the border onto child QLabels in Qt; an #objectName selector
        # does not — that cascade was drawing boxes around every chip, token
        # number and "+N more".
        cell.setObjectName("calCell")
        cell.setStyleSheet(
            f"#calCell{{background:{bg};border:{border_width}px solid {border};border-radius:8px;}}"
        )
        cell.setMinimumHeight(88)
        v = QVBoxLayout(cell)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(4)
        num = QLabel(str(day.day))
        num.setStyleSheet(f"color:{t['text'] if in_month else t['text3']};font-size:11px;font-weight:700;")
        top.addWidget(num, 0, Qt.AlignTop)
        top.addStretch(1)
        tokens = self._totals.get(iso, 0)
        if tokens:
            # Token total as a small tinted badge in the top-right corner so it
            # reads as a usage figure rather than floating muted text.
            tok = QLabel(data.compact_number(tokens) + " tok")
            tok.setStyleSheet(
                f"background:{t['panel2']};color:{t['text2']};border-radius:6px;"
                f"padding:1px 6px;font-size:9px;font-weight:600;"
            )
            top.addWidget(tok, 0, Qt.AlignTop)
        v.addLayout(top)
        markers = self._markers.get(iso, [])
        for label in markers[:2]:
            chip = ElidedLabel("●  " + L.calendar_reset_chip_label(label))
            chip.setStyleSheet(
                f"background:{_soft(t['accent'])};color:{t['accent']};border-radius:4px;padding:2px 5px;font-size:9px;"
            )
            v.addWidget(chip)
        if len(markers) > 2:
            more = QLabel(f"+{len(markers) - 2} more")
            more.setStyleSheet(f"color:{t['accent']};font-size:9px;font-weight:700;")
            v.addWidget(more)
        v.addStretch(1)
        cell.mousePressEvent = lambda event, d=iso: self._choose_day(event, d)
        return cell

    def _choose_day(self, event, iso: str) -> None:
        # Clicking a day opens its detail modal; it no longer re-renders the grid
        # or leaves a lingering selection border.
        if event.button() != Qt.LeftButton:
            return
        self.day_clicked.emit(iso)

    # ---------- nav ----------
    def _shift(self, direction: int) -> None:
        if self._mode == "week":
            self._anchor += _dt.timedelta(days=7 * direction)
        else:
            m = self._anchor.month - 1 + direction
            y = self._anchor.year + m // 12
            self._anchor = _dt.date(y, m % 12 + 1, 1)
        self._render_grid()

    def _go_today(self) -> None:
        self._anchor = _dt.date.today()
        self._render_grid()

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self._sync_mode_buttons()
        self._render_grid()

    def _sync_mode_buttons(self) -> None:
        for mode, button in (("month", self._btn_month), ("week", self._btn_week)):
            button.setProperty("variant", "primary" if mode == self._mode else "ghost")
            button.style().unpolish(button)
            button.style().polish(button)


def _soft(hexcolor: str, alpha: float = 0.16) -> str:
    from tokens import rgba
    return rgba(hexcolor, alpha)
