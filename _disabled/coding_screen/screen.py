"""Coding view: the persistent 2-column shell. The heavy method groups live in
the threads/composer/blocks mixins; this file owns construction + layout."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from ai_account_hub.ui.widgets import (
    Avatar, StatusPill, make_button,
)
from ai_account_hub.ui.screens.coding_screen.helpers import (
    ComposerInput, _nav_row,
)

from ai_account_hub.ui.screens.coding_screen.threads import _ThreadsMixin
from ai_account_hub.ui.screens.coding_screen.composer import _ComposerMixin
from ai_account_hub.ui.screens.coding_screen.blocks import _BlocksMixin


class CodingScreen(_ThreadsMixin, _ComposerMixin, _BlocksMixin, QWidget):
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

