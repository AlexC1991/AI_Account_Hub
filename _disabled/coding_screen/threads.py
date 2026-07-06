"""Data, per-provider composer controls, and project/thread/history rendering
for the Coding screen (mixed into CodingScreen)."""

from __future__ import annotations

import base64
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ai_account_hub import data
from ai_account_hub import core as L
from ai_account_hub.ui.widgets import (
    CyclePill, Dot, ElidedLabel, FolderTag, NetworkLogo, SegmentedControl,
    ToggleSwitch, make_button,
)
from ai_account_hub.ui.screens.coding_screen.helpers import (
    CODING_UI_ENABLED, _relative_time, _soft, _thread_preview,
)


class _ThreadsMixin:
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

