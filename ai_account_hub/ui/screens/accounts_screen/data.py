"""Data updates for the Accounts screen: stats, calendar/resets, sorting,
filtering, and the account detail rail (mixed into AccountsScreen)."""

from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget,
)

from ai_account_hub import data
from ai_account_hub import core as L
from ai_account_hub.ui.widgets import AccentBar, Avatar, make_button

from ai_account_hub.ui.screens.accounts_screen.card import AccountCard, _label


class _DataMixin:
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
                "desktop", "cli", "login", "device", "status",
                "doctor", "refresh", "online", "dry_run", "restore", "use_reset",
                "set_timer", "clear_timer", "home", "seed",
            },
            "claude": {
                "desktop", "cli", "login", "status", "doctor",
                "refresh", "online", "home", "desktop_login",
            },
            "cursor": {
                "desktop", "cli", "login", "logout", "status",
                "doctor", "refresh", "online", "home",
            },
            "antigravity": {
                "desktop", "cli", "login", "status", "doctor",
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

