"""UI rendering for the Coding workbench message stream and inspector.

Extracted from AccountCalendarApp. Holds no state of its own beyond
``self.app`` -- every reference to shared app state (and every call between
two moved methods) goes through ``self.app.X``, matching the same design as
coding_transport_bridge.py (see that module's docstring for the full
rationale: it lets instance-level test monkeypatching keep working, and
avoids a circular import that breaks under the test suite's module loader).
"""

from __future__ import annotations

import hashlib
import math
import re
import webbrowser
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from native_harness import (
    clean_windows_path_text,
    compact_history_text,
)

_HUB_STATIC_NAMES = (
    "AMBER",
    "BLUE",
    "CODING_ACTIVITY_TEXT_LIMIT",
    "CODING_COMMAND_OUTPUT_LIMIT",
    "DARK",
    "GREEN",
    "LINE_STRONG",
    "MUTED",
    "PRIMARY",
    "PRIMARY_HOVER",
    "RED",
    "RED_SOFT",
    "account_plan_label",
    "claude_profile_home",
    "clip_text",
    "coding_command_activity_parts",
    "coding_compact_display_text",
    "coding_display_text",
    "coding_palette",
    "coding_sidebar_thread_preview_limit",
    "coding_user_message_details",
    "format_countdown",
    "format_percent",
    "image_refs_from_transport_text",
    "markdown_image_refs",
    "native_attachment_status_text",
    "native_token_usage_label",
    "percent_left",
    "provider_key",
    "provider_label",
    "status_badge_text",
    "status_colors",
    "status_label",
)
_HUB_LIVE_CONSTANT_NAMES = (
    "DEFAULT_WORKSPACE",
)

_hub_globals: dict = {}


def configure_helpers(hub_globals: dict) -> None:
    """Wire up ai_hub_calendar_gui.py's small helpers.

    Called once by ai_hub_calendar_gui.py immediately after it imports
    CodingUiRenderer from this module, passing its own globals(). See
    coding_transport_bridge.py's module docstring for why this replaces a
    direct circular import.
    """
    missing = (set(_HUB_STATIC_NAMES) | set(_HUB_LIVE_CONSTANT_NAMES)) - set(hub_globals)
    if missing:
        raise RuntimeError(f"configure_helpers missing required names: {sorted(missing)}")
    globals().update({name: hub_globals[name] for name in _HUB_STATIC_NAMES})
    global _hub_globals
    _hub_globals = hub_globals


class CodingUiRenderer:
    """Renders the Coding workbench message stream, sidebar, and inspector
    tabs. Holds no state of its own; all shared state lives on ``self.app``.
    """

    def __init__(self, app) -> None:
        self.app = app

    def set_coding_context_tab(self, tab: str) -> None:
        if tab not in {"session", "skills", "files", "terminal"}:
            return
        self.app.coding_context_tab = tab
        if tab == "skills":
            self.app.refresh_native_skills()
        self.app._render_coding_context()
        self.app._update_coding_details_button()

    def _render_coding(self) -> None:
        if not hasattr(self.app, "coding_project_scroll"):
            return
        self.app._sync_coding_profile_combo()
        self.app._sync_coding_controls()
        choices = self.app.coding_workspace_choices()
        if self.app.coding_workspace_var.get() not in choices:
            self.app.coding_workspace_var.set(choices[0])
        workspace = self.app.coding_workspace_var.get()
        profile = self.app.coding_selected_profile()
        project_name = Path(workspace).name or workspace
        thread_title = self.app.native_thread_title or project_name
        self.app.coding_title.configure(text="New thread" if not self.app.native_thread_id else thread_title)
        if profile is None:
            self.app.coding_subtitle.configure(text=project_name)
        else:
            self.app.coding_subtitle.configure(text=f"{project_name} | {provider_label(profile)} | {profile.get('name', 'Account')}")

        self.app._render_coding_projects()
        self.app._render_coding_sidebar_account()
        self.app._render_coding_stream()
        self.app._render_coding_context()
        if self.app.coding_details_visible:
            self.app.coding_inspector.grid(row=0, column=2, sticky="nsew")
        else:
            self.app.coding_inspector.grid_remove()
        new_thread_state = "normal" if profile is not None and not self.app.busy and not self.app.native_busy else "disabled"
        self.app.coding_new_thread_button.configure(state=new_thread_state)
        self.app.coding_sidebar_new_thread_button.configure(state=new_thread_state)
        self.app.coding_sidebar_accounts_button.configure(text=f"Accounts    {len(self.app.profiles)}")
        send_available = profile is not None and not self.app.busy and not self.app.native_busy
        self.app.coding_send_button.configure(state="normal" if send_available else "disabled")
        self.app.coding_stop_button.configure(state="normal" if self.app.native_busy else "disabled")
        self.app.coding_attach_button.configure(state="normal" if profile is not None and not self.app.native_busy else "disabled")
        controls_state = "disabled" if profile is None or self.app.native_busy else "readonly"
        self.app.coding_model_combo.configure(state=controls_state)
        effort_state = controls_state if len(self.app.coding_effort_options) > 1 else "disabled"
        self.app.coding_effort_combo.configure(state=effort_state)
        self.app.coding_access_combo.configure(state=controls_state)
        self.app._render_native_attachments()
        self.app._render_coding_short_limit()
        if self.app.native_busy:
            self.app.coding_composer_status.configure(text=f"{provider_label(profile)} working" if profile else "Working")
        elif self.app.native_attachments:
            self.app.coding_composer_status.configure(text=native_attachment_status_text(self.app.native_attachments))
        elif self.app.coding_session_active:
            session = self.app.native_thread_id[:8] if self.app.native_thread_id else "new"
            self.app.coding_composer_status.configure(text=f"Native session {session}")
        elif not self.app.native_attachments:
            self.app.coding_composer_status.configure(text="Native passthrough")
        self.app._update_coding_details_button()
        self.app._update_status_context()

    def _render_coding_short_limit(self) -> None:
        if not hasattr(self.app, "coding_short_limit_label"):
            return
        coding = coding_palette(self.app.theme_name)
        profile = self.app.coding_selected_profile()
        left = percent_left(profile.get("shortLimitUsedPercent")) if profile is not None else None
        reset = str(profile.get("shortLimitResetUtc") or "") if profile is not None else ""
        if left is None:
            label = "Not exposed"
            fill = 0
            color = coding["faint"]
        else:
            fill = max(0, min(100, round(left)))
            label = f"{fill}% left"
            if fill <= 0 and reset:
                label = f"0% | {format_countdown(reset)}"
            color = RED if fill <= 10 else AMBER if fill <= 30 else GREEN
        self.app.coding_short_limit_label.configure(text=label, fg=color if left is not None else coding["muted"])
        meter = self.app.coding_short_limit_meter
        meter.delete("all")
        width = max(1, int(meter.cget("width")))
        height = max(1, int(meter.cget("height")))
        meter.create_rectangle(0, 0, width, height, fill=coding["line"], outline="")
        if fill:
            meter.create_rectangle(0, 0, round(width * fill / 100), height, fill=color, outline="")

    def _render_coding_projects(self) -> None:
        if not hasattr(self.app, "coding_project_scroll"):
            return
        coding = coding_palette(self.app.theme_name)
        self.app._clear(self.app.coding_project_scroll.inner)
        term = self.app._coding_search_term()
        workspaces = [
            workspace
            for workspace in self.app.coding_workspace_choices()
            if not term or term in workspace.lower() or term in (Path(workspace).name or workspace).lower()
        ]
        self.app.coding_project_count.configure(text=str(len(workspaces)))
        selected_workspace = self.app.coding_workspace_var.get()
        for workspace in workspaces:
            selected = workspace == selected_workspace
            workspace_path = Path(workspace)
            project_name = workspace_path.name or workspace
            parent_text = str(workspace_path.parent)
            if parent_text in {"", "."}:
                parent_text = workspace
            project_threads = [
                thread
                for thread in self.app.native_threads
                if str(thread.get("cwd") or "").lower() == str(workspace).lower()
            ]
            bg = coding["active"] if selected else coding["panel_alt"]
            hairline = PRIMARY if selected else coding["line"]
            row = tk.Frame(
                self.app.coding_project_scroll.inner,
                bg=bg,
                cursor="hand2",
                highlightbackground=hairline,
                highlightthickness=1,
            )
            row.pack(fill="x", padx=(0, 4), pady=(0, 6))
            row.grid_columnconfigure(2, weight=1)
            tk.Frame(row, bg=PRIMARY if selected else bg, width=3).grid(row=0, column=0, rowspan=99, sticky="nsw")
            tk.Label(
                row,
                text=">" if selected else "",
                bg=bg,
                fg=PRIMARY if selected else coding["faint"],
                font=("Consolas", 9, "bold"),
                width=2,
            ).grid(row=0, column=1, sticky="w", padx=(6, 1), pady=(8, 7))

            title_box = tk.Frame(row, bg=bg)
            title_box.grid(row=0, column=2, sticky="ew", pady=(7, 6))
            title_box.grid_columnconfigure(0, weight=1)
            tk.Label(
                title_box,
                text=clip_text(project_name, 28),
                bg=bg,
                fg=coding["ink"],
                font=("Segoe UI", 9, "bold" if selected else "normal"),
                anchor="w",
            ).grid(row=0, column=0, sticky="ew")
            tk.Label(
                title_box,
                text=clip_text(parent_text, 33),
                bg=bg,
                fg=coding["faint"],
                font=("Segoe UI", 7),
                anchor="w",
            ).grid(row=1, column=0, sticky="ew", pady=(1, 0))
            count_text = "..." if selected and self.app.native_loading_threads else str(len(project_threads))
            count_badge = tk.Label(
                row,
                text=count_text,
                bg=coding["panel"] if selected else coding["field"],
                fg=coding["muted"],
                font=("Segoe UI", 7, "bold"),
                width=3,
                anchor="center",
                padx=3,
                pady=1,
                highlightbackground=coding["line"],
                highlightthickness=1,
            )
            count_badge.grid(row=0, column=3, sticky="ne", padx=(6, 9), pady=(9, 0))
            callback = lambda _event, value=workspace: self.app.select_coding_workspace(value)
            self.app._bind_recursive(row, callback)
            if project_threads:
                threads_box = tk.Frame(row, bg=bg)
                threads_box.grid(row=1, column=2, columnspan=2, sticky="ew", padx=(0, 9), pady=(0, 8))
                threads_box.grid_columnconfigure(0, weight=1)
                expansion_key = self.app._coding_project_thread_expansion_key(workspace)
                expanded = expansion_key in self.app.expanded_coding_project_threads
                visible_thread_count = coding_sidebar_thread_preview_limit(
                    len(project_threads),
                    selected,
                    expanded,
                )
                for index, thread in enumerate(project_threads[:visible_thread_count], start=1):
                    thread_id = str(thread.get("id") or "")
                    thread_selected = bool(thread_id and thread_id == self.app.native_thread_id)
                    thread_bg = coding["panel"] if thread_selected else bg
                    thread_row = tk.Frame(
                        threads_box,
                        bg=thread_bg,
                        cursor="hand2",
                        highlightbackground=coding["line"],
                        highlightthickness=1 if thread_selected else 0,
                    )
                    thread_row.grid(row=index - 1, column=0, sticky="ew", pady=(0, 2))
                    thread_row.grid_columnconfigure(1, weight=1)
                    tk.Frame(
                        thread_row,
                        bg=PRIMARY if thread_selected else coding["line_strong"],
                        width=2,
                    ).grid(row=0, column=0, sticky="nsw", padx=(0, 6))
                    title = str(thread.get("name") or thread.get("preview") or "Native thread").strip().splitlines()[0]
                    tk.Label(
                        thread_row,
                        text=clip_text(title, 28),
                        bg=thread_bg,
                        fg=coding["ink"] if thread_selected else coding["muted"],
                        font=("Segoe UI", 8, "bold" if thread_selected else "normal"),
                        anchor="w",
                    ).grid(row=0, column=1, sticky="ew", pady=4)
                    callback_thread = lambda _event, item=thread: self.app.select_native_thread(item)
                    self.app._bind_recursive(thread_row, callback_thread)
                collapsed_limit = coding_sidebar_thread_preview_limit(
                    len(project_threads),
                    selected,
                    False,
                )
                if len(project_threads) > visible_thread_count or (
                    expanded and len(project_threads) > collapsed_limit
                ):
                    remaining = max(0, len(project_threads) - visible_thread_count)
                    label = "Show less" if expanded else f"Show {remaining} more"
                    more = tk.Button(
                        row,
                        text=label,
                        command=lambda value=workspace: self.app.toggle_coding_project_thread_expansion(value),
                        bg=bg,
                        fg=coding["muted"],
                        activebackground=coding["active"],
                        activeforeground=coding["ink"],
                        relief="flat",
                        bd=0,
                        font=("Segoe UI", 8),
                        anchor="w",
                        padx=7,
                        pady=3,
                        cursor="hand2",
                    )
                    more.grid(
                        row=visible_thread_count,
                        column=0,
                        sticky="ew",
                        pady=(2, 1),
                    )
            elif selected and not self.app.native_loading_threads:
                tk.Label(
                    row,
                    text="No native chats yet",
                    bg=bg,
                    fg=coding["faint"],
                    font=("Segoe UI", 8),
                    anchor="w",
                ).grid(row=1, column=2, columnspan=2, sticky="ew", padx=(0, 9), pady=(0, 9))
        if not workspaces:
            tk.Label(
                self.app.coding_project_scroll.inner,
                text="No matching projects",
                bg=coding["rail"],
                fg=coding["muted"],
                font=("Segoe UI", 9),
                pady=20,
            ).pack(fill="x")

    def _render_coding_sidebar_account(self) -> None:
        if not hasattr(self.app, "coding_sidebar_account"):
            return
        coding = coding_palette(self.app.theme_name)
        self.app._clear(self.app.coding_sidebar_account)
        profile = self.app.coding_selected_profile()
        if profile is None:
            tk.Label(
                self.app.coding_sidebar_account,
                text="No harness account",
                bg=coding["rail"],
                fg=coding["muted"],
                font=("Segoe UI", 8),
                anchor="w",
            ).pack(fill="x", padx=12, pady=12)
            return
        row = tk.Frame(self.app.coding_sidebar_account, bg=coding["rail"])
        row.pack(fill="x", padx=12, pady=10)
        self.app._service_icon(row, provider_key(profile), size=28).pack(side="left", padx=(0, 8))
        text = tk.Frame(row, bg=coding["rail"])
        text.pack(side="left", fill="x", expand=True)
        tk.Label(
            text,
            text=clip_text(str(profile.get("name", "Account")), 24),
            bg=coding["rail"],
            fg=coding["ink"],
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            text,
            text=f"{provider_label(profile)} | {account_plan_label(profile)}",
            bg=coding["rail"],
            fg=coding["muted"],
            font=("Segoe UI", 7),
            anchor="w",
        ).pack(fill="x", pady=(2, 0))
        state = self.app.profile_state(profile)
        color, _soft = status_colors(state)
        tk.Label(row, text=status_label(state), bg=coding["rail"], fg=color, font=("Segoe UI", 7, "bold")).pack(side="right")
        selector = tk.Frame(self.app.coding_sidebar_account, bg=coding["rail"])
        selector.pack(fill="x", padx=12, pady=(0, 10))
        tk.Label(
            selector,
            text="ACTIVE ACCOUNT",
            bg=coding["rail"],
            fg=coding["faint"],
            font=("Segoe UI", 6, "bold"),
            anchor="w",
        ).pack(fill="x", padx=2, pady=(0, 2))
        self.app.coding_profile_combo = ttk.Combobox(
            selector,
            textvariable=self.app.coding_profile_var,
            state="readonly",
            values=list(getattr(self.app, "coding_profile_option_map", {})),
            style="Coding.TCombobox",
        )
        self.app.coding_profile_combo.pack(fill="x")
        self.app.coding_profile_combo.bind("<<ComboboxSelected>>", lambda _event: self.app.on_coding_profile_changed())

    def _configure_coding_stream_tags(self) -> None:
        coding = coding_palette(self.app.theme_name)
        stream = self.app.coding_stream_text
        width = max(720, stream.winfo_width())
        margin = max(42, (width - 820) // 2)
        self.app._coding_stream_margin = margin
        base = {
            "foreground": coding["ink"],
            "font": ("Segoe UI", 10),
            "lmargin1": margin,
            "lmargin2": margin,
            "rmargin": margin,
        }
        stream.tag_configure("assistant", **base, spacing1=2, spacing3=5)
        stream.tag_configure(
            "commentary",
            foreground=coding["muted"],
            font=("Segoe UI", 9, "italic"),
            lmargin1=margin,
            lmargin2=margin,
            rmargin=margin,
            spacing1=3,
            spacing3=6,
        )
        stream.tag_configure(
            "turn_meta",
            foreground=coding["muted"],
            font=("Segoe UI", 8),
            lmargin1=margin,
            lmargin2=margin,
            rmargin=margin,
            spacing1=18,
            spacing3=9,
        )
        stream.tag_configure(
            "activity",
            foreground=coding["faint"],
            font=("Segoe UI", 8),
            lmargin1=margin + 18,
            lmargin2=margin + 18,
            rmargin=margin,
            spacing1=5,
            spacing3=5,
        )
        stream.tag_configure(
            "error",
            foreground=RED,
            background=RED_SOFT,
            font=("Segoe UI", 9),
            lmargin1=margin,
            lmargin2=margin + 14,
            rmargin=margin,
            spacing1=8,
            spacing2=5,
            spacing3=8,
        )
        stream.tag_configure(
            "user_row",
            justify="right",
            lmargin1=margin,
            lmargin2=margin,
            rmargin=margin,
            spacing1=13,
            spacing3=14,
        )
        stream.tag_configure(
            "md_h1",
            foreground=coding["ink"],
            font=("Segoe UI", 16, "bold"),
            lmargin1=margin,
            lmargin2=margin,
            rmargin=margin,
            spacing1=12,
            spacing3=6,
        )
        stream.tag_configure(
            "md_h2",
            foreground=coding["ink"],
            font=("Segoe UI", 13, "bold"),
            lmargin1=margin,
            lmargin2=margin,
            rmargin=margin,
            spacing1=11,
            spacing3=5,
        )
        stream.tag_configure(
            "md_h3",
            foreground=coding["ink"],
            font=("Segoe UI", 11, "bold"),
            lmargin1=margin,
            lmargin2=margin,
            rmargin=margin,
            spacing1=9,
            spacing3=4,
        )
        stream.tag_configure("md_bold", font=("Segoe UI", 10, "bold"))
        stream.tag_configure(
            "md_code_inline",
            foreground=coding["ink"],
            background=coding["active"],
            font=("Consolas", 9),
        )
        stream.tag_configure(
            "md_code",
            foreground=coding["ink"],
            background=coding["composer"],
            font=("Consolas", 9),
            lmargin1=margin + 16,
            lmargin2=margin + 16,
            rmargin=margin + 16,
            spacing1=0,
            spacing2=2,
            spacing3=0,
        )
        stream.tag_configure(
            "md_code_edge",
            foreground=coding["faint"],
            background=coding["composer"],
            font=("Segoe UI", 7),
            lmargin1=margin + 16,
            lmargin2=margin + 16,
            rmargin=margin + 16,
            spacing1=7,
            spacing3=2,
        )
        stream.tag_configure(
            "md_list",
            foreground=coding["ink"],
            font=("Segoe UI", 10),
            lmargin1=margin + 4,
            lmargin2=margin + 24,
            rmargin=margin,
            tabs=(margin + 24,),
            spacing1=1,
            spacing3=2,
        )
        stream.tag_configure(
            "md_quote",
            foreground=coding["muted"],
            font=("Segoe UI", 10, "italic"),
            lmargin1=margin + 18,
            lmargin2=margin + 18,
            rmargin=margin,
            spacing1=4,
            spacing3=4,
        )
        stream.tag_configure("md_link", foreground=BLUE, underline=True)
        stream.tag_configure(
            "empty_title",
            foreground=coding["ink"],
            font=("Segoe UI", 14, "bold"),
            justify="center",
            spacing1=155,
            spacing3=7,
        )
        stream.tag_configure(
            "empty_meta",
            foreground=coding["muted"],
            font=("Segoe UI", 9),
            justify="center",
        )

    def _insert_coding_inline(self, text: str, base_tag: str) -> None:
        stream = self.app.coding_stream_text
        token_pattern = re.compile(r"(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\([^) \n]+(?: [^)]+)?\))")
        cursor = 0
        for match in token_pattern.finditer(text):
            if match.start() > cursor:
                stream.insert("end", text[cursor : match.start()], (base_tag,))
            token = match.group(0)
            if token.startswith("`"):
                stream.insert("end", token[1:-1], (base_tag, "md_code_inline"))
            elif token.startswith("**"):
                stream.insert("end", token[2:-2], (base_tag, "md_bold"))
            else:
                link = re.match(r"\[([^\]]+)\]\(([^)]+)\)", token)
                if link is None:
                    stream.insert("end", token, (base_tag,))
                else:
                    label, target = link.groups()
                    self.app._coding_link_counter += 1
                    link_tag = f"coding_link_{self.app._coding_link_counter}"
                    stream.tag_configure(link_tag, foreground=BLUE, underline=True)
                    stream.tag_bind(link_tag, "<Enter>", lambda _event: stream.configure(cursor="hand2"))
                    stream.tag_bind(link_tag, "<Leave>", lambda _event: stream.configure(cursor="arrow"))
                    stream.tag_bind(link_tag, "<Button-1>", lambda _event, value=target: self.app._open_coding_link(value))
                    stream.insert("end", label, (base_tag, "md_link", link_tag))
            cursor = match.end()
        if cursor < len(text):
            stream.insert("end", text[cursor:], (base_tag,))

    def _insert_coding_markdown(self, value: str, muted: bool = False) -> None:
        stream = self.app.coding_stream_text
        base_tag = "commentary" if muted else "assistant"
        in_code = False
        code_language = ""
        for raw_line in value.replace("\r\n", "\n").split("\n"):
            line_image_refs = markdown_image_refs(raw_line)
            if line_image_refs and not in_code:
                text_without_images = re.sub(r"!\[[^\]]*\]\((<[^>]+>|[^)]+)\)", "", raw_line).strip()
                if text_without_images:
                    self.app._insert_coding_inline(text_without_images, base_tag)
                    stream.insert("end", "\n", (base_tag,))
                for ref in line_image_refs:
                    self.app._insert_coding_image_card(ref)
                continue
            fence = re.match(r"^\s*```(.*)$", raw_line)
            if fence:
                if not in_code:
                    in_code = True
                    code_language = fence.group(1).strip()
                    stream.insert("end", (code_language or "CODE").upper() + "\n", ("md_code_edge",))
                else:
                    stream.insert("end", "\n", ("md_code_edge",))
                    in_code = False
                    code_language = ""
                continue
            if in_code:
                stream.insert("end", raw_line + "\n", ("md_code",))
                continue
            heading = re.match(r"^(#{1,3})\s+(.+)$", raw_line)
            bullet = re.match(r"^\s*[-*+]\s+(.+)$", raw_line)
            numbered = re.match(r"^\s*(\d+)[.)]\s+(.+)$", raw_line)
            quote = re.match(r"^\s*>\s?(.*)$", raw_line)
            if heading:
                tag = f"md_h{len(heading.group(1))}"
                self.app._insert_coding_inline(heading.group(2), tag)
                stream.insert("end", "\n", (tag,))
            elif bullet:
                stream.insert("end", "\u2022\t", ("md_list",))
                self.app._insert_coding_inline(bullet.group(1), "md_list")
                stream.insert("end", "\n", ("md_list",))
            elif numbered:
                stream.insert("end", f"{numbered.group(1)}.\t", ("md_list",))
                self.app._insert_coding_inline(numbered.group(2), "md_list")
                stream.insert("end", "\n", ("md_list",))
            elif quote:
                self.app._insert_coding_inline(quote.group(1), "md_quote")
                stream.insert("end", "\n", ("md_quote",))
            elif re.match(r"^\s*([-*_])\1\1+\s*$", raw_line):
                stream.insert("end", "\n", ("turn_meta",))
            elif raw_line:
                self.app._insert_coding_inline(raw_line, base_tag)
                stream.insert("end", "\n", (base_tag,))
            else:
                stream.insert("end", "\n", (base_tag,))
        if in_code:
            stream.insert("end", "\n", ("md_code_edge",))
        stream.insert("end", "\n", (base_tag,))

    def _open_coding_link(self, target: str) -> None:
        value = target.strip().strip("<>")
        if value.startswith(("https://", "http://")):
            webbrowser.open(value, new=2)
            return
        match = re.match(r"^(.*?)(?::\d+)?$", value)
        path = Path(match.group(1) if match else value)
        if path.exists():
            try:
                os.startfile(str(path))
            except OSError as error:
                self.app.status_var.set(f"Could not open link: {error}")

    def _coding_image_refs(self, message: dict, text: str = "") -> list[dict]:
        refs: list[dict] = []
        raw_refs = message.get("imageRefs") if isinstance(message, dict) else []
        if isinstance(raw_refs, list):
            refs.extend(ref for ref in raw_refs if isinstance(ref, dict))
        refs.extend(image_refs_from_transport_text(text))
        unique: list[dict] = []
        seen: set[str] = set()
        for ref in refs:
            key = str(ref.get("path") or ref.get("url") or ref.get("data") or ref.get("name") or "").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(ref)
        return unique

    def _load_coding_image(self, ref: dict, max_width: int = 520, max_height: int = 260) -> tk.PhotoImage | None:
        image: tk.PhotoImage | None = None
        data = str(ref.get("data") or "").strip()
        path = clean_windows_path_text(ref.get("path"))
        try:
            if data:
                image = tk.PhotoImage(data=data)
            elif path and Path(path).is_file():
                image = tk.PhotoImage(file=path)
        except tk.TclError:
            image = None
        if image is None:
            return None
        factor = max(1, math.ceil(image.width() / max_width), math.ceil(image.height() / max_height))
        if factor > 1:
            image = image.subsample(factor, factor)
        self.app.image_refs.append(image)
        return image

    def _image_ref_label(self, ref: dict) -> str:
        name = str(ref.get("name") or "").strip()
        path = clean_windows_path_text(ref.get("path"))
        url = str(ref.get("url") or "").strip()
        if name:
            return name
        if path:
            return Path(path).name or path
        if url:
            return url
        return "Image"

    def _build_image_preview(self, master: tk.Misc, ref: dict, bg: str, max_width: int = 520, max_height: int = 260) -> tk.Frame:
        coding = coding_palette(self.app.theme_name)
        frame = tk.Frame(master, bg=bg, highlightbackground=coding["line"], highlightthickness=1)
        image = self.app._load_coding_image(ref, max_width=max_width, max_height=max_height)
        if image is not None:
            tk.Label(frame, image=image, bg=bg, bd=0).pack(fill="x", padx=6, pady=(6, 3))
        info = tk.Frame(frame, bg=bg)
        info.pack(fill="x", padx=7, pady=(3, 6))
        tk.Label(
            info,
            text=clip_text(self.app._image_ref_label(ref), 60),
            bg=bg,
            fg=coding["ink"],
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        target = clean_windows_path_text(ref.get("path")) or str(ref.get("url") or "")
        if target:
            tk.Button(
                info,
                text="Open",
                command=lambda value=target: self.app._open_coding_link(value),
                bg=coding["panel"],
                fg=coding["ink"],
                activebackground=coding["active"],
                activeforeground=coding["ink"],
                relief="solid",
                bd=1,
                font=("Segoe UI", 7, "bold"),
                padx=6,
                pady=2,
            ).pack(side="right")
        return frame

    def _insert_coding_image_card(self, ref: dict, title: str = "Image") -> None:
        coding = coding_palette(self.app.theme_name)
        stream = self.app.coding_stream_text
        card = tk.Frame(
            stream,
            bg=coding["panel"],
            highlightbackground=coding["line"],
            highlightthickness=1,
            padx=10,
            pady=9,
        )
        tk.Label(card, text=title, bg=coding["panel"], fg=coding["muted"], font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x")
        self.app._build_image_preview(card, ref, coding["panel"], max_width=500, max_height=240).pack(fill="x", pady=(6, 0))
        stream.window_create("end", window=card)
        stream.insert("end", "\n\n", ("activity",))

    def _insert_coding_user_message(self, value: str, message: dict | None = None) -> None:
        coding = coding_palette(self.app.theme_name)
        stream = self.app.coding_stream_text
        text, attachments = coding_user_message_details(value)
        if message:
            existing = {str(item.get("path") or item.get("url") or item.get("data") or item.get("name") or "").lower() for item in attachments}
            for ref in self.app._coding_image_refs(message, ""):
                key = str(ref.get("path") or ref.get("url") or ref.get("data") or ref.get("name") or "").lower()
                if key and key not in existing:
                    attachments.append(ref)
                    existing.add(key)
        bubble = tk.Frame(
            stream,
            bg=coding["active"],
            highlightbackground=coding["line"],
            highlightthickness=1,
            padx=13,
            pady=10,
        )
        for attachment in attachments[:6]:
            name = self.app._image_ref_label(attachment)
            chip = tk.Frame(bubble, bg=coding["panel"], highlightbackground=coding["line"], highlightthickness=1)
            chip.pack(fill="x", pady=(0, 6))
            tk.Label(
                chip,
                text="+",
                bg=coding["panel"],
                fg=coding["muted"],
                font=("Segoe UI", 8, "bold"),
                width=2,
            ).pack(side="left", padx=(5, 1), pady=4)
            tk.Label(
                chip,
                text=clip_text(name, 56),
                bg=coding["panel"],
                fg=coding["ink"],
                font=("Segoe UI", 8),
                anchor="w",
            ).pack(side="left", fill="x", expand=True, padx=(0, 8), pady=4)
            if clean_windows_path_text(attachment.get("path")) or attachment.get("data"):
                self.app._build_image_preview(bubble, attachment, coding["active"], max_width=420, max_height=160).pack(fill="x", pady=(0, 6))
        if text:
            display = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
            display = re.sub(r"`([^`\n]+)`", r"\1", display)
            tk.Label(
                bubble,
                text=display,
                bg=coding["active"],
                fg=coding["ink"],
                font=("Segoe UI", 10),
                wraplength=610,
                justify="left",
                anchor="w",
            ).pack(fill="x")
        start = stream.index("end-1c")
        stream.window_create("end", window=bubble, align="center")
        stream.insert("end", "\n\n", ("user_row",))
        stream.tag_add("user_row", start, "end-1c")

    def _activity_kind_from_text(self, value: str) -> str:
        first_line = next((line.strip().lower() for line in value.splitlines() if line.strip()), "")
        if first_line == "plan":
            return "plan"
        if first_line.startswith("diff --git") or first_line == "current diff":
            return "diff"
        if first_line.startswith("viewed image"):
            return "image"
        if "command" in first_line:
            return "command"
        return "activity"

    def _insert_diff_text(self, widget: tk.Text, value: str) -> None:
        widget.tag_configure("diff_add", foreground=GREEN)
        widget.tag_configure("diff_del", foreground=RED)
        widget.tag_configure("diff_meta", foreground=BLUE)
        for line in coding_display_text(value).splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                tag = "diff_add"
            elif line.startswith("-") and not line.startswith("---"):
                tag = "diff_del"
            elif line.startswith(("diff --git", "@@", "+++", "---")):
                tag = "diff_meta"
            else:
                tag = "md_code"
            widget.insert("end", line + "\n", (tag,))

    def _insert_plan_rows(self, master: tk.Misc, message: dict, value: str, bg: str) -> bool:
        coding = coding_palette(self.app.theme_name)
        plan_rows: list[tuple[str, str]] = []
        plan = message.get("plan") if isinstance(message.get("plan"), list) else []
        for entry in plan:
            if not isinstance(entry, dict):
                continue
            step = str(entry.get("step") or entry.get("text") or "").strip()
            status = str(entry.get("status") or "pending").strip()
            if step:
                plan_rows.append((status, step))
        if not plan_rows:
            for line in value.splitlines():
                match = re.match(r"\[(done|active|todo|pending|completed|inProgress|in_progress)\]\s+(.+)", line.strip())
                if match:
                    plan_rows.append((match.group(1), match.group(2)))
        if not plan_rows:
            for raw_line in value.splitlines():
                line = raw_line.strip()
                if not line or line.lower() == "plan" or line.startswith("#"):
                    continue
                checkbox = re.match(r"(?:[-*+]|\d+[.)])\s+\[([ xX])\]\s+(.+)", line)
                ordered = re.match(r"(?:\d+[.)]|[-*+])\s+(.+)", line)
                if checkbox:
                    plan_rows.append(("done" if checkbox.group(1).lower() == "x" else "todo", checkbox.group(2)))
                elif ordered:
                    text = ordered.group(1).strip()
                    if text and not text.lower().startswith(("context", "steps")):
                        plan_rows.append(("todo", text))
        if not plan_rows:
            return False
        for status, step in plan_rows[:8]:
            normalized = {"completed": "done", "inProgress": "active", "in_progress": "active", "pending": "todo"}.get(status, status)
            color = GREEN if normalized == "done" else BLUE if normalized == "active" else coding["muted"]
            row = tk.Frame(master, bg=bg)
            row.pack(fill="x", pady=1)
            tk.Label(row, text="OK" if normalized == "done" else ">", bg=bg, fg=color, font=("Segoe UI", 7, "bold"), width=3).pack(side="left")
            tk.Label(row, text=clip_text(step, 82), bg=bg, fg=coding["ink"], font=("Segoe UI", 8), anchor="w").pack(side="left", fill="x", expand=True)
        if len(plan_rows) > 8:
            tk.Label(master, text=f"+{len(plan_rows) - 8} more steps", bg=bg, fg=coding["muted"], font=("Segoe UI", 7)).pack(anchor="w", pady=(2, 0))
        return True

    def _insert_coding_activity_card(self, message: dict, value: str) -> None:
        coding = coding_palette(self.app.theme_name)
        stream = self.app.coding_stream_text
        value = coding_display_text(value)
        kind = str(message.get("kind") or self.app._activity_kind_from_text(value))
        first_line = next((line.strip() for line in value.splitlines() if line.strip()), "Activity")
        title = str(message.get("title") or first_line or "Activity")
        status = str(message.get("status") or "").strip()
        card = tk.Frame(
            stream,
            bg=coding["panel"],
            highlightbackground=coding["line"],
            highlightthickness=1,
            padx=10,
            pady=9,
        )
        header = tk.Frame(card, bg=coding["panel"])
        header.pack(fill="x")
        accent = {
            "diff": BLUE,
            "file_change": BLUE,
            "command": GREEN,
            "tool": AMBER,
            "result": coding["muted"],
            "plan": PRIMARY,
            "image": BLUE,
            "reasoning": coding["muted"],
            "notice": coding["muted"],
        }.get(kind, coding["muted"])
        tk.Frame(header, bg=accent, width=4, height=18).pack(side="left", padx=(0, 8))
        tk.Label(header, text=clip_text(title, 58), bg=coding["panel"], fg=coding["ink"], font=("Segoe UI", 8, "bold"), anchor="w").pack(side="left", fill="x", expand=True)
        if status:
            tk.Label(header, text=clip_text(status, 18), bg=coding["active"], fg=coding["muted"], font=("Segoe UI", 7, "bold"), padx=6, pady=2).pack(side="right")
        if kind in {"diff", "file_change"}:
            diff = str(message.get("diff") or self.app.native_turn_diff or value).strip()
            changes = message.get("changes") if isinstance(message.get("changes"), list) else []
            if changes:
                path_line = ", ".join(clip_text(str(change.get("path") or ""), 32) for change in changes[:3] if isinstance(change, dict))
                if path_line:
                    tk.Label(card, text=path_line, bg=coding["panel"], fg=coding["muted"], font=("Segoe UI", 7), anchor="w").pack(fill="x", pady=(5, 0))
            if diff:
                diff_box = tk.Text(
                    card,
                    height=min(12, max(4, diff.count("\n") + 1)),
                    bg=coding["composer"],
                    fg=coding["ink"],
                    insertbackground=coding["ink"],
                    relief="flat",
                    font=("Consolas", 7),
                    wrap="none",
                    padx=7,
                    pady=6,
                )
                diff_box.pack(fill="x", pady=(7, 0))
                self.app._insert_diff_text(diff_box, compact_history_text(diff, limit=7000))
                diff_box.configure(state="disabled")
            tk.Button(
                card,
                text="Open Files View",
                command=lambda: self.app.set_coding_context_tab("files"),
                bg=coding["active"],
                fg=coding["ink"],
                activebackground=coding["composer"],
                activeforeground=coding["ink"],
                relief="solid",
                bd=1,
                font=("Segoe UI", 7, "bold"),
                padx=7,
                pady=3,
            ).pack(anchor="e", pady=(7, 0))
        elif kind == "command":
            command, details, output = coding_command_activity_parts(value)
            if command:
                tk.Label(
                    card,
                    text=clip_text(command, 130),
                    bg=coding["panel"],
                    fg=coding["ink"],
                    font=("Consolas", 7),
                    wraplength=620,
                    justify="left",
                    anchor="w",
                ).pack(fill="x", pady=(7, 0))
            if details:
                tk.Label(
                    card,
                    text=clip_text(details, 90),
                    bg=coding["panel"],
                    fg=coding["muted"],
                    font=("Segoe UI", 7),
                    anchor="w",
                ).pack(fill="x", pady=(3, 0))
            if output:
                output = coding_compact_display_text(output, limit=CODING_COMMAND_OUTPUT_LIMIT)
                output_box = tk.Text(
                    card,
                    height=min(8, max(2, output.count("\n") + 1)),
                    bg=coding["composer"],
                    fg=coding["ink"],
                    insertbackground=coding["ink"],
                    relief="flat",
                    font=("Consolas", 7),
                    wrap="char",
                    padx=7,
                    pady=6,
                )
                output_box.pack(fill="x", pady=(7, 0))
                output_box.insert("1.0", output)
                output_box.configure(state="disabled")
        elif kind == "plan":
            if not self.app._insert_plan_rows(card, message, value, coding["panel"]):
                tk.Label(card, text=clip_text(coding_display_text(value), 180), bg=coding["panel"], fg=coding["muted"], font=("Segoe UI", 8), wraplength=620, justify="left", anchor="w").pack(fill="x", pady=(7, 0))
        elif kind == "image":
            refs = self.app._coding_image_refs(message, value)
            if refs:
                for ref in refs[:4]:
                    self.app._build_image_preview(card, ref, coding["panel"], max_width=500, max_height=220).pack(fill="x", pady=(7, 0))
            else:
                tk.Label(card, text=clip_text(coding_display_text(value), 180), bg=coding["panel"], fg=coding["muted"], font=("Segoe UI", 8), wraplength=620, justify="left", anchor="w").pack(fill="x", pady=(7, 0))
        else:
            body = value
            if "\n" in body:
                lines = [line.strip() for line in body.splitlines() if line.strip()]
                if lines and lines[0] == title:
                    body = "\n".join(lines[1:])
            if body.strip():
                tk.Label(card, text=coding_compact_display_text(body, limit=CODING_ACTIVITY_TEXT_LIMIT), bg=coding["panel"], fg=coding["muted"], font=("Segoe UI", 8), wraplength=620, justify="left", anchor="w").pack(fill="x", pady=(7, 0))
        stream.window_create("end", window=card)
        stream.insert("end", "\n\n", ("activity",))

    def _plain_coding_activity_text(self, message: dict | None, value: str) -> str:
        value = coding_display_text(value).strip()
        if not message:
            return value
        kind = str(message.get("kind") or self.app._activity_kind_from_text(value))
        title = str(message.get("title") or "").strip()
        status = str(message.get("status") or "").strip()
        heading = title or kind.replace("_", " ").title() or "Activity"
        suffix = f" [{status}]" if status else ""
        if kind in {"diff", "file_change"}:
            changes = message.get("changes") if isinstance(message.get("changes"), list) else []
            paths = ", ".join(
                clip_text(str(change.get("path") or ""), 42)
                for change in changes[:4]
                if isinstance(change, dict) and str(change.get("path") or "").strip()
            )
            diff = str(message.get("diff") or self.app.native_turn_diff or "").strip()
            body = compact_history_text(diff, limit=1600) if diff else value
            return "\n".join(part for part in [f"{heading}{suffix}", paths, body] if part).strip()
        if kind == "image":
            refs = self.app._coding_image_refs(message, value)
            labels = ", ".join(clip_text(self.app._image_ref_label(ref), 44) for ref in refs[:4])
            return "\n".join(part for part in [f"{heading}{suffix}", labels or value] if part).strip()
        if kind == "plan":
            return "\n".join(part for part in [f"{heading}{suffix}", value] if part).strip()
        if value.lower().startswith(heading.lower()):
            return value
        return "\n".join(part for part in [f"{heading}{suffix}", value] if part).strip()

    def _insert_coding_activity(self, role: str, value: str, message: dict | None = None) -> None:
        stream = self.app.coding_stream_text
        value = coding_display_text(value)
        if role == "turn_meta":
            stream.insert("end", f"{value}  >\n", ("turn_meta",))
            return
        if role == "error":
            stream.insert("end", f"  {value.strip()}  \n", ("error",))
            return
        if message and self.app._coding_message_uses_windows(message, value):
            self.app._insert_coding_activity_card(message, value)
            return
        if message:
            body = self.app._plain_coding_activity_text(message, value)
            if body:
                stream.insert("end", f">  {coding_compact_display_text(body, limit=2200)}\n", ("activity",))
                return
        tool_notice = re.match(r"Tool activity compacted:\s+([\d,]+)\s+calls/results", value)
        history_notice = re.match(r"History compacted:\s+([\d,]+)\s+older messages", value)
        if tool_notice:
            summary = f"Tool activity hidden  |  {tool_notice.group(1)} calls and results"
        elif history_notice:
            summary = f"Earlier history hidden  |  {history_notice.group(1)} messages"
        else:
            first_line = next((line.strip() for line in value.splitlines() if line.strip()), "Activity")
            summary = clip_text(first_line, 120)
        stream.insert("end", f">  {summary}\n", ("activity",))

    def _coding_stream_message_key(self, index: int, message: dict) -> str:
        native_id = str(message.get("nativeId") or message.get("timestamp") or index)
        digest = hashlib.sha1(native_id.encode("utf-8", errors="replace")).hexdigest()[:10]
        return f"msg_{index}_{digest}"

    def _coding_message_uses_windows(self, message: dict, text: str) -> bool:
        role = str(message.get("role") or "activity")
        if role == "user":
            return True
        if role == "assistant":
            return bool(self.app._coding_image_refs(message, text) or markdown_image_refs(text))
        if role == "activity" and self.app.native_busy:
            return False
        return bool(message.get("kind") or message.get("diff") or message.get("imageRefs") or message.get("changes"))

    def _coding_stream_state(self) -> tuple[tuple[str, ...], dict[str, str], dict[str, int]]:
        signature: list[str] = []
        texts: dict[str, str] = {}
        indexes: dict[str, int] = {}
        for index, message in enumerate(self.app.native_messages):
            role = str(message.get("role") or "activity")
            text = coding_display_text(message.get("text") or "")
            key = self.app._coding_stream_message_key(index, message)
            uses_windows = self.app._coding_message_uses_windows(message, text)
            signature.append(f"{key}|{role}|{message.get('kind') or ''}|{uses_windows}")
            texts[key] = text
            indexes[key] = index
        return tuple(signature), texts, indexes

    def _try_update_coding_stream_delta(self) -> bool:
        if not self.app._coding_stream_signature or not self.app._coding_stream_message_ranges:
            return False
        signature, texts, indexes = self.app._coding_stream_state()
        if signature != self.app._coding_stream_signature:
            return False
        changed = [
            key
            for key, text in texts.items()
            if text != getattr(self.app, "_coding_stream_last_texts", {}).get(key)
        ]
        if len(changed) != 1:
            return False
        key = changed[0]
        index = indexes.get(key)
        if index is None or index >= len(self.app.native_messages):
            return False
        message = self.app.native_messages[index]
        text = texts[key]
        role = str(message.get("role") or "")
        if role not in {"assistant", "activity"} or self.app._coding_message_uses_windows(message, text):
            return False
        marks = self.app._coding_stream_message_ranges.get(key)
        if marks is None:
            return False
        stream = self.app.coding_stream_text
        start_mark, end_mark = marks
        try:
            stream.configure(state="normal")
            stream.delete(start_mark, end_mark)
            if role == "activity":
                body = self.app._plain_coding_activity_text(message, text)
                rendered = f">  {coding_compact_display_text(body, limit=2200)}\n"
                tags = ("activity",)
            else:
                rendered = text.rstrip() + "\n\n"
                tags = ("commentary" if message.get("muted") else "assistant",)
            stream.insert(start_mark, rendered, tags)
            stream.mark_set(end_mark, f"{start_mark}+{len(rendered)}c")
            stream.configure(state="disabled")
            self.app._coding_stream_last_texts = texts
            if self.app._coding_scroll_after_id:
                try:
                    self.app.after_cancel(self.app._coding_scroll_after_id)
                except tk.TclError:
                    pass
            self.app._coding_scroll_after_id = self.app.after(60, self.app._scroll_coding_to_bottom)
            return True
        except tk.TclError:
            try:
                stream.configure(state="disabled")
            except tk.TclError:
                pass
            return False

    def _try_append_coding_stream_messages(self) -> bool:
        if not self.app._coding_stream_signature:
            return False
        signature, texts, _indexes = self.app._coding_stream_state()
        old_count = len(self.app._coding_stream_signature)
        if len(signature) <= old_count or tuple(signature[:old_count]) != self.app._coding_stream_signature:
            return False
        appended_indexes = range(old_count, len(signature))
        for index in appended_indexes:
            if index >= len(self.app.native_messages):
                return False
            message = self.app.native_messages[index]
            text = texts.get(self.app._coding_stream_message_key(index, message), "")
            if str(message.get("role") or "") == "user" or self.app._coding_message_uses_windows(message, text):
                return False

        stream = self.app.coding_stream_text
        try:
            stream.configure(state="normal")
            for index in appended_indexes:
                message = self.app.native_messages[index]
                role = str(message.get("role") or "activity")
                text = coding_display_text(message.get("text") or "")
                if len(text) > 14000:
                    text = compact_history_text(text, limit=14000)
                if not text:
                    continue
                key = self.app._coding_stream_message_key(index, message)
                start_mark = f"{key}_start"
                end_mark = f"{key}_end"
                stream.mark_set(start_mark, "end-1c")
                stream.mark_gravity(start_mark, "left")
                if role == "assistant":
                    self.app._insert_coding_markdown(text, muted=bool(message.get("muted")))
                else:
                    self.app._insert_coding_activity(role, text, message=message)
                stream.mark_set(end_mark, "end-1c")
                stream.mark_gravity(end_mark, "right")
                self.app._coding_stream_message_ranges[key] = (start_mark, end_mark)
            stream.configure(state="disabled")
            self.app._coding_stream_signature = signature
            self.app._coding_stream_last_texts = texts
            if self.app._coding_scroll_after_id:
                try:
                    self.app.after_cancel(self.app._coding_scroll_after_id)
                except tk.TclError:
                    pass
            self.app._coding_scroll_after_id = self.app.after(60, self.app._scroll_coding_to_bottom)
            return True
        except tk.TclError:
            try:
                stream.configure(state="disabled")
            except tk.TclError:
                pass
            return False

    def _render_coding_stream(self) -> None:
        if not hasattr(self.app, "coding_stream_text"):
            return
        if self.app._try_update_coding_stream_delta():
            return
        if self.app._try_append_coding_stream_messages():
            return
        stream = self.app.coding_stream_text
        stream.configure(state="normal")
        for child in stream.winfo_children():
            child.destroy()
        self.app.image_refs = []
        self.app._coding_stream_message_ranges = {}
        stream.delete("1.0", "end")
        self.app._coding_link_counter = 0
        self.app._configure_coding_stream_tags()
        if not self.app.native_messages:
            profile = self.app.coding_selected_profile()
            workspace = Path(self.app.coding_workspace_var.get() or _hub_globals["DEFAULT_WORKSPACE"]).name
            account = str(profile.get("name", "Account")) if profile is not None else "No harness account"
            stream.insert("end", "New coding thread\n", ("empty_title",))
            stream.insert("end", f"{workspace}  |  {account}\n", ("empty_meta",))
            stream.configure(state="disabled")
            self.app._coding_stream_signature = ()
            self.app._coding_stream_last_texts = {}
            return

        stream.insert("end", "\n", ("assistant",))
        for index, message in enumerate(self.app.native_messages):
            role = str(message.get("role") or "activity")
            text = coding_display_text(message.get("text") or "")
            if len(text) > 14000:
                text = compact_history_text(text, limit=14000)
            if not text:
                continue
            if role == "user":
                self.app._insert_coding_user_message(text, message=message)
            elif role == "assistant":
                key = self.app._coding_stream_message_key(index, message)
                if not self.app._coding_message_uses_windows(message, text):
                    start_mark = f"{key}_start"
                    end_mark = f"{key}_end"
                    stream.mark_set(start_mark, "end-1c")
                    stream.mark_gravity(start_mark, "left")
                self.app._insert_coding_markdown(text, muted=bool(message.get("muted")))
                for ref in self.app._coding_image_refs(message, ""):
                    self.app._insert_coding_image_card(ref)
                if not self.app._coding_message_uses_windows(message, text):
                    stream.mark_set(end_mark, "end-1c")
                    stream.mark_gravity(end_mark, "right")
                    self.app._coding_stream_message_ranges[key] = (start_mark, end_mark)
            else:
                key = self.app._coding_stream_message_key(index, message)
                if role == "activity" and not self.app._coding_message_uses_windows(message, text):
                    start_mark = f"{key}_start"
                    end_mark = f"{key}_end"
                    stream.mark_set(start_mark, "end-1c")
                    stream.mark_gravity(start_mark, "left")
                self.app._insert_coding_activity(role, text, message=message)
                if role == "activity" and not self.app._coding_message_uses_windows(message, text):
                    stream.mark_set(end_mark, "end-1c")
                    stream.mark_gravity(end_mark, "right")
                    self.app._coding_stream_message_ranges[key] = (start_mark, end_mark)
        stream.insert("end", "\n", ("assistant",))
        stream.configure(state="disabled")
        signature, texts, _indexes = self.app._coding_stream_state()
        self.app._coding_stream_signature = signature
        self.app._coding_stream_last_texts = texts
        if self.app._coding_scroll_after_id:
            try:
                self.app.after_cancel(self.app._coding_scroll_after_id)
            except tk.TclError:
                pass
        self.app._coding_scroll_after_id = self.app.after(60, self.app._scroll_coding_to_bottom)

    def _scroll_coding_to_bottom(self) -> None:
        self.app._coding_scroll_after_id = None
        if self.app._closing:
            return
        try:
            self.app.coding_stream_text.yview_moveto(1.0)
        except tk.TclError:
            pass

    def _render_coding_context(self) -> None:
        coding = coding_palette(self.app.theme_name)
        for tab, button in getattr(self.app, "coding_context_buttons", {}).items():
            selected = tab == self.app.coding_context_tab
            button.configure(
                bg=PRIMARY if selected else coding["panel"],
                fg="white" if selected else coding["ink"],
                activebackground=PRIMARY_HOVER if selected else coding["active"],
                activeforeground="white" if selected else coding["ink"],
                relief="flat" if selected else "solid",
            )
        if not hasattr(self.app, "coding_context_scroll"):
            return
        body = self.app.coding_context_scroll.inner
        self.app._clear(body)
        if self.app.coding_context_tab == "files":
            self.app._render_coding_files(body)
        elif self.app.coding_context_tab == "skills":
            self.app._render_coding_skills(body)
        elif self.app.coding_context_tab == "terminal":
            self.app._render_coding_terminal(body)
        else:
            self.app._render_coding_session(body)

    def _render_coding_session(self, master: tk.Misc) -> None:
        coding = coding_palette(self.app.theme_name)
        profile = self.app.coding_selected_profile()
        tk.Label(master, text="Session", bg=coding["rail"], fg=coding["ink"], font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x", pady=(2, 10))
        if profile is None:
            tk.Label(master, text="No harness account", bg=coding["rail"], fg=coding["muted"], font=("Segoe UI", 9)).pack(anchor="w")
            return
        head = tk.Frame(master, bg=coding["panel"], highlightbackground=coding["line"], highlightthickness=1, padx=10, pady=10)
        head.pack(fill="x", pady=(0, 8))
        self.app._service_icon(head, provider_key(profile), size=34).pack(side="left", padx=(0, 9))
        identity = tk.Frame(head, bg=coding["panel"])
        identity.pack(side="left", fill="x", expand=True)
        tk.Label(identity, text=str(profile.get("name", "Account")), bg=coding["panel"], fg=coding["ink"], font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(identity, text=provider_label(profile), bg=coding["panel"], fg=coding["muted"], font=("Segoe UI", 8)).pack(anchor="w", pady=(2, 0))
        state = self.app.profile_state(profile)
        self.app._status_badge(head, status_badge_text(profile, state), state).pack(side="right")

        weekly_left = percent_left(profile.get("weeklyLimitUsedPercent"))
        short_left = percent_left(profile.get("shortLimitUsedPercent"))
        provider = provider_key(profile)
        transport_state = "Running" if self.app.native_busy else "Connected" if self.app.coding_session_active else "Idle"
        if provider == "cursor" and not self.app.cursor_agent_path:
            transport_state = "Agent CLI missing"
        elif provider == "antigravity" and (
            not self.app.antigravity_cli_path or Path(self.app.antigravity_cli_path).name.lower() == "agy-node.cmd"
        ):
            transport_state = "Desktop fallback"
        controls = self.app.coding_control_values()
        fields = [
            ("Plan", account_plan_label(profile)),
            ("Model", controls["model"] or "Provider default"),
            ("Effort", controls["effort"] or "Model default"),
            ("Style", self.app.coding_personality_var.get() or controls["personality"] or "Provider default"),
            ("Access", self.app.coding_access_var.get() or controls["access"]),
            ("Weekly left", format_percent(weekly_left)),
            ("Session left", format_percent(short_left)),
            ("Transport", transport_state),
            ("Session", self.app.native_thread_id[:12] if self.app.native_thread_id else "-"),
            ("Tokens", native_token_usage_label(self.app.native_token_usage)),
            ("Project", clip_text(Path(self.app.coding_workspace_var.get()).name, 24)),
            ("History", "Native provider"),
        ]
        for label, value in fields:
            row = tk.Frame(master, bg=coding["panel"], highlightbackground=coding["line"], highlightthickness=1)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, bg=coding["panel"], fg=coding["muted"], font=("Segoe UI", 8), anchor="w").pack(side="left", padx=9, pady=7)
            tk.Label(row, text=value, bg=coding["panel"], fg=coding["ink"], font=("Segoe UI", 8, "bold"), anchor="e").pack(side="right", padx=9, pady=7)

        home = str(claude_profile_home(profile)) if provider == "claude" else str(profile.get("codexHome") or "-")
        tk.Label(master, text="Native home", bg=coding["rail"], fg=coding["muted"], font=("Segoe UI", 7, "bold"), anchor="w").pack(fill="x", pady=(10, 3))
        tk.Label(master, text=home, bg=coding["rail"], fg=coding["ink"], font=("Consolas", 7), wraplength=285, justify="left", anchor="w").pack(fill="x")

    def _native_skill_rows(self) -> tuple[list[dict], list[str]]:
        rows: list[dict] = []
        errors: list[str] = []
        for entry in self.app.native_skills:
            if not isinstance(entry, dict):
                continue
            cwd = str(entry.get("cwd") or "")
            for error in entry.get("errors") or []:
                if isinstance(error, dict):
                    message = str(error.get("message") or "").strip()
                    path = clean_windows_path_text(error.get("path"))
                    if message:
                        errors.append(f"{clip_text(path, 34)}: {message}" if path else message)
            for skill in entry.get("skills") or []:
                if isinstance(skill, dict):
                    row = dict(skill)
                    row["_cwd"] = cwd
                    rows.append(row)
        rows.sort(key=lambda item: (str(item.get("scope") or ""), str(item.get("name") or "").lower()))
        return rows, errors

    def _render_coding_skills(self, master: tk.Misc) -> None:
        coding = coding_palette(self.app.theme_name)
        profile = self.app.coding_selected_profile()
        header = tk.Frame(master, bg=coding["rail"])
        header.pack(fill="x", pady=(2, 10))
        tk.Label(header, text="Skills", bg=coding["rail"], fg=coding["ink"], font=("Segoe UI", 11, "bold"), anchor="w").pack(side="left")
        refresh = tk.Button(
            header,
            text="Refresh",
            command=lambda: self.app.refresh_native_skills(force=True),
            bg=coding["panel"],
            fg=coding["ink"],
            activebackground=coding["active"],
            activeforeground=coding["ink"],
            relief="solid",
            bd=1,
            font=("Segoe UI", 7, "bold"),
            padx=7,
            pady=3,
            cursor="hand2",
        )
        refresh.pack(side="right")
        if profile is None:
            tk.Label(master, text="No harness account", bg=coding["rail"], fg=coding["muted"], font=("Segoe UI", 9)).pack(anchor="w")
            return
        if provider_key(profile) != "codex":
            text = self.app.native_skills_error or f"{provider_label(profile)} does not expose Codex app-server skills."
            tk.Label(master, text=text, bg=coding["rail"], fg=coding["muted"], font=("Segoe UI", 8), wraplength=285, justify="left", anchor="w").pack(fill="x")
            return
        if self.app.native_skills_loading:
            tk.Label(master, text="Loading skills...", bg=coding["rail"], fg=coding["muted"], font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(0, 8))
        if self.app.native_skills_error:
            tk.Label(master, text=self.app.native_skills_error, bg=coding["rail"], fg=RED, font=("Segoe UI", 8), wraplength=285, justify="left", anchor="w").pack(fill="x", pady=(0, 8))

        rows, errors = self.app._native_skill_rows()
        enabled = sum(1 for row in rows if bool(row.get("enabled")))
        summary = f"{enabled}/{len(rows)} enabled" if rows else "No skills reported"
        tk.Label(master, text=summary, bg=coding["rail"], fg=coding["muted"], font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(0, 8))
        for error in errors[:4]:
            tk.Label(master, text=clip_text(error, 120), bg=coding["rail"], fg=AMBER, font=("Segoe UI", 8), wraplength=285, justify="left", anchor="w").pack(fill="x", pady=(0, 4))

        for skill in rows[:80]:
            skill_enabled = bool(skill.get("enabled"))
            interface = skill.get("interface") if isinstance(skill.get("interface"), dict) else {}
            name = str(interface.get("displayName") or skill.get("name") or "Skill").strip()
            scope = str(skill.get("scope") or "").strip().title() or "Skill"
            description = str(interface.get("shortDescription") or skill.get("shortDescription") or skill.get("description") or "").strip()
            path = clean_windows_path_text(skill.get("path"))
            card = tk.Frame(master, bg=coding["panel"], highlightbackground=coding["line"], highlightthickness=1, padx=9, pady=8)
            card.pack(fill="x", pady=3)
            top = tk.Frame(card, bg=coding["panel"])
            top.pack(fill="x")
            tk.Label(top, text=clip_text(name, 31), bg=coding["panel"], fg=coding["ink"], font=("Segoe UI", 8, "bold"), anchor="w").pack(side="left", fill="x", expand=True)
            self.app._status_badge(top, "Enabled" if skill_enabled else "Disabled", "ready" if skill_enabled else "idle").pack(side="right")
            meta = f"{scope}"
            if path:
                meta = f"{meta} | {clip_text(path, 46)}"
            tk.Label(card, text=meta, bg=coding["panel"], fg=coding["muted"], font=("Segoe UI", 7), wraplength=260, justify="left", anchor="w").pack(fill="x", pady=(4, 0))
            if description:
                tk.Label(card, text=clip_text(description, 150), bg=coding["panel"], fg=coding["muted"], font=("Segoe UI", 8), wraplength=260, justify="left", anchor="w").pack(fill="x", pady=(5, 0))
            tk.Button(
                card,
                text="Disable" if skill_enabled else "Enable",
                command=lambda item=skill, state=not skill_enabled: self.app.set_native_skill_enabled(item, state),
                bg=coding["active"],
                fg=coding["ink"],
                activebackground=coding["composer"],
                activeforeground=coding["ink"],
                relief="solid",
                bd=1,
                font=("Segoe UI", 7, "bold"),
                padx=7,
                pady=3,
                cursor="hand2",
            ).pack(anchor="e", pady=(7, 0))
        if len(rows) > 80:
            tk.Label(master, text=f"+{len(rows) - 80} more skills", bg=coding["rail"], fg=coding["muted"], font=("Segoe UI", 8)).pack(anchor="w", pady=(8, 0))

    def _render_coding_files(self, master: tk.Misc) -> None:
        coding = coding_palette(self.app.theme_name)
        workspace = Path(self.app.coding_workspace_var.get() or _hub_globals["DEFAULT_WORKSPACE"])
        if self.app.native_file_changes or self.app.native_turn_diff:
            tk.Label(master, text="Turn changes", bg=coding["rail"], fg=coding["ink"], font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x", pady=(2, 7))
            for change in self.app.native_file_changes:
                row = tk.Frame(master, bg=coding["panel"], highlightbackground=coding["line"], highlightthickness=1)
                row.pack(fill="x", pady=2)
                kind = str(change.get("kind") or "update").upper()
                path = str(change.get("path") or "")
                tk.Label(row, text=kind[:6], bg=coding["panel"], fg=GREEN, font=("Segoe UI", 7, "bold"), width=7).pack(side="left", padx=(8, 4), pady=6)
                tk.Label(row, text=clip_text(path, 34), bg=coding["panel"], fg=coding["ink"], font=("Segoe UI", 8), anchor="w").pack(side="left", fill="x", expand=True, padx=(0, 8), pady=6)
            if self.app.native_turn_diff:
                diff_box = tk.Text(
                    master,
                    height=14,
                    bg=DARK,
                    fg="#d8e0da",
                    insertbackground="#d8e0da",
                    relief="flat",
                    font=("Consolas", 7),
                    wrap="none",
                    padx=8,
                    pady=8,
                )
                diff_box.pack(fill="x", pady=(7, 14))
                diff_box.insert("1.0", self.app.native_turn_diff)
                diff_box.configure(state="disabled")
        tk.Label(master, text="Project files", bg=coding["rail"], fg=coding["ink"], font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x", pady=(2, 3))
        tk.Label(master, text=str(workspace), bg=coding["rail"], fg=coding["muted"], font=("Consolas", 7), wraplength=285, justify="left", anchor="w").pack(fill="x", pady=(0, 10))
        try:
            entries = sorted(workspace.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))[:30] if workspace.exists() else []
        except OSError:
            entries = []
        for entry in entries:
            row = tk.Frame(master, bg=coding["panel"], highlightbackground=coding["line"], highlightthickness=1)
            row.pack(fill="x", pady=2)
            symbol = "DIR" if entry.is_dir() else "FILE"
            tk.Label(row, text=symbol, bg=coding["panel"], fg=BLUE if entry.is_dir() else coding["muted"], font=("Segoe UI", 7, "bold"), width=5).pack(side="left", padx=(8, 5), pady=6)
            tk.Label(row, text=clip_text(entry.name, 30), bg=coding["panel"], fg=coding["ink"], font=("Segoe UI", 8), anchor="w").pack(side="left", fill="x", expand=True, padx=(0, 8), pady=6)
        if not entries:
            tk.Label(master, text="No project files available", bg=coding["rail"], fg=coding["muted"], font=("Segoe UI", 9), pady=18).pack(fill="x")

    def _render_coding_terminal(self, master: tk.Misc) -> None:
        coding = coding_palette(self.app.theme_name)
        tk.Label(master, text="Terminal", bg=coding["rail"], fg=coding["ink"], font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x", pady=(2, 10))
        terminal = tk.Frame(master, bg=DARK, highlightbackground=LINE_STRONG, highlightthickness=1, padx=12, pady=12)
        terminal.pack(fill="x")
        tk.Label(terminal, text=">_", bg=DARK, fg=GREEN, font=("Consolas", 12, "bold")).pack(anchor="w")
        state = "Native process attached" if self.app.native_transport is not None else "No native process attached"
        tk.Label(terminal, text=state, bg=DARK, fg=MUTED, font=("Consolas", 8)).pack(anchor="w", pady=(8, 0))
        for line in self.app.native_diagnostics[-20:]:
            tk.Label(
                terminal,
                text=clip_text(line, 120),
                bg=DARK,
                fg=MUTED,
                font=("Consolas", 7),
                wraplength=285,
                justify="left",
                anchor="w",
            ).pack(fill="x", pady=(3, 0))
