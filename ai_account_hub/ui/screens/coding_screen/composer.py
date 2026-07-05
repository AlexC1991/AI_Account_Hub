"""Send/stream flow, queued-message controls, skills, and slash palette for
the Coding screen (mixed into CodingScreen)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMenu, QSizePolicy, QWidget,
)

from ai_account_hub import data
from ai_account_hub import core as L


class _ComposerMixin:
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

