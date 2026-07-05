"""Coding view (design section 4): 2-column layout.

This milestone lays down the persistent 2-column shell with the correct rail
nav (New chat / Search / Scheduled) and a conversation + composer scaffold.
The rich message blocks, per-provider composer controls, popovers, and
native-transport wiring land in the next milestone (they attach to these
persistent containers rather than rebuilding them).
"""

from __future__ import annotations

import base64
import html
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QTextEdit,
    QVBoxLayout, QWidget,
)

from ai_account_hub import data
from ai_account_hub import core as L
from ai_account_hub.ui.widgets import (
    Avatar, CyclePill, Dot, ElidedLabel, FolderTag, NetworkLogo,
    SegmentedControl, StatusPill, ToggleSwitch, make_button,
)

# The Coding view is deactivated in the UI for this release (see
# main_window's SegmentedSlider.set_disabled + the Window menu). Because the
# screen is built but can never be shown, its native background loaders — the
# project/thread discovery and the per-thread history reader, each of which
# reads the provider's on-disk state on a worker thread — are gated off here so
# the app does no provider I/O for a surface the user cannot reach. Flip this to
# True when the Coding workbench is finished and re-enabled in the UI.
CODING_UI_ENABLED = False


def _relative_time(updated: object) -> str:
    """Compact 'time ago' from an epoch float or ISO-8601 string ('7m ago')."""
    import datetime as _dt

    ts: float = 0.0
    if isinstance(updated, (int, float)):
        ts = float(updated)
    elif isinstance(updated, str) and updated.strip():
        raw = updated.strip()
        try:
            ts = float(raw)
        except ValueError:
            try:
                ts = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return ""
    if ts <= 0:
        return ""
    # Epoch may be seconds or milliseconds; normalise ms → s.
    if ts > 1e12:
        ts /= 1000.0
    secs = max(0.0, _dt.datetime.now().timestamp() - ts)
    if secs < 60:
        return "just now"
    mins = secs / 60
    if mins < 60:
        return f"{int(mins)}m ago"
    hours = mins / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    if days < 7:
        return f"{int(days)}d ago"
    weeks = days / 7
    if weeks < 5:
        return f"{int(weeks)}w ago"
    months = days / 30
    if months < 12:
        return f"{int(months)}mo ago"
    return f"{int(days / 365)}y ago"


def _thread_preview(th: dict) -> str:
    """First line of a thread's title/preview for the sidebar."""
    raw = str(th.get("preview") or th.get("title") or th.get("name") or "Untitled chat")
    line = raw.splitlines()[0].strip() if raw.strip() else "Untitled chat"
    return line or "Untitled chat"


def _soft(hexcolor: str, alpha: float = 0.16) -> str:
    from ai_account_hub.ui.tokens import rgba
    return rgba(hexcolor, alpha)


def _wrap(text: str, t: dict) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color:{t['text2']};font-size:11px;")
    return lab


def _faint(text: str, t: dict) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color:{t['text3']};font-size:9px;")
    return lab


def _colorize_diff(diff: str) -> str:
    lines = []
    for line in diff.splitlines():
        esc = html.escape(line)
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(f'<span style="color:#45b164">{esc}</span>')
        elif line.startswith("-") and not line.startswith("---"):
            lines.append(f'<span style="color:#e1514e">{esc}</span>')
        elif line.startswith(("diff --git", "@@", "+++", "---")):
            lines.append(f'<span style="color:#2c90e8">{esc}</span>')
        else:
            lines.append(esc)
    return "<br>".join(lines)


class ComposerInput(QPlainTextEdit):
    """Text input where Enter sends and Shift+Enter inserts a newline."""

    def __init__(self, on_submit) -> None:
        super().__init__()
        self._on_submit = on_submit

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            event.accept()
            self._on_submit()
            return
        super().keyPressEvent(event)


def _nav_row(icon: str, text: str, shortcut: str = "") -> QPushButton:
    btn = QPushButton(f"  {icon}   {text}")
    btn.setObjectName("navRow")
    btn.setCursor(Qt.PointingHandCursor)
    if shortcut:
        btn.setText(f"  {icon}   {text}")
    return btn


class CodingScreen(QWidget):
    history_loaded = Signal(int, list)
    projects_loaded = Signal(int, str, list, list)
    active_account_changed = Signal(str)

    def __init__(self, theme_manager) -> None:
        super().__init__()
        self._tm = theme_manager
        self._profiles: list[dict] = []
        self._active_pid: str | None = None
        self._assistant_label: QLabel | None = None
        self._assistant_text = ""
        self._assistant_native_id = ""
        self._collapsed: set[str] = set()  # projects default to expanded (Codex-style)
        self._composer_state: dict = {}
        self._blocks: dict = {}            # id -> block dict (accumulated)
        self._block_cards: dict = {}       # id -> QFrame
        self._block_expanded: set[str] = set()
        self._queued: dict | None = None
        self._attachments: list[Path] = []
        self._current_session_id = ""
        self._bridge_pid = ""
        self._skills: list[dict] = []
        self._history_generation = 0
        self._history_side: str | None = None  # "user"/"agent" — dedup AGENT label
        self._cmd_buf: list[dict] = []          # commands/tool-results in current turn
        self._diff_buf: list[dict] = []         # file edits in current turn
        self._history_pending: list[dict] = []
        self._history_total = 0
        self._history_source: list[dict] = []
        self._history_visible_count = 100
        self._project_generation = 0
        self._project_cache: dict[str, tuple[list[dict], list[dict]]] = {}
        from ai_account_hub.coding_bridge import CodingBridge
        self._bridge = CodingBridge()
        self._build()
        self._bridge.assistant_delta.connect(self._on_assistant_delta)
        self._bridge.activity.connect(self._append_activity)
        self._bridge.block.connect(self._on_block)
        self._bridge.turn_started.connect(lambda: self._set_working(True))
        self._bridge.turn_finished.connect(lambda: self._set_working(False))
        self._bridge.error.connect(self._on_error)
        self._bridge.approval_request.connect(self._on_approval_request)
        self._bridge.claude_permission.connect(self._on_claude_permission)
        self._bridge.session_ready.connect(self._on_session_ready)
        self._bridge.rate_limits_changed.connect(self._on_rate_limits_changed)
        self._bridge.skills_ready.connect(self._on_skills_ready)
        self.history_loaded.connect(self._on_history_loaded)
        self.projects_loaded.connect(self._on_projects_loaded)

    def _build(self) -> None:
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(1)
        self.setStyleSheet(f"background:{self._tm.tokens['border']};")
        row.addWidget(self._build_rail(), 0)
        row.addWidget(self._build_center(), 1)

    def _build_rail(self) -> QWidget:
        rail = QWidget()
        self._rail = rail
        rail.setFixedWidth(280)
        rail.setStyleSheet(f"background:{self._tm.tokens['panel']};")
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(8, 12, 8, 8)
        lay.setSpacing(2)

        # three flat borderless nav rows (design 4a)
        new_chat = _nav_row("✎", "New chat")
        self._add_shortcut(new_chat, "Ctrl+N")
        new_chat.clicked.connect(self._new_chat)
        lay.addWidget(new_chat)

        search_row = QWidget()
        srl = QHBoxLayout(search_row)
        srl.setContentsMargins(10, 0, 10, 0)
        srl.setSpacing(8)
        srl.addWidget(QLabel("⌕"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.setStyleSheet("border:none;background:transparent;padding:6px 0;")
        self.search.textChanged.connect(lambda _t: self._render_projects())
        srl.addWidget(self.search, 1)
        kbd = QLabel("Ctrl K")
        kbd.setObjectName("kbd")
        srl.addWidget(kbd)
        lay.addWidget(search_row)

        scheduled = _nav_row("◷", "Scheduled")
        scheduled.clicked.connect(lambda: self._append_activity("Scheduled tasks are managed by the selected native provider."))
        lay.addWidget(scheduled)

        # projects section
        phead = QHBoxLayout()
        plabel = QLabel("PROJECTS")
        plabel.setObjectName("sectionLabel")
        phead.addWidget(plabel)
        phead.addStretch(1)
        self.project_count = QLabel("0")
        self.project_count.setObjectName("faint")
        phead.addWidget(self.project_count)
        phead.setContentsMargins(10, 12, 10, 4)
        lay.addLayout(phead)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._proj_host = QWidget()
        self._proj_layout = QVBoxLayout(self._proj_host)
        self._proj_layout.setContentsMargins(2, 0, 2, 0)
        self._proj_layout.setSpacing(2)
        self._proj_layout.addStretch(1)
        scroll.setWidget(self._proj_host)
        lay.addWidget(scroll, 1)

        # bottom account switcher row
        self.switcher = QFrame()
        self.switcher.setObjectName("card")
        self.switcher.setCursor(Qt.PointingHandCursor)
        self.switcher.mousePressEvent = self._show_account_menu
        sl = QHBoxLayout(self.switcher)
        sl.setContentsMargins(10, 8, 10, 8)
        sl.setSpacing(8)
        self.switch_avatar = Avatar(self._tm.tokens["text3"], "—", size=26, radius=7)
        sl.addWidget(self.switch_avatar)
        self.switch_name = QLabel("No account")
        sl.addWidget(self.switch_name, 1)
        self.switch_state = StatusPill("", "idle")
        sl.addWidget(self.switch_state)
        sl.addWidget(QLabel("⌄"))
        lay.addWidget(self.switcher)
        return rail

    def _add_shortcut(self, btn: QPushButton, text: str) -> None:
        # right-align a keyboard hint inside the flat nav button
        btn.setText(btn.text() + "        " + text)

    def _build_center(self) -> QWidget:
        center = QWidget()
        self._center = center
        center.setStyleSheet(f"background:{self._tm.tokens['bg']};")
        lay = QVBoxLayout(center)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = QFrame()
        header.setStyleSheet(f"border-bottom:1px solid {self._tm.tokens['border']};")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 10, 12, 10)
        titlebox = QVBoxLayout()
        titlebox.setSpacing(2)
        titlerow = QHBoxLayout()
        titlerow.setContentsMargins(0, 0, 0, 0)
        titlerow.setSpacing(8)
        self.thread_title = QLabel("New thread")
        self.thread_title.setStyleSheet("font-size:14px;font-weight:600;")
        titlerow.addWidget(self.thread_title)
        self.project_pill = QLabel("")
        self.project_pill.setStyleSheet(
            f"background:{self._tm.tokens['panel2']};color:{self._tm.tokens['text2']};"
            f"border:1px solid {self._tm.tokens['border']};border-radius:6px;"
            f"padding:2px 8px;font-size:10px;"
        )
        self.project_pill.setVisible(False)
        titlerow.addWidget(self.project_pill)
        titlerow.addStretch(1)
        self.thread_sub = QLabel("")
        self.thread_sub.setObjectName("faint")
        titlebox.addLayout(titlerow)
        titlebox.addWidget(self.thread_sub)
        hl.addLayout(titlebox, 1)
        open_desktop = make_button("Open in Desktop", "ghost")
        open_desktop.clicked.connect(self._open_desktop)
        details = make_button("Details", "ghost")
        details.clicked.connect(self._show_session_details)
        hl.addWidget(open_desktop)
        hl.addWidget(details)
        lay.addWidget(header)

        # conversation area (appendable message list)
        self._conv_scroll = QScrollArea()
        self._conv_scroll.setWidgetResizable(True)
        conv = QWidget()
        self._conv_layout = QVBoxLayout(conv)
        self._conv_layout.setContentsMargins(40, 20, 40, 20)
        self._conv_layout.setSpacing(6)
        self._empty_hint = self._make_empty_state()
        self._conv_layout.addWidget(self._empty_hint, 10)
        self._conv_layout.addStretch(1)
        self._conv_scroll.setWidget(conv)
        lay.addWidget(self._conv_scroll, 1)

        # composer
        dock = QWidget()
        dl = QVBoxLayout(dock)
        dl.setContentsMargins(40, 6, 40, 14)
        # queued-message row (appears above the composer while a turn runs)
        self._queued_row = QFrame()
        self._queued_row.setObjectName("card")
        qr = QHBoxLayout(self._queued_row)
        qr.setContentsMargins(10, 7, 10, 7)
        qlab = QLabel("QUEUED")
        qlab.setObjectName("faint")
        qr.addWidget(qlab)
        self._queued_text = QLabel("")
        self._queued_text.setStyleSheet(f"color:{self._tm.tokens['text']};font-size:11px;")
        qr.addWidget(self._queued_text, 1)
        steer = make_button("Steer", "primary")
        steer.clicked.connect(self._steer_queued)
        edit = make_button("Edit", "ghost")
        edit.clicked.connect(self._edit_queued)
        dele = make_button("Delete", "ghost")
        dele.clicked.connect(self._delete_queued)
        for b in (steer, edit, dele):
            qr.addWidget(b)
        self._queued_row.setVisible(False)
        dl.addWidget(self._queued_row)
        self._attachment_row = QFrame()
        self._attachment_row.setObjectName("card")
        self._attachment_layout = QHBoxLayout(self._attachment_row)
        self._attachment_layout.setContentsMargins(10, 5, 10, 5)
        self._attachment_layout.setSpacing(6)
        self._attachment_caption = QLabel("")
        self._attachment_caption.setObjectName("faint")
        self._attachment_layout.addWidget(self._attachment_caption, 1)
        clear_attachments = make_button("Clear", "ghost")
        clear_attachments.clicked.connect(self._clear_attachments)
        self._attachment_layout.addWidget(clear_attachments)
        self._attachment_row.setVisible(False)
        dl.addWidget(self._attachment_row)
        composer = QFrame()
        self._composer = composer
        composer.setObjectName("card")
        composer.setStyleSheet(
            f"background:{self._tm.tokens['panel2']};border:1px solid {self._tm.tokens['borderStrong']};border-radius:14px;"
        )
        col = QVBoxLayout(composer)
        col.setContentsMargins(12, 10, 12, 10)
        self.input = ComposerInput(self._send)
        self.input.setPlaceholderText("Ask for follow-up changes  ·  Enter to send, Shift+Enter for newline")
        self.input.setFixedHeight(52)
        self.input.setStyleSheet("border:none;background:transparent;")
        col.addWidget(self.input)
        controls = QHBoxLayout()
        self._attach_btn = make_button("+", "ghost")
        self._attach_btn.setToolTip("Attach images or files")
        self._attach_btn.clicked.connect(self._choose_attachments)
        controls.addWidget(self._attach_btn)
        self._skills_btn = make_button("⚡ Skills", "ghost")
        self._skills_btn.clicked.connect(self._show_skills)
        controls.addWidget(self._skills_btn)
        self.input.textChanged.connect(self._maybe_slash_palette)
        self._stop_btn = make_button("Stop", "ghost")
        self._stop_btn.clicked.connect(self._bridge.stop)
        self._stop_btn.setEnabled(False)
        controls.addWidget(self._stop_btn)
        controls.addSpacing(8)
        self._provider_controls = QWidget()
        self._provider_controls_row = QHBoxLayout(self._provider_controls)
        self._provider_controls_row.setContentsMargins(0, 0, 0, 0)
        self._provider_controls_row.setSpacing(6)
        controls.addWidget(self._provider_controls)
        controls.addStretch(1)
        self.session_caption = QLabel("Native passthrough")
        self.session_caption.setObjectName("faint")
        controls.addWidget(self.session_caption)
        self._send_btn = QPushButton("➤")
        self._send_btn.setProperty("variant", "primary")
        self._send_btn.setFixedSize(34, 34)
        self._send_btn.setStyleSheet("border-radius:17px;")
        self._send_btn.clicked.connect(self._send)
        controls.addWidget(self._send_btn)
        col.addLayout(controls)
        dl.addWidget(composer)
        lay.addWidget(dock)
        return center

    # ---------- data ----------
    def set_profiles(self, profiles: list[dict]) -> None:
        # Desktop-only Claude accounts belong in the Accounts switcher, not in
        # the coding harness. They have no Claude Code transport or CLI.
        profiles = [profile for profile in profiles if data.coding_capable(profile)]
        self._profiles = list(profiles)
        if profiles and not self._active_pid:
            ready = next((profile for profile in profiles if data.account_state(profile) == "ready"), profiles[0])
            self._active_pid = data.profile_id(ready)
        if self._active_pid and not any(data.profile_id(profile) == self._active_pid for profile in profiles):
            self._active_pid = data.profile_id(profiles[0]) if profiles else None
        self._sync_active()

    def set_active_account(self, pid: str) -> None:
        profile = next((item for item in self._profiles if data.profile_id(item) == pid), None)
        if profile is None or not data.coding_capable(profile):
            return
        if pid == self._active_pid:
            return
        self._bridge.reset_session()
        self._current_session_id = ""
        self._new_chat(reset_bridge=False)
        self._active_pid = pid
        self._sync_active()

    def _sync_active(self) -> None:
        p = next((x for x in self._profiles if data.profile_id(x) == self._active_pid), None)
        if p is None:
            p = self._profiles[0] if self._profiles else None
        if p is None:
            return
        self.switch_avatar.set_identity(
            data.provider_color(p),
            data.provider_monogram(p),
            data.provider_icon_path(p),
        )
        self.switch_name.setText(str(p.get("name", "Account")))
        state = data.account_state(p)
        self.switch_state.setText(L.status_badge_text(p, state))
        self.switch_state.set_kind(data.STATE_PILL.get(state, "idle"))
        self.thread_sub.setText(f"{data.provider_label(p)} · {p.get('name', '')}")
        if self._bridge_pid != data.profile_id(p):
            self._bridge.reset_session()
            self._bridge.set_session(p, self._current_session_id)
            self._bridge_pid = data.profile_id(p)
        self._update_session_caption()
        self._render_projects()
        self.active_account_changed.emit(data.profile_id(p) or "")
        self._render_provider_controls(p)
        # Keep the centered empty-state breadcrumb in sync with the active
        # account (it is first built before profiles have loaded).
        if self._empty_hint is not None:
            fresh = self._make_empty_state()
            self._conv_layout.replaceWidget(self._empty_hint, fresh)
            self._empty_hint.setParent(None)
            self._empty_hint = fresh

    def _show_account_menu(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        menu = QMenu(self)
        for profile in self._profiles:
            label = str(profile.get("name", "Account"))
            state = data.account_state(profile)
            action = menu.addAction(f"{'✓ ' if data.profile_id(profile) == self._active_pid else '   '}{label}  ·  {L.status_label(state)}")
            action.triggered.connect(lambda _checked=False, pid=data.profile_id(profile): self.set_active_account(pid))
        menu.exec(self.switcher.mapToGlobal(self.switcher.rect().topLeft()))

    # ---------- per-provider composer controls (design 4c) ----------
    def _render_provider_controls(self, profile: dict) -> None:
        provider = data.provider_key(profile)
        profile_key = data.profile_id(profile)
        row = self._provider_controls_row
        while row.count():
            item = row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        defaults = {
            "model": "",
            "effort": "",
            "access": "workspace" if provider == "codex" else "default",
            "personality": "friendly" if provider == "codex" else "",
        }
        defaults.update(L.read_coding_profile_defaults(profile))
        state = self._composer_state.setdefault(profile_key, defaults)
        t = self._tm.tokens

        def menu_btn(label_key: str, options: list[tuple[str, str]]) -> QPushButton:
            current = str(state.get(label_key) or "")
            current_label = next((label for label, value in options if value == current), current or options[0][0])
            btn = make_button(f"{current_label}  ⌄", "ghost")
            menu = QMenu(btn)
            for label, value in options:
                act = menu.addAction(("✓ " if value == current else "   ") + label)
                act.triggered.connect(
                    lambda _c=False, v=value, k=label_key: self._set_composer(profile_key, k, v, profile)
                )
            btn.setMenu(menu)
            return btn

        models = list(L.CODING_FALLBACK_MODELS.get(provider, [("Default", "")]))
        efforts = list(L.CODING_EFFORT_OPTIONS.get(provider, [("Default", "")]))
        access = list(L.CODING_ACCESS_OPTIONS.get(provider, [("Provider default", "default")]))
        if provider == "codex":
            row.addWidget(menu_btn("model", models))
            row.addWidget(menu_btn("effort", efforts))
            row.addWidget(menu_btn("access", access))
        elif provider == "claude":
            row.addWidget(menu_btn("model", models))
            row.addWidget(menu_btn("effort", efforts))
            # Click-to-cycle permission pill: Accept edits → Plan → Manual approval.
            cycle_opts = [(lab, val) for lab, val in access if val in ("accept-edits", "plan", "default")]
            if str(state.get("access") or "") not in {v for _, v in cycle_opts}:
                state["access"] = cycle_opts[0][1] if cycle_opts else "default"
            pill = CyclePill(cycle_opts, str(state.get("access") or ""), t)
            pill.changed.connect(lambda v: self._set_composer_value(profile_key, "access", v))
            row.addWidget(pill)
        elif provider == "cursor":
            acc = str(state.get("access") or "default")
            if "auto_run" not in state:
                state["auto_run"] = acc == "full-access"
            if "cursor_mode" not in state:
                state["cursor_mode"] = acc if acc in ("default", "ask", "plan") else "default"
            mode_opts = [(lab, val) for lab, val in access if val in ("default", "ask", "plan")]
            mode_current = str(state.get("cursor_mode") or "default")
            mode_label = next((lab for lab, val in mode_opts if val == mode_current), "Agent")
            mode_btn = make_button(f"{mode_label}  ⌄", "ghost")
            mode_menu = QMenu(mode_btn)
            for lab, val in mode_opts:
                act = mode_menu.addAction(("✓ " if val == mode_current else "   ") + lab)
                act.triggered.connect(lambda _c=False, v=val: self._set_cursor_mode(profile_key, v, profile))
            mode_btn.setMenu(mode_menu)
            row.addWidget(mode_btn)
            row.addWidget(menu_btn("model", models))
            auto_wrap = QWidget()
            aw = QHBoxLayout(auto_wrap)
            aw.setContentsMargins(4, 0, 0, 0)
            aw.setSpacing(6)
            auto_lbl = QLabel("Auto-run")
            auto_lbl.setStyleSheet(f"color:{t['text3']};font-size:11px;")
            aw.addWidget(auto_lbl)
            auto = ToggleSwitch(checked=bool(state.get("auto_run")), on_color=t["success"], off_color=t["border"])
            auto.toggled.connect(lambda on: self._set_cursor_autorun(profile_key, on))
            aw.addWidget(auto)
            row.addWidget(auto_wrap)
        elif provider == "antigravity":
            row.addWidget(menu_btn("model", models))
            # Always-visible 3-way autonomy control (Manual / Supervised / Autonomous).
            if str(state.get("access") or "") not in {v for _, v in access}:
                state["access"] = access[0][1] if access else "default"
            seg = SegmentedControl(access, str(state.get("access") or ""), t)
            seg.changed.connect(lambda v: self._set_composer_value(profile_key, "access", v))
            row.addWidget(seg)

    def _set_composer(self, profile_key: str, key: str, value: str, profile: dict) -> None:
        self._composer_state.setdefault(profile_key, {})[key] = value
        self._render_provider_controls(profile)

    def _set_composer_value(self, profile_key: str, key: str, value: str) -> None:
        """Update one composer value WITHOUT rebuilding the control row — safe
        for self-updating widgets (cycle pill, toggle, segmented) that would
        otherwise be destroyed mid-signal by a full re-render."""
        self._composer_state.setdefault(profile_key, {})[key] = value
        self._update_session_caption()

    def _set_cursor_mode(self, profile_key: str, mode: str, profile: dict) -> None:
        st = self._composer_state.setdefault(profile_key, {})
        st["cursor_mode"] = mode
        if not st.get("auto_run"):
            st["access"] = mode
        self._render_provider_controls(profile)

    def _set_cursor_autorun(self, profile_key: str, on: bool) -> None:
        st = self._composer_state.setdefault(profile_key, {})
        st["auto_run"] = bool(on)
        st["access"] = "full-access" if on else str(st.get("cursor_mode") or "default")
        self._update_session_caption()

    # ---------- projects/threads (real discovery) ----------
    def _render_projects(self, force: bool = False) -> None:
        # Coding view is disabled for this release: skip native project/thread
        # discovery entirely so no worker thread reads provider state for a
        # screen that can't be opened. (Re-enabled by CODING_UI_ENABLED above.)
        if not CODING_UI_ENABLED:
            return
        p = next((x for x in self._profiles if data.profile_id(x) == self._active_pid), None)
        if p is None:
            self._draw_projects([], [])
            return
        pid = data.profile_id(p)
        if force:
            self._project_cache.pop(pid, None)
        cached = self._project_cache.get(pid)
        if cached is not None:
            self._draw_projects(*cached)
            return
        self._project_generation += 1
        generation = self._project_generation
        self.project_count.setText("…")
        self._clear_project_rows()
        loading = QLabel("Loading native projects…")
        loading.setObjectName("faint")
        self._proj_layout.insertWidget(0, loading)

        def worker() -> None:
            # Guarded so a discovery error (or being torn down mid-read on app
            # exit) can't surface as an unhandled thread traceback in the console.
            try:
                projects, loose = data.project_tree(p)
                self.projects_loaded.emit(generation, pid, projects, loose)
            except Exception:
                self.projects_loaded.emit(generation, pid, [], [])

        threading.Thread(target=worker, daemon=True, name="ai-hub-qt-projects").start()

    def _on_projects_loaded(
        self,
        generation: int,
        pid: str,
        projects: list,
        loose: list,
    ) -> None:
        self._project_cache[pid] = (
            [pr for pr in projects if isinstance(pr, dict)],
            [th for th in loose if isinstance(th, dict)],
        )
        if generation != self._project_generation or pid != self._active_pid:
            return
        self._draw_projects(*self._project_cache[pid])

    def _clear_project_rows(self) -> None:
        while self._proj_layout.count() > 1:  # keep trailing stretch
            item = self._proj_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

    def _draw_projects(self, projects: list[dict], loose: list[dict]) -> None:
        self._clear_project_rows()
        t = self._tm.tokens
        self.project_count.setText(str(len(projects)))
        term = self.search.text().strip().lower()

        def matches(th: dict) -> bool:
            return not term or term in _thread_preview(th).lower()

        shown_projects = 0
        for idx, proj in enumerate(projects):
            name = str(proj.get("name") or "")
            threads = [th for th in proj.get("threads", []) if isinstance(th, dict)]
            if term and term not in name.lower():
                threads = [th for th in threads if matches(th)]
                if not threads:
                    continue
            self._proj_layout.insertWidget(
                self._proj_layout.count() - 1,
                self._project_row(proj, threads, t, idx),
            )
            shown_projects += 1

        loose = [th for th in loose if matches(th)]
        if loose:
            self._proj_layout.insertWidget(self._proj_layout.count() - 1, self._section_label("CHATS", t))
            for th in loose[:8]:
                self._proj_layout.insertWidget(
                    self._proj_layout.count() - 1, self._thread_row(th, t, indent=16)
                )

        if not shown_projects and not loose:
            hint = QLabel("No projects yet" if not term else "No matches")
            hint.setStyleSheet(f"color:{t['text3']};font-style:italic;font-size:11px;padding:6px 12px;")
            self._proj_layout.insertWidget(self._proj_layout.count() - 1, hint)

    def _section_label(self, text: str, t: dict) -> QWidget:
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(10, 12, 10, 4)
        lab = QLabel(text)
        lab.setObjectName("sectionLabel")
        h.addWidget(lab)
        h.addStretch(1)
        return box

    def _project_row(self, proj: dict, threads: list[dict], t: dict, idx: int = 0) -> QWidget:
        ws = str(proj.get("path") or "")
        name = str(proj.get("name") or ws)
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        collapsed = ws in self._collapsed
        header = QPushButton()
        header.setObjectName("navRow")
        header.setCursor(Qt.PointingHandCursor)
        header.clicked.connect(lambda _c=False, w=ws: self._toggle_project(w))
        hb = QHBoxLayout(header)
        hb.setContentsMargins(10, 4, 10, 4)
        hb.setSpacing(8)
        # One calm, theme-aware tone for every project (Codex-style), not a
        # rainbow. The open project's folder picks up the accent.
        folder_color = t["accent"] if (not collapsed and any(
            str(th.get("id") or "") == self._current_session_id for th in threads
        )) else t["text3"]
        tag = FolderTag(folder_color)
        tag.setAttribute(Qt.WA_TransparentForMouseEvents)
        hb.addWidget(tag)
        nm = ElidedLabel(name)
        nm.setStyleSheet(f"color:{t['text']};font-size:12px;font-weight:500;")
        nm.setAttribute(Qt.WA_TransparentForMouseEvents)
        hb.addWidget(nm, 1)
        # Codex has no count badge; a faint count only when collapsed hides chats.
        if collapsed and threads:
            cnt = QLabel(str(len(threads)))
            cnt.setStyleSheet(f"color:{t['text3']};font-size:10px;")
            cnt.setAttribute(Qt.WA_TransparentForMouseEvents)
            hb.addWidget(cnt)
        chev = QLabel("▸" if collapsed else "▾")
        chev.setStyleSheet(f"color:{t['text3']};font-size:9px;")
        chev.setAttribute(Qt.WA_TransparentForMouseEvents)
        hb.addWidget(chev)
        v.addWidget(header)
        if not collapsed:
            if not threads:
                empty = QLabel("No chats")
                empty.setStyleSheet(
                    f"color:{t['text3']};font-style:italic;font-size:11px;padding:1px 0 3px 34px;"
                )
                v.addWidget(empty)
            else:
                for th in threads[:8]:
                    v.addWidget(self._thread_row(th, t))
                if len(threads) > 8:
                    more = QLabel(f"+{len(threads) - 8} more")
                    more.setStyleSheet(f"color:{t['text3']};font-size:10px;padding:1px 0 3px 34px;")
                    v.addWidget(more)
        return box

    def _thread_row(self, th: dict, t: dict, indent: int = 34) -> QWidget:
        """A single chat under a project (or in Chats): title + relative time,
        Codex-style. A pulsing dot marks the actively-working thread; the open
        thread is highlighted. No verbose 'Completed' label."""
        preview = _thread_preview(th)
        rel = _relative_time(th.get("updatedAt") or th.get("updated_at") or "")
        tid = str(th.get("id") or "")
        is_open = bool(tid) and tid == self._current_session_id
        working = is_open and bool(self._bridge.busy)
        row = QPushButton()
        row.setObjectName("navRow")
        row.setCursor(Qt.PointingHandCursor)
        if is_open:
            row.setStyleSheet(f"QPushButton{{background:{_soft(t['accent'], 0.16)};border-radius:7px;}}")
        row.clicked.connect(lambda _c=False, item=th: self._open_thread(item))
        hb = QHBoxLayout(row)
        hb.setContentsMargins(indent, 3, 10, 3)
        hb.setSpacing(6)
        if working:
            dot = Dot(t["warn"], pulse=True, size=7)
            dot.setAttribute(Qt.WA_TransparentForMouseEvents)
            hb.addWidget(dot)
        title = ElidedLabel(preview)
        title.setStyleSheet(
            f"color:{t['accent'] if is_open else t['text2']};font-size:11px;"
            + ("font-weight:600;" if is_open else "")
        )
        title.setAttribute(Qt.WA_TransparentForMouseEvents)
        hb.addWidget(title, 1)
        if rel:
            ts = QLabel(rel)
            ts.setStyleSheet(f"color:{t['text3']};font-size:10px;")
            ts.setAttribute(Qt.WA_TransparentForMouseEvents)
            hb.addWidget(ts)
        return row

    def _toggle_project(self, ws: str) -> None:
        if ws in self._collapsed:
            self._collapsed.discard(ws)
        else:
            self._collapsed.add(ws)
        self._render_projects()

    def _open_thread(self, thread: dict) -> None:
        # Unreachable while the Coding view is disabled (no thread rows are
        # drawn), but guarded explicitly so the native history reader never runs
        # for this release.
        if not CODING_UI_ENABLED:
            return
        p = next((x for x in self._profiles if data.profile_id(x) == self._active_pid), None)
        if p is None:
            return
        self._new_chat(reset_bridge=False)
        self._current_session_id = str(thread.get("id") or "")
        self._bridge.set_session(p, self._current_session_id)
        self.thread_title.setText(str(thread.get("preview") or thread.get("title") or "Native thread").splitlines()[0][:60])
        project = Path(str(thread.get("cwd") or "")).name
        self.project_pill.setText(project)
        self.project_pill.setVisible(bool(project))
        self._update_session_caption()
        self._history_generation += 1
        generation = self._history_generation
        self._append_activity("Loading native history…")

        def worker() -> None:
            # Guarded so a transcript-read error (or interpreter shutdown while
            # the read is in flight) can't surface as an unhandled thread crash.
            try:
                messages = self._read_thread_history(p, thread)
            except Exception:
                messages = []
            self.history_loaded.emit(generation, messages)

        threading.Thread(target=worker, daemon=True, name="ai-hub-qt-history").start()

    def _on_history_loaded(self, generation: int, messages: list) -> None:
        if generation != self._history_generation:
            return
        if not messages:
            self._append_activity("No local transcript was readable. The native session can still be resumed.")
            return
        self._history_source = [message for message in messages if isinstance(message, dict)]
        self._history_total = len(self._history_source)
        self._history_visible_count = min(100, self._history_total)
        self._render_history_window(generation)

    def _render_history_window(self, generation: int) -> None:
        self._clear_conversation_widgets()
        self._history_side = None
        self._cmd_buf = []
        self._diff_buf = []
        start = max(0, self._history_total - self._history_visible_count)
        if start > 0:
            older = make_button(f"Load {min(100, start)} older records", "ghost")
            older.clicked.connect(lambda: self._load_older_history(generation))
            self._conv_layout.insertWidget(0, older, 0, Qt.AlignHCenter)
            notice = QLabel(f"Showing the newest {self._history_visible_count} of {self._history_total} records")
            notice.setObjectName("faint")
            notice.setAlignment(Qt.AlignCenter)
            self._conv_layout.insertWidget(1, notice)
        self._history_pending = list(self._history_source[start:])
        self._render_history_batch(generation)

    def _load_older_history(self, generation: int) -> None:
        if generation != self._history_generation:
            return
        self._history_visible_count = min(self._history_total, self._history_visible_count + 100)
        self._blocks.clear()
        self._block_cards.clear()
        self._render_history_window(generation)

    def _render_history_batch(self, generation: int) -> None:
        if generation != self._history_generation:
            return
        batch, self._history_pending = self._history_pending[:18], self._history_pending[18:]
        for message in batch:
            self._render_history_message(message)
        if self._history_pending:
            QTimer.singleShot(1, lambda g=generation: self._render_history_batch(g))
            return
        self._flush_pending_group()
        self._append_activity(
            f"Loaded {min(self._history_visible_count, self._history_total)} of {self._history_total} native history records. "
            "Continue below to resume this session."
        )
        # Open at the latest exchange (your most recent message + the reply),
        # once all batch widgets have been laid out.
        QTimer.singleShot(90, self._scroll_bottom)

    def _read_thread_history(self, profile: dict, thread: dict) -> list[dict]:
        nh = data.native()
        provider = data.provider_key(profile)
        path = Path(str(thread.get("path") or ""))
        session_id = str(thread.get("id") or "")
        try:
            if provider == "codex":
                # Works for any Codex thread (state-DB, rollout-file or hub-ref):
                # read the stored rollout path, and if it's missing/stale re-find
                # the rollout by its session id across the Codex homes.
                if not path.is_file():
                    resolved = self._find_codex_rollout(session_id)
                    if resolved is not None:
                        path = resolved
                if path.is_file():
                    return nh.read_codex_session_file(path)
                return []
            if provider == "claude" and path.is_file():
                return nh.read_claude_thread(path)
            if provider == "cursor" and path.is_file():
                return nh.read_cursor_thread(path)
            if provider == "antigravity":
                return nh.read_antigravity_thread(nh.antigravity_cli_home(), session_id)
        except Exception:
            return []
        return []

    def _find_codex_rollout(self, session_id: str) -> Path | None:
        """Locate a Codex rollout .jsonl by session id across the shared default
        home and the active account's home — resilient to a stale stored path."""
        if not session_id:
            return None
        homes = [Path(L.DEFAULT_CODEX_HOME)]
        active = next((x for x in self._profiles if data.profile_id(x) == self._active_pid), None)
        if active and active.get("codexHome"):
            homes.append(Path(str(active.get("codexHome"))))
        for home in homes:
            sessions = home / "sessions"
            if not sessions.is_dir():
                continue
            try:
                for match in sessions.glob(f"**/rollout-*-{session_id}.jsonl"):
                    if match.is_file():
                        return match
            except OSError:
                continue
        return None

    def _ensure_history_agent_label(self) -> None:
        """Emit a single all-caps AGENT heading when the flow switches from the
        user (or start) into agent-side content — not before every block."""
        if self._history_side == "agent":
            return
        lab = QLabel("AGENT")
        lab.setObjectName("sectionLabel")
        self._conv_layout.insertWidget(self._conv_layout.count() - 1, lab)
        self._history_side = "agent"

    def _add_history_assistant(self, text: str) -> None:
        t = self._tm.tokens
        self._ensure_history_agent_label()
        body = QLabel(L.coding_display_text(text))
        body.setTextFormat(Qt.MarkdownText)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        body.setOpenExternalLinks(True)
        body.setWordWrap(True)
        body.setMinimumWidth(0)
        body.setMaximumWidth(760)
        body.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        body.setStyleSheet(f"color:{t['text']};font-size:13px;line-height:160%;")
        self._conv_layout.insertWidget(self._conv_layout.count() - 1, body)

    def _breadcrumb_text(self) -> str:
        p = next((x for x in self._profiles if data.profile_id(x) == self._active_pid), None)
        if p is None:
            return "Select an account to begin"
        return f"{data.provider_label(p)} · {p.get('name', '')}"

    def _make_empty_state(self) -> QWidget:
        """Centered empty state (design 4b): logo tile + heading + breadcrumb."""
        t = self._tm.tokens
        host = QWidget()
        host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addStretch(1)
        tile = QFrame()
        tile.setObjectName("logoTile")
        tile.setFixedSize(52, 52)
        tl = QHBoxLayout(tile)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setAlignment(Qt.AlignCenter)
        tl.addWidget(NetworkLogo(t["accent"], size=26), 0, Qt.AlignCenter)
        v.addWidget(tile, 0, Qt.AlignHCenter)
        v.addSpacing(10)
        heading = QLabel("New coding thread")
        heading.setAlignment(Qt.AlignHCenter)
        heading.setStyleSheet(f"color:{t['text']};font-size:16px;font-weight:600;")
        v.addWidget(heading, 0, Qt.AlignHCenter)
        v.addSpacing(4)
        sub = QLabel(self._breadcrumb_text())
        sub.setAlignment(Qt.AlignHCenter)
        sub.setStyleSheet(f"color:{t['text3']};font-size:12px;")
        v.addWidget(sub, 0, Qt.AlignHCenter)
        v.addStretch(1)
        return host

    def _render_history_message(self, message: dict) -> None:
        role = str(message.get("role") or "activity")
        if role == "turn_meta":
            return
        text = str(message.get("text") or "").strip()
        image_refs = list(message.get("imageRefs") or [])
        if role == "user":
            self._flush_pending_group()
            self._add_user_message(text, image_refs=image_refs)
            self._history_side = "user"
            return
        if role == "assistant":
            self._flush_pending_group()
            if text:
                self._add_history_assistant(text)
            if image_refs:
                self._ensure_history_agent_label()
                self._add_image_refs(image_refs)
            return
        kind = str(message.get("kind") or "")
        block = {
            "id": str(message.get("nativeId") or f"history-{len(self._blocks)}") + (":result" if kind == "result" else ""),
            "kind": kind,
            "title": str(message.get("title") or (kind.title() if kind else "")),
            "status": str(message.get("status") or ""),
            "body": text,
            "diff": str(message.get("diff") or ""),
            "files": list(message.get("changes") or []),
            "imageRefs": image_refs,
        }
        # Collapse runs of commands/tool-results into one "Ran N commands" line,
        # and runs of file edits into one "Edited N files" card (design §4b).
        if kind in {"tool", "result", "command"}:
            self._queue_group_block(block, "cmd")
        elif kind == "diff":
            enriched = []
            for f in block["files"]:
                if isinstance(f, dict) and not (f.get("added") or f.get("removed")):
                    a, r = self._edit_line_counts(text, str(f.get("kind") or ""))
                    if a or r:
                        f = dict(f, added=a, removed=r)
                enriched.append(f)
            block["files"] = enriched
            self._queue_group_block(block, "diff")
        elif kind:
            self._flush_pending_group()
            self._ensure_history_agent_label()
            self._on_block(block)
        elif text:
            self._flush_pending_group()
            self._ensure_history_agent_label()
            self._append_activity(L.coding_compact_display_text(text, limit=1000))

    def _queue_group_block(self, block: dict, group_kind: str) -> None:
        self._ensure_history_agent_label()
        (self._cmd_buf if group_kind == "cmd" else self._diff_buf).append(block)

    def _flush_pending_group(self) -> None:
        # One "Ran N commands" line and one "Edited N files" card per agent turn,
        # even when reads/edits are interleaved.
        if self._cmd_buf:
            self._conv_layout.insertWidget(self._conv_layout.count() - 1, self._render_command_group(self._cmd_buf))
            self._cmd_buf = []
        if self._diff_buf:
            self._conv_layout.insertWidget(self._conv_layout.count() - 1, self._render_diff_group(self._diff_buf))
            self._diff_buf = []

    def _terminal_glyph(self, color: str, size: int = 12) -> QLabel:
        """The design's command icon: a rounded 'terminal window' rect with a
        '>' prompt inside (matches the reference SVG)."""
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(color))
        pen.setWidthF(1.4)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(1.5, 2.5, size - 3, size - 5, 2, 2)
        chevron = QPainterPath()
        chevron.moveTo(size * 0.32, size * 0.38)
        chevron.lineTo(size * 0.47, size * 0.5)
        chevron.lineTo(size * 0.32, size * 0.62)
        p.drawPath(chevron)
        p.end()
        lab = QLabel()
        lab.setPixmap(pm)
        lab.setFixedSize(size, size)
        lab.setAttribute(Qt.WA_TransparentForMouseEvents)
        return lab

    def _render_command_group(self, group: list[dict]) -> QWidget:
        """A turn's tool activity as the design's collapsed 'Ran N commands' line
        (terminal icon + label, no box), expandable on click."""
        t = self._tm.tokens
        n = len([b for b in group if str(b.get("kind")) in {"tool", "command"}]) or len(group)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(4)
        header = QWidget()
        header.setCursor(Qt.PointingHandCursor)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(7)
        hl.addWidget(self._terminal_glyph(t["text3"], 12))
        htext = QLabel(f"Ran {n} commands")
        htext.setStyleSheet(f"color:{t['text3']};font-size:11px;")
        htext.setAttribute(Qt.WA_TransparentForMouseEvents)
        hl.addWidget(htext)
        hl.addStretch(1)
        detail = QFrame()
        detail.setObjectName("cmdDetail")
        detail.setStyleSheet(f"#cmdDetail{{background:{_soft(t['text3'], 0.06)};border-radius:8px;}}")
        dv = QVBoxLayout(detail)
        dv.setContentsMargins(10, 8, 10, 8)
        dv.setSpacing(3)
        for b in group:
            kind_b = str(b.get("kind") or "")
            glyph = "↳" if kind_b == "result" else "❯"
            head = self._command_head(str(b.get("body") or "").strip(), str(b.get("title") or ""), kind_b)
            line = ElidedLabel(f"{glyph}  {head}")
            line.setStyleSheet(
                f"color:{t['text2'] if kind_b != 'result' else t['text3']};"
                f"font-family:Consolas,'Courier New',monospace;font-size:10px;"
            )
            line.setMaximumWidth(860)
            dv.addWidget(line)
        detail.setVisible(False)

        def toggle(_e, det=detail):
            det.setVisible(not det.isVisible())

        header.mousePressEvent = toggle
        v.addWidget(header)
        v.addWidget(detail)
        return w

    def _render_diff_group(self, group: list[dict]) -> QWidget:
        merged_files: list = []
        diff_text = ""
        for b in group:
            files = b.get("files") or []
            if files:
                merged_files.extend(files)
            elif not diff_text:
                diff_text = str(b.get("diff") or b.get("body") or "")
        merged = {
            "id": str(group[0].get("id") or "diffgroup"),
            "kind": "diff",
            "files": merged_files,
            "diff": diff_text,
        }
        return self._render_diff_card(merged)

    def _add_image_refs(self, refs: list[dict], layout=None) -> None:
        target = layout or self._conv_layout
        for ref in refs[:12]:
            if not isinstance(ref, dict):
                continue
            widget = self._image_thumb(ref)
            if layout is None:
                target.insertWidget(target.count() - 1, widget)
            else:
                target.addWidget(widget)

    def _image_thumb(self, ref: dict) -> QWidget:
        """Design §4b image: a 260x160 rounded thumbnail with a caption, or a
        dashed placeholder tile when the pixels aren't available."""
        t = self._tm.tokens
        pixmap = QPixmap()
        path = Path(str(ref.get("path") or "")).expanduser()
        if path.is_file():
            pixmap.load(str(path))
        elif ref.get("data"):
            try:
                raw = str(ref.get("data") or "")
                if "," in raw and raw.lstrip().startswith("data:"):
                    raw = raw.split(",", 1)[1]
                pixmap.loadFromData(base64.b64decode(raw))
            except (ValueError, TypeError):
                pass
        name = str(ref.get("name") or path.name or "Screenshot")
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 2, 0, 2)
        v.setSpacing(5)
        thumb = QLabel()
        thumb.setFixedSize(260, 160)
        thumb.setAlignment(Qt.AlignCenter)
        if not pixmap.isNull():
            scaled = pixmap.scaled(260, 160, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            if scaled.width() > 260 or scaled.height() > 160:
                x = max(0, (scaled.width() - 260) // 2)
                y = max(0, (scaled.height() - 160) // 2)
                scaled = scaled.copy(x, y, 260, 160)
            thumb.setPixmap(scaled)
            thumb.setStyleSheet(f"background:{t['panel2']};border-radius:10px;")
            from PySide6.QtGui import QRegion
            thumb.setMask(QRegion(0, 0, 260, 160, QRegion.Rectangle))
        else:
            thumb.setText("Screenshot")
            thumb.setStyleSheet(
                f"background:{t['panel2']};border:1px dashed {t['borderStrong']};"
                f"border-radius:10px;color:{t['text3']};font-size:11px;"
            )
        v.addWidget(thumb)
        cap = QLabel(name)
        cap.setStyleSheet(f"color:{t['text3']};font-size:10px;")
        v.addWidget(cap)
        return box

    # ---------- send / stream ----------
    def _choose_attachments(self) -> None:
        selected, _filter = QFileDialog.getOpenFileNames(
            self,
            "Attach files to the next native turn",
            str(Path.home()),
            "All files (*.*)",
        )
        for raw in selected:
            path = Path(raw)
            if path.is_file() and path not in self._attachments:
                self._attachments.append(path)
        self._sync_attachment_row()

    def _clear_attachments(self) -> None:
        self._attachments.clear()
        self._sync_attachment_row()

    def _sync_attachment_row(self) -> None:
        self._attachment_row.setVisible(bool(self._attachments))
        if self._attachments:
            names = ", ".join(path.name for path in self._attachments[:4])
            if len(self._attachments) > 4:
                names += f" +{len(self._attachments) - 4}"
            self._attachment_caption.setText(
                f"{len(self._attachments)} attached · {names}"
            )
        else:
            self._attachment_caption.setText("")

    def _send(self) -> None:
        p = next((x for x in self._profiles if data.profile_id(x) == self._active_pid), None)
        if p is None:
            return
        state = data.account_state(p)
        if state != "ready":
            self._on_error(f"{p.get('name', 'Account')} is {L.status_label(state).lower()}. Select a ready account or refresh its limits.")
            return
        text = self.input.toPlainText().strip()
        attachments = list(self._attachments)
        if not text and not attachments:
            return
        # If a turn is already running, queue this message instead of sending.
        if self._bridge.busy:
            self._queued = {"text": text, "attachments": attachments}
            self.input.clear()
            self._clear_attachments()
            summary = text[:70] or "(attachments only)"
            if attachments:
                summary += f" · {len(attachments)} file(s)"
            self._queued_text.setText(summary)
            self._queued_row.setVisible(True)
            return
        self.input.clear()
        self._clear_attachments()
        self._add_user_message(
            text,
            image_refs=[
                {"path": str(path), "name": path.name}
                for path in attachments
                if L.native_attachment_kind(path) == "image"
            ],
            attachments=attachments,
        )
        self._assistant_label = None
        self._assistant_text = ""
        controls = dict(self._composer_state.get(data.profile_id(p), {}))
        self._bridge.send(p, text, controls, attachments)

    def _steer_queued(self) -> None:
        if not getattr(self, "_queued", None):
            return
        queued = dict(self._queued)
        self._delete_queued()
        self.input.setPlainText(str(queued.get("text") or ""))
        self._attachments = [Path(path) for path in queued.get("attachments") or []]
        self._sync_attachment_row()
        self._send()

    def _edit_queued(self) -> None:
        if not getattr(self, "_queued", None):
            return
        queued = dict(self._queued)
        self.input.setPlainText(str(queued.get("text") or ""))
        self._attachments = [Path(path) for path in queued.get("attachments") or []]
        self._sync_attachment_row()
        self._delete_queued()
        self.input.setFocus()

    def _delete_queued(self) -> None:
        self._queued = None
        self._queued_row.setVisible(False)
        self._queued_text.setText("")

    # ---------- skills + slash popovers ----------
    _SLASH = [
        ("Code review", "Review the current changes"),
        ("Compact", "Compact the conversation"),
        ("Fast", "Switch to a faster model"),
        ("Feedback", "Send feedback"),
        ("Fork", "Fork this session"),
        ("Goal", "Set a session goal"),
        ("MCP", "Manage MCP servers"),
        ("Memories", "View/edit memories"),
        ("Model", "Change model"),
        ("Personality", "Change personality"),
    ]

    def _show_skills(self) -> None:
        p = next((x for x in self._profiles if data.profile_id(x) == self._active_pid), None)
        if p is None:
            return
        if data.provider_key(p) == "codex":
            self._append_activity("Loading native Codex skills…")
            self._bridge.load_skills(p)
            return
        skills = []
        if data.provider_key(p) == "claude":
            skills_root = L.claude_profile_home(p) / "skills"
            if skills_root.is_dir():
                skills = [
                    {"name": path.parent.name, "description": ""}
                    for path in sorted(skills_root.glob("*/SKILL.md"))
                ]
        self._show_skills_menu(skills)

    def _on_skills_ready(self, skills: list) -> None:
        self._skills = [skill for skill in skills if isinstance(skill, dict)]
        self._show_skills_menu(self._skills)

    def _show_skills_menu(self, skills: list[dict]) -> None:
        menu = QMenu(self)
        if not skills:
            empty = menu.addAction("No provider skills found")
            empty.setEnabled(False)
        for skill in skills:
            name = str(skill.get("name") or Path(str(skill.get("path") or "")).parent.name or "skill")
            description = str(skill.get("description") or "")
            label = name if not description else f"{name}  ·  {L.clip_text(description, 52)}"
            act = menu.addAction(label)
            act.triggered.connect(lambda _c=False, n=name: self._insert_skill(n))
        menu.exec(self._skills_btn.mapToGlobal(self._skills_btn.rect().topLeft()))

    def _insert_skill(self, name: str) -> None:
        prefix = f"${name} "
        existing = self.input.toPlainText()
        self.input.setPlainText(prefix + existing)
        cur = self.input.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        self.input.setTextCursor(cur)
        self.input.setFocus()

    def _maybe_slash_palette(self) -> None:
        if self.input.toPlainText().strip() != "/":
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        for name, desc in self._SLASH:
            act = menu.addAction(f"/{name.lower()}   —   {desc}")
            act.triggered.connect(lambda _c=False, n=name: self._pick_slash(n))
        menu.exec(self.input.mapToGlobal(self.input.rect().topLeft()))

    def _pick_slash(self, name: str) -> None:
        self.input.setPlainText(f"/{name.lower()} ")
        cur = self.input.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        self.input.setTextCursor(cur)
        self.input.setFocus()

    def _add_user_message(
        self,
        text: str,
        image_refs: list[dict] | None = None,
        attachments: list[Path] | None = None,
    ) -> None:
        if self._empty_hint is not None:
            self._empty_hint.setParent(None)
            self._empty_hint = None
        t = self._tm.tokens
        body, wrapped_attachments = L.coding_user_message_parts(text)
        bubble = QLabel(body or text)
        bubble.setTextFormat(Qt.MarkdownText)
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        bubble.setOpenExternalLinks(True)
        bubble.setWordWrap(True)
        bubble.setMaximumWidth(560)
        # Size to the text (up to 560), NOT collapse to zero — `Ignored` inside a
        # right-aligned row starved the bubble to width 0 (invisible + a tall gap,
        # which is why "my messages weren't displayed").
        bubble.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
        bubble.setStyleSheet(f"background:{t['panel2']};border-radius:12px;padding:10px 13px;color:{t['text']};font-size:13px;")
        wrap = QHBoxLayout()
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.addStretch(1)
        wrap.addWidget(bubble, 0)
        holder = QWidget()
        holder.setLayout(wrap)
        self._conv_layout.insertWidget(self._conv_layout.count() - 1, holder)
        names = [path.name for path in attachments or []] or wrapped_attachments
        if names:
            attachment_label = QLabel("Attachments: " + ", ".join(names[:8]))
            attachment_label.setObjectName("faint")
            attachment_label.setAlignment(Qt.AlignRight)
            self._conv_layout.insertWidget(self._conv_layout.count() - 1, attachment_label)
        self._add_image_refs(list(image_refs or []))
        self._scroll_bottom()

    def _on_assistant_delta(self, native_id: str, text: str) -> None:
        if native_id in {"claude-result", "cursor-result"} and self._assistant_text:
            if text.startswith(self._assistant_text):
                self._assistant_text = text
                if self._assistant_label is not None:
                    self._assistant_label.setText(L.coding_display_text(self._assistant_text))
                return
            if text == self._assistant_text:
                return
        if self._assistant_label is not None and self._assistant_native_id and native_id != self._assistant_native_id:
            self._assistant_label = None
            self._assistant_text = ""
        if self._assistant_label is None:
            t = self._tm.tokens
            lab = QLabel("AGENT")
            lab.setObjectName("sectionLabel")
            self._conv_layout.insertWidget(self._conv_layout.count() - 1, lab)
            self._assistant_label = QLabel("")
            self._assistant_label.setTextFormat(Qt.MarkdownText)
            self._assistant_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
            self._assistant_label.setOpenExternalLinks(True)
            self._assistant_label.setWordWrap(True)
            self._assistant_label.setMinimumWidth(0)
            self._assistant_label.setMaximumWidth(920)
            self._assistant_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            self._assistant_label.setStyleSheet(f"color:{t['text']};font-size:13px;line-height:160%;")
            self._conv_layout.insertWidget(self._conv_layout.count() - 1, self._assistant_label)
            self._assistant_native_id = native_id
        self._assistant_text += text
        self._assistant_label.setText(L.coding_display_text(self._assistant_text))
        self._scroll_bottom()

    def _append_activity(self, text: str) -> None:
        lab = QLabel("›  " + text)
        lab.setObjectName("faint")
        lab.setWordWrap(True)
        lab.setMinimumWidth(0)
        lab.setMaximumWidth(920)
        lab.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._conv_layout.insertWidget(self._conv_layout.count() - 1, lab)
        self._scroll_bottom()

    # ---------- rich blocks (command/plan/diff/tool/thinking), upserted ----------
    def _on_block(self, block: dict) -> None:
        if self._empty_hint is not None:
            self._empty_hint.setParent(None)
            self._empty_hint = None
        bid = str(block.get("id") or "")
        existing = self._blocks.get(bid)
        if existing is None:
            merged = dict(block)
            if "append" in block:
                merged["body"] = block.get("append", "")
            self._blocks[bid] = merged
        else:
            existing.update({k: v for k, v in block.items() if k != "append"})
            if "append" in block:
                existing["body"] = str(existing.get("body") or "") + block["append"]
            merged = existing
        card = self._render_block_card(merged)
        old = self._block_cards.get(bid)
        if old is not None:
            idx = self._conv_layout.indexOf(old)
            old.setParent(None)
            self._conv_layout.insertWidget(max(0, idx), card)
        else:
            self._conv_layout.insertWidget(self._conv_layout.count() - 1, card)
        self._block_cards[bid] = card
        self._scroll_bottom()

    def _render_block_card(self, block: dict) -> QWidget:
        """Render a content block per design §4b. Commands/tools/results/thinking
        are compact, *un-boxed* muted lines (click to expand); only plan and diff
        get a bordered card; images render as inline thumbnails."""
        kind = str(block.get("kind") or "activity")
        if kind in {"command", "tool", "result"}:
            return self._render_command_line(block)
        if kind == "thinking":
            return self._render_thinking_line(block)
        if kind == "plan":
            return self._render_plan_card(block)
        if kind == "diff":
            return self._render_diff_card(block)
        if kind == "image":
            return self._render_image_block(block)
        if kind == "label":
            return self._render_label_block(block)
        if kind == "workingLabel":
            return self._render_working_label(block)
        return self._render_activity_line(block)

    def _render_label_block(self, block: dict) -> QWidget:
        """Small all-caps inline heading (e.g. 'STEERED CONVERSATION')."""
        t = self._tm.tokens
        lab = QLabel(str(block.get("text") or block.get("body") or block.get("title") or "").upper())
        f = lab.font()
        f.setBold(True)
        f.setLetterSpacing(f.PercentageSpacing, 104)
        lab.setFont(f)
        lab.setStyleSheet(f"color:{t['text3']};font-size:10px;")
        return lab

    def _render_working_label(self, block: dict) -> QWidget:
        """'Working for Ns' muted line with a bottom hairline (design §4b)."""
        t = self._tm.tokens
        w = QFrame()
        w.setObjectName("workRule")
        w.setStyleSheet(f"#workRule{{border-bottom:1px solid {t['border']};}}")
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 6)
        v.setSpacing(0)
        lab = QLabel(str(block.get("text") or block.get("body") or "Working"))
        lab.setStyleSheet(f"color:{t['text3']};font-size:12px;")
        v.addWidget(lab)
        return w

    def _command_head(self, body: str, title: str, kind: str) -> str:
        """Readable one-liner for a command/tool block: the shell command when
        there is one, otherwise the tool name — never a raw '{}' JSON blob."""
        first = body.splitlines()[0].strip() if body else ""
        if first and not first.startswith("{") and first not in {"{}", "}"}:
            return first
        return title or ("Output" if kind == "result" else "command")

    def _render_command_line(self, block: dict) -> QWidget:
        t = self._tm.tokens
        bid = str(block.get("id") or "")
        body = str(block.get("body") or "").strip()
        title = str(block.get("title") or "").strip()
        kind = str(block.get("kind") or "")
        expanded = bid in self._block_expanded
        glyph = "❯" if kind != "result" else "↳"
        head = self._command_head(body, title, kind)
        w = QWidget()
        w.setMaximumWidth(920)
        v = QVBoxLayout(w)
        v.setContentsMargins(2, 1, 2, 1)
        v.setSpacing(3)
        line = ElidedLabel(f"{glyph}  {head}")
        line.setStyleSheet(f"color:{t['text3']};font-family:Consolas,'Courier New',monospace;font-size:11px;")
        has_more = bool(body) and (len(body.splitlines()) > 1 or len(body) > 90)
        if has_more:
            line.setCursor(Qt.PointingHandCursor)
            line.mousePressEvent = lambda _e, i=bid: self._toggle_block(i)
        v.addWidget(line)
        if expanded and has_more:
            out = QLabel(body[:8000])
            out.setWordWrap(True)
            out.setMaximumWidth(900)
            out.setStyleSheet(
                f"background:{_soft(t['text3'], 0.07)};color:{t['text2']};"
                f"font-family:Consolas,'Courier New',monospace;font-size:10px;"
                f"border-radius:6px;padding:6px 8px;"
            )
            row = QHBoxLayout()
            row.setContentsMargins(16, 0, 0, 0)
            row.addWidget(out)
            v.addLayout(row)
        return w

    def _render_thinking_line(self, block: dict) -> QWidget:
        t = self._tm.tokens
        bid = str(block.get("id") or "")
        body = str(block.get("body") or "").strip()
        expanded = bid in self._block_expanded
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(2, 1, 2, 1)
        v.setSpacing(3)
        line = QLabel(("▾  Thinking" if expanded else "▸  Thinking"))
        line.setStyleSheet(f"color:{t['text3']};font-style:italic;font-size:11px;")
        if body:
            line.setCursor(Qt.PointingHandCursor)
            line.mousePressEvent = lambda _e, i=bid: self._toggle_block(i)
        v.addWidget(line)
        if expanded and body:
            r = QLabel(body[:4000])
            r.setWordWrap(True)
            r.setMaximumWidth(900)
            r.setStyleSheet(f"color:{t['text3']};font-size:11px;padding-left:16px;")
            v.addWidget(r)
        return w

    def _render_activity_line(self, block: dict) -> QWidget:
        t = self._tm.tokens
        title = str(block.get("title") or "").strip()
        body = str(block.get("body") or "").strip()
        text = title if not body else (f"{title}: {body.splitlines()[0]}" if title else body.splitlines()[0])
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(2, 1, 2, 1)
        v.setSpacing(0)
        line = ElidedLabel("›  " + (text or "Activity"))
        line.setStyleSheet(f"color:{t['text3']};font-size:11px;")
        line.setMaximumWidth(900)
        v.addWidget(line)
        return w

    def _render_image_block(self, block: dict) -> QWidget:
        t = self._tm.tokens
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(4)
        refs = list(block.get("imageRefs") or [])
        self._add_image_refs(refs, layout=v)
        body = str(block.get("body") or "").strip()
        if body and not refs:
            v.addWidget(_wrap(body[:1200], t))
        return w

    def _plan_status(self, step: dict) -> str:
        st = str(step.get("status") or "pending").lower()
        if st in {"completed", "done"}:
            return "done"
        if st in {"in_progress", "inprogress", "active", "running"}:
            return "active"
        return "pending"

    def _plan_circle(self, kind: str, t: dict) -> QLabel:
        """15px status circle: filled-green ✓ (done), amber ring + dot (active),
        muted ring (pending) — design §4b plan."""
        dot = QLabel()
        dot.setFixedSize(15, 15)
        dot.setAlignment(Qt.AlignCenter)
        if kind == "done":
            dot.setText("✓")
            dot.setStyleSheet(
                f"background:{t['success']};color:{t['bg']};border-radius:7px;font-size:9px;font-weight:700;"
            )
        elif kind == "active":
            dot.setText("●")
            dot.setStyleSheet(f"border:2px solid {t['warn']};border-radius:7px;color:{t['warn']};font-size:6px;")
        else:
            dot.setStyleSheet(f"border:2px solid {t['borderStrong']};border-radius:7px;")
        return dot

    def _render_plan_card(self, block: dict) -> QWidget:
        t = self._tm.tokens
        plan = block.get("plan") if isinstance(block.get("plan"), list) else []
        steps = [s for s in plan if isinstance(s, dict)]
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(f"#card{{background:{t['panel']};border:1px solid {t['border']};border-radius:10px;}}")
        card.setMaximumWidth(900)
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(10)
        done = sum(1 for s in steps if self._plan_status(s) == "done")
        head = QHBoxLayout()
        head.setSpacing(7)
        icon = QLabel("🗒")
        icon.setStyleSheet("font-size:12px;")
        head.addWidget(icon)
        heading = QLabel(str(block.get("title") or "Plan"))
        heading.setStyleSheet(f"color:{t['text']};font-size:12px;font-weight:700;")
        head.addWidget(heading)
        head.addStretch(1)
        counter = QLabel(f"{done}/{len(steps)} done")
        counter.setStyleSheet(f"color:{t['text3']};font-size:10px;")
        head.addWidget(counter)
        v.addLayout(head)
        if not steps:
            v.addWidget(_wrap(str(block.get("body") or "")[:1000], t))
        for step in steps[:20]:
            kind = self._plan_status(step)
            row = QHBoxLayout()
            row.setSpacing(9)
            row.addWidget(self._plan_circle(kind, t), 0, Qt.AlignTop)
            txt = QLabel(str(step.get("step") or step.get("text") or ""))
            txt.setWordWrap(True)
            txt.setStyleSheet(f"color:{t['text'] if kind != 'pending' else t['text3']};font-size:12px;")
            row.addWidget(txt, 1)
            v.addLayout(row)
        return card

    def _hairline(self, t: dict) -> QFrame:
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{t['border']};border:none;")
        return line

    def _edit_line_counts(self, tool_input: str, kind: str) -> tuple[int, int]:
        """Approximate +added/-removed for a Claude Edit/Write/MultiEdit from its
        tool-input JSON (Claude records the edit content but not diff stats)."""
        import json as _json
        try:
            payload = _json.loads(tool_input)
        except (ValueError, TypeError):
            return (0, 0)
        if not isinstance(payload, dict):
            return (0, 0)

        def nlines(s: object) -> int:
            text = str(s or "")
            return text.count("\n") + 1 if text else 0

        if isinstance(payload.get("edits"), list):
            a = r = 0
            for e in payload["edits"]:
                if isinstance(e, dict):
                    a += nlines(e.get("new_string"))
                    r += nlines(e.get("old_string"))
            return (a, r)
        if kind == "write" or payload.get("content") is not None:
            return (nlines(payload.get("content")), 0)
        return (nlines(payload.get("new_string")), nlines(payload.get("old_string")))

    def _diff_counts(self, block: dict) -> tuple[int, int]:
        added = removed = 0
        for item in block.get("files") or []:
            if isinstance(item, dict):
                added += int(item.get("added") or item.get("additions") or 0)
                removed += int(item.get("removed") or item.get("deletions") or 0)
        if not added and not removed:
            for raw in str(block.get("diff") or block.get("body") or "").splitlines():
                if raw.startswith("+") and not raw.startswith("+++"):
                    added += 1
                elif raw.startswith("-") and not raw.startswith("---"):
                    removed += 1
        return added, removed

    def _diff_file_row(self, item, t: dict) -> QWidget | None:
        path = str(item.get("path") or item.get("file") or "") if isinstance(item, dict) else str(item)
        if not path:
            return None
        parts = Path(path).parts
        short = "/".join(parts[-2:]) if len(parts) > 2 else path
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(12, 8, 12, 8)
        rl.setSpacing(10)
        pl = ElidedLabel(short)
        pl.setStyleSheet(f"color:{t['text2']};font-family:Consolas,'Courier New',monospace;font-size:11px;")
        rl.addWidget(pl, 1)
        if isinstance(item, dict):
            a = int(item.get("added") or item.get("additions") or 0)
            r = int(item.get("removed") or item.get("deletions") or 0)
            if a or r:
                cc = QLabel(
                    f"<span style='color:{t['success']};'>+{a}</span> <span style='color:{t['danger']};'>-{r}</span>"
                )
                cc.setStyleSheet("font-size:10px;")
                rl.addWidget(cc)
        return row

    def _render_diff_card(self, block: dict) -> QWidget:
        t = self._tm.tokens
        files = list(block.get("files") or [])
        added, removed = self._diff_counts(block)
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(f"#card{{background:{t['panel']};border:1px solid {t['border']};border-radius:10px;}}")
        card.setMaximumWidth(900)
        v = QVBoxLayout(card)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        # header
        hwrap = QWidget()
        hl = QHBoxLayout(hwrap)
        hl.setContentsMargins(12, 10, 12, 10)
        hl.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(2)
        n = len(files) if files else 1
        fl = QLabel(f"Edited {n} files")
        fl.setStyleSheet(f"color:{t['text']};font-size:12.5px;font-weight:700;")
        left.addWidget(fl)
        if added or removed:
            counts = QLabel(
                f"<span style='color:{t['success']};font-weight:700;'>+{added}</span> "
                f"<span style='color:{t['danger']};font-weight:700;'>-{removed}</span>"
            )
            counts.setStyleSheet("font-size:10px;")
            left.addWidget(counts)
        hl.addLayout(left, 1)
        undo = make_button("Undo ↺", "ghost")
        undo.setFixedHeight(26)
        undo.clicked.connect(lambda: self._append_activity("Undo is handled in the native provider session."))
        review = make_button("Review", "primary")
        review.setFixedHeight(26)
        review.clicked.connect(self._open_desktop)
        hl.addWidget(undo)
        hl.addWidget(review)
        v.addWidget(hwrap)
        v.addWidget(self._hairline(t))
        limit = 6
        for item in files[:limit]:
            row = self._diff_file_row(item, t)
            if row is not None:
                v.addWidget(row)
                v.addWidget(self._hairline(t))
        if len(files) > limit:
            more_box = QWidget()
            mv = QVBoxLayout(more_box)
            mv.setContentsMargins(0, 0, 0, 0)
            mv.setSpacing(0)
            for item in files[limit:]:
                row = self._diff_file_row(item, t)
                if row is not None:
                    mv.addWidget(row)
                    mv.addWidget(self._hairline(t))
            more_box.setVisible(False)
            v.addWidget(more_box)
            link = QLabel(f"Show {len(files) - limit} more files")
            link.setStyleSheet(f"color:{t['accent']};font-size:11px;font-weight:600;padding:8px 12px;")
            link.setCursor(Qt.PointingHandCursor)

            def toggle(_e, box=more_box, lbl=link, extra=len(files) - limit):
                box.setVisible(not box.isVisible())
                lbl.setText("Show fewer files" if box.isVisible() else f"Show {extra} more files")

            link.mousePressEvent = toggle
            v.addWidget(link)
        if not files:
            diff = str(block.get("diff") or block.get("body") or "").strip()
            if diff:
                box = QTextEdit()
                box.setReadOnly(True)
                box.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
                box.setHtml("<pre style=\"white-space:pre;font-family:Consolas,monospace;\">" + _colorize_diff(diff[:24000]) + "</pre>")
                box.setStyleSheet(f"background:{t['bg']};border:none;font-family:Consolas,monospace;font-size:10px;border-radius:6px;padding:6px;")
                box.setMinimumHeight(100)
                box.setMaximumHeight(300)
                v.addWidget(box)
        return card

    def _toggle_block(self, bid: str) -> None:
        if bid in self._block_expanded:
            self._block_expanded.discard(bid)
        else:
            self._block_expanded.add(bid)
        if bid in self._blocks:
            self._on_block(dict(self._blocks[bid]))  # re-render in place

    def _on_approval_request(self, req: dict) -> None:
        from PySide6.QtWidgets import QMessageBox, QInputDialog
        rid = req.get("request_id")
        if req.get("kind") == "input":
            value, ok = QInputDialog.getText(self, "Provider needs input", str(req.get("subject") or "Input:"))
            self._bridge.respond_input(rid, req.get("questions") or [], value if ok else None)
            return
        box = QMessageBox(self)
        box.setWindowTitle("Approval request")
        box.setText(str(req.get("subject") or "The provider is requesting approval."))
        allow = box.addButton("Allow once", QMessageBox.AcceptRole)
        decline = box.addButton("Decline", QMessageBox.RejectRole)
        box.addButton("Cancel", QMessageBox.DestructiveRole)
        box.exec()
        clicked = box.clickedButton()
        decision = "accept" if clicked is allow else ("decline" if clicked is decline else "cancel")
        self._bridge.respond_approval(rid, decision)
        self._append_activity(f"Approval: {decision}")

    def _on_claude_permission(self, key: str) -> None:
        from PySide6.QtWidgets import QInputDialog, QMessageBox
        from ai_account_hub.coding_bridge import allow_decision, _deny
        payload = self._bridge.permission_payload(key)
        tool = str(payload.get("tool_name") or "Claude tool")
        tool_input = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        if tool == "AskUserQuestion":
            questions = tool_input.get("questions") if isinstance(tool_input.get("questions"), list) else []
            answers = {}
            for index, question in enumerate(questions, start=1):
                if not isinstance(question, dict):
                    continue
                prompt = str(question.get("question") or question.get("header") or f"Question {index}")
                options = [
                    str(option.get("label") or "")
                    for option in question.get("options") or []
                    if isinstance(option, dict) and option.get("label")
                ]
                if options and not question.get("multiSelect"):
                    value, ok = QInputDialog.getItem(self, "Claude question", prompt, options, 0, True)
                else:
                    value, ok = QInputDialog.getText(self, "Claude question", prompt)
                if not ok:
                    self._bridge.answer_permission(key, _deny(payload, "User cancelled the Claude question."))
                    return
                value = str(value).strip()
                answers[prompt] = (
                    [part.strip() for part in value.split(",") if part.strip()]
                    if question.get("multiSelect")
                    else value
                )
            updated = dict(tool_input)
            updated["questions"] = questions
            updated["answers"] = answers
            self._bridge.answer_permission(key, allow_decision(payload, updated))
            self._append_activity("Claude question answered.")
            return
        if tool == "ExitPlanMode":
            plan = str(tool_input.get("plan") or "")
            reviewed, ok = QInputDialog.getMultiLineText(
                self,
                "Review Claude plan",
                "Approve or edit the plan before Claude continues:",
                plan,
            )
            if not ok:
                self._bridge.answer_permission(key, _deny(payload, "Plan was not approved in AI Account Hub."))
                self._append_activity("Claude plan denied.")
                return
            updated = dict(tool_input)
            updated["plan"] = str(reviewed)
            self._bridge.answer_permission(key, allow_decision(payload, updated))
            self._append_activity("Claude plan approved." if reviewed == plan else "Claude plan edited and approved.")
            return
        summary = str(tool_input.get("command") or tool_input.get("description") or tool_input.get("path") or "")
        box = QMessageBox(self)
        box.setWindowTitle("Claude permission request")
        box.setText(f"{tool}\n{summary}".strip())
        allow = box.addButton("Allow once", QMessageBox.AcceptRole)
        box.addButton("Deny", QMessageBox.RejectRole)
        box.exec()
        decision = allow_decision(payload) if box.clickedButton() is allow else _deny(payload, "Denied by user in AI Account Hub.")
        self._bridge.answer_permission(key, decision)
        self._append_activity(f"Claude permission: {'allowed' if decision.get('behavior')=='allow' else 'denied'} ({tool})")

    def _on_error(self, text: str) -> None:
        t = self._tm.tokens
        lab = QLabel("⚠  " + text)
        lab.setWordWrap(True)
        lab.setStyleSheet(f"color:{t['danger']};background:{_soft(t['danger'])};border-radius:8px;padding:6px 9px;")
        self._conv_layout.insertWidget(self._conv_layout.count() - 1, lab)
        self._scroll_bottom()

    def _on_session_ready(self, session_id: str) -> None:
        self._current_session_id = str(session_id or "")
        self._update_session_caption()
        self._render_projects(force=True)

    def _on_rate_limits_changed(self) -> None:
        data.save_profiles(self._profiles)
        self._sync_active()

    def _update_session_caption(self) -> None:
        profile = next((item for item in self._profiles if data.profile_id(item) == self._active_pid), None)
        if profile is None:
            self.session_caption.setText("Native passthrough")
            return
        short_left = data.percent_left(profile.get("shortLimitUsedPercent"))
        limit_text = "5h not exposed" if short_left is None else f"5h {short_left:.0f}% left"
        session = self._current_session_id[:8] if self._current_session_id else "new"
        self.session_caption.setText(
            f"{data.provider_label(profile)} · {limit_text} · session {session}"
        )

    def _open_desktop(self) -> None:
        profile = next((item for item in self._profiles if data.profile_id(item) == self._active_pid), None)
        if profile is None:
            return
        engine = data.engine()
        if data.provider_key(profile) == "codex":
            ok, message = engine.codex_switch_desktop(profile)
        else:
            ok, message = engine.action_desktop(profile)
        self._append_activity(message)
        if not ok:
            self._on_error(message)

    def _show_session_details(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        profile = next((item for item in self._profiles if data.profile_id(item) == self._active_pid), None)
        if profile is None:
            return
        controls = self._composer_state.get(data.profile_id(profile), {})
        weekly = data.percent_left(profile.get("weeklyLimitUsedPercent"))
        session = data.percent_left(profile.get("shortLimitUsedPercent"))
        message = "\n".join(
            (
                f"Account: {profile.get('name', 'Account')}",
                f"Provider: {data.provider_label(profile)}",
                f"Workspace: {profile.get('workspace') or L.DEFAULT_WORKSPACE}",
                f"Session: {self._current_session_id or 'new'}",
                f"Model: {controls.get('model') or 'provider default'}",
                f"Effort: {controls.get('effort') or 'provider default'}",
                f"Access: {controls.get('access') or 'provider default'}",
                f"5h left: {'-' if session is None else f'{session:.0f}%'}",
                f"Weekly left: {'-' if weekly is None else f'{weekly:.0f}%'}",
            )
        )
        QMessageBox.information(self, "Native session details", message)

    def _set_working(self, working: bool) -> None:
        if working:
            self.session_caption.setText("Working · send again to queue")
        else:
            self._update_session_caption()
        self._stop_btn.setEnabled(working)
        self._send_btn.setEnabled(True)
        if not working:
            self._assistant_label = None
            self._assistant_native_id = ""
            # auto-send a queued message once the current turn finishes
            if getattr(self, "_queued", None):
                self._steer_queued()

    def _new_chat(self, reset_bridge: bool = True) -> None:
        self._history_generation += 1
        self._history_pending.clear()
        self._history_total = 0
        if reset_bridge:
            self._bridge.reset_session()
            self._current_session_id = ""
        self._assistant_label = None
        self._assistant_text = ""
        self._assistant_native_id = ""
        self._blocks.clear()
        self._block_cards.clear()
        self._block_expanded.clear()
        self._history_source.clear()
        self._delete_queued()
        self._clear_attachments()
        self._clear_conversation_widgets()
        self._empty_hint = self._make_empty_state()
        self._conv_layout.insertWidget(0, self._empty_hint, 10)
        self.thread_title.setText("New thread")
        self.project_pill.setVisible(False)
        self._update_session_caption()

    def _clear_conversation_widgets(self) -> None:
        while self._conv_layout.count() > 1:
            item = self._conv_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._empty_hint = None

    def tick(self) -> None:
        profile = next((item for item in self._profiles if data.profile_id(item) == self._active_pid), None)
        if profile is not None:
            state = data.account_state(profile)
            self.switch_state.setText(L.status_badge_text(profile, state))
            self.switch_state.set_kind(data.STATE_PILL.get(state, "idle"))
        if not self._bridge.busy:
            self._update_session_caption()

    def apply_theme(self) -> None:
        t = self._tm.tokens
        self.setStyleSheet(f"background:{t['border']};")
        self._rail.setStyleSheet(f"background:{t['panel']};")
        self._center.setStyleSheet(f"background:{t['bg']};")
        self._composer.setStyleSheet(
            f"background:{t['panel2']};border:1px solid {t['borderStrong']};border-radius:14px;"
        )
        # Rebuild the inline-styled centered empty state if it is showing.
        if self._empty_hint is not None:
            self._empty_hint.setParent(None)
            self._empty_hint = self._make_empty_state()
            self._conv_layout.insertWidget(0, self._empty_hint, 10)
        self.project_pill.setStyleSheet(
            f"background:{t['panel2']};color:{t['text2']};border:1px solid {t['border']};"
            f"border-radius:6px;padding:2px 8px;font-size:10px;"
        )
        self._render_projects()
        profile = next((item for item in self._profiles if data.profile_id(item) == self._active_pid), None)
        if profile is not None:
            self._render_provider_controls(profile)

    def close(self) -> None:
        self._bridge.close()

    def _scroll_bottom(self) -> None:
        # Defer so the layout has settled (bar.maximum() is only correct after the
        # new widgets are laid out) — otherwise "scroll to bottom" lands mid-way
        # and the user never sees the latest exchange / their own messages.
        def do() -> None:
            bar = self._conv_scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        QTimer.singleShot(0, do)
