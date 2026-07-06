"""Rich message blocks, native approvals, session details, and lifecycle for
the Coding screen (mixed into CodingScreen)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QTextEdit, QVBoxLayout, QWidget,
)

from ai_account_hub import data
from ai_account_hub import core as L
from ai_account_hub.ui.widgets import (
    ElidedLabel, make_button,
)
from ai_account_hub.ui.screens.coding_screen.helpers import (
    _colorize_diff, _soft, _wrap,
)


class _BlocksMixin:
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
