"""Account card widget (left rail) and its small label/soft-color helpers."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ai_account_hub import data
from ai_account_hub import core as L
from ai_account_hub.ui.tokens import severity_color
from ai_account_hub.ui.widgets import Avatar, ElidedLabel, SeverityBar, StatusPill

def _label(text: str, obj: str = "", *, bold: bool = False, size: int | None = None) -> QLabel:
    lab = QLabel(text)
    if obj:
        lab.setObjectName(obj)
    if bold or size:
        weight = "600" if bold else "400"
        px = f"font-size:{size}px;" if size else ""
        lab.setStyleSheet(f"{px}font-weight:{weight};")
    return lab


class AccountCard(QFrame):
    clicked = Signal(str)  # profile id

    def __init__(self, profile: dict, tokens: dict, template: str = "Balanced") -> None:
        super().__init__()
        self.setObjectName("accountCard")
        self.profile = profile
        self.pid = data.profile_id(profile)
        self._tokens = tokens
        self._template = template
        self._in_use = False
        self.setCursor(Qt.PointingHandCursor)
        self._build()

    def set_in_use(self, active: bool) -> None:
        if bool(active) != self._in_use:
            self._in_use = bool(active)
            self.update_runtime()

    def _build(self) -> None:
        tokens = self._tokens
        outer = QVBoxLayout(self)
        compact = self._template == "Compact"
        outer.setContentsMargins(12, 12 if compact else 13, 12, 12 if compact else 13)
        outer.setSpacing(6 if compact else 9)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.avatar = Avatar(
            data.provider_color(self.profile),
            data.provider_monogram(self.profile),
            size=28 if compact else 32,
            radius=8,
            icon_path=data.provider_icon_path(self.profile),
        )
        top.addWidget(self.avatar, 0, Qt.AlignTop)

        idbox = QVBoxLayout()
        idbox.setSpacing(2)
        self.name = ElidedLabel(str(self.profile.get("name", "Account")))
        self.name.setStyleSheet("font-size:13px;font-weight:600;")
        self.sub = ElidedLabel("")
        self.sub.setObjectName("faint")
        idbox.addWidget(self.name)
        idbox.addWidget(self.sub)
        top.addLayout(idbox, 1)

        self.pill = StatusPill()
        top.addWidget(self.pill, 0, Qt.AlignTop)
        outer.addLayout(top)

        self.identity = _label("", "faint")
        self.identity.setVisible(self._template == "Identity")
        outer.addWidget(self.identity)

        self.bars = QWidget()
        bl = QVBoxLayout(self.bars)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)
        self.usage_labels: dict[str, QLabel] = {}
        self.usage_bars: dict[str, SeverityBar] = {}
        for key, caption in (("weeklyLimitUsedPercent", "WEEKLY USAGE LEFT"), ("shortLimitUsedPercent", "5H SESSION LEFT")):
            if compact and key == "shortLimitUsedPercent":
                continue
            if self._template == "Identity" and key == "shortLimitUsedPercent":
                continue
            head = QHBoxLayout()
            caption_label = _label(caption, "faint")
            if self._template == "Usage First":
                caption_label.setStyleSheet(f"color:{tokens['text2']};font-size:10px;font-weight:700;")
            head.addWidget(caption_label)
            head.addStretch(1)
            value = _label("—", "faint")
            self.usage_labels[key] = value
            head.addWidget(value)
            bar = SeverityBar(tokens)
            self.usage_bars[key] = bar
            bl.addLayout(head)
            bl.addWidget(bar)
        outer.addWidget(self.bars)

        # Shown for providers whose CLI doesn't expose quota (Cursor / Antigravity)
        # instead of blank usage bars — an honest note, not fabricated numbers.
        self.usage_note = _label("", "faint")
        self.usage_note.setWordWrap(True)
        self.usage_note.setVisible(False)
        outer.addWidget(self.usage_note)

        self.cooldown = _label("", "faint")
        self.cooldown.setStyleSheet(
            f"background:{_soft(tokens['warn'])};color:{tokens['warn']};border-radius:7px;padding:6px 9px;font-size:11px;"
        )
        outer.addWidget(self.cooldown)
        self.update_runtime()

    def update_runtime(self) -> None:
        state = data.account_state(self.profile)
        self.name.setText(str(self.profile.get("name", "Account")))
        plan = data.account_plan(self.profile)
        if self._template == "Plan Chips":
            self.sub.setText(f"{data.provider_label(self.profile)}  ·  [{plan}]")
        elif self._template == "Usage First":
            weekly = data.percent_left(self.profile.get("weeklyLimitUsedPercent"))
            session = data.percent_left(self.profile.get("shortLimitUsedPercent"))
            self.sub.setText(
                f"Week {'-' if weekly is None else f'{weekly:.0f}%'}  ·  "
                f"Session {'-' if session is None else f'{session:.0f}%'}"
            )
        else:
            self.sub.setText(f"{data.provider_label(self.profile)} · {plan}")
        self.identity.setText(L.masked_account_identity_label(self.profile))
        # Top pill shows just the status word (no timer); the countdown lives in
        # the cooldown chip at the bottom of the card, so it isn't repeated. A
        # ready account that is the active Codex Desktop account reads "In use".
        if self._in_use and state == "ready":
            self.pill.setText("In use")
            self.pill.set_kind("inuse")
        else:
            self.pill.setText(data.STATE_LABEL.get(state, L.status_label(state)))
            self.pill.set_kind(data.STATE_PILL.get(state, "idle"))
        for key, bar in self.usage_bars.items():
            left = data.percent_left(self.profile.get(key))
            bar.set_percent_left(left)
            lbl = self.usage_labels[key]
            lbl.setText("—" if left is None else f"{left:.0f}%")
            lbl.setStyleSheet(
                f"color:{severity_color(self._tokens, left)};font-size:11px;font-weight:700;"
            )
        countdown = L.ready_countdown(self.profile)
        base = {"login": "Login required", "error": "Refresh error", "not_ready": "Not ready"}.get(state, L.status_label(state))
        self.cooldown.setText(base + (f" · ready in {countdown}" if countdown else ""))
        cooldown_color = self._tokens["warn"] if state == "login" else self._tokens["danger"]
        self.cooldown.setStyleSheet(
            f"background:{_soft(cooldown_color)};color:{cooldown_color};"
            "border-radius:7px;padding:6px 9px;font-size:11px;"
        )
        self.cooldown.setVisible(state in {"not_ready", "login", "error"})
        # If the provider CLI doesn't expose usage %, show an honest note in
        # place of the (empty) bars and point to the web dashboard.
        weekly_left = data.percent_left(self.profile.get("weeklyLimitUsedPercent"))
        session_left = data.percent_left(self.profile.get("shortLimitUsedPercent"))
        usage_err = str(self.profile.get("lastUsageError") or "").strip()
        no_quota = state == "ready" and weekly_left is None and session_left is None and bool(usage_err)
        if no_quota:
            self.usage_note.setText(f"{usage_err}  Open “Online” for the web dashboard.")
        self.usage_note.setVisible(no_quota)
        self.bars.setVisible((self._template != "Plan Chips" or state == "ready") and not no_quota)

    def set_theme(self, tokens: dict) -> None:
        self._tokens = tokens
        for bar in self.usage_bars.values():
            bar.set_theme(tokens)
        self.update_runtime()

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.pid)


def _soft(hexcolor: str, alpha: float = 0.16) -> str:
    from ai_account_hub.ui.tokens import rgba
    return rgba(hexcolor, alpha)


