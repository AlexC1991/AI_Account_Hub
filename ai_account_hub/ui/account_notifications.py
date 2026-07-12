"""Account-limit Signal Rail rules and their settings dialog."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ai_account_hub import core as L
from ai_account_hub import data
from ai_account_hub.ui.widgets import make_button


NOTIFICATION_SETTING_DEFAULTS = {
    "notificationsEnabled": True,
    "notificationLowThreshold": 15,
    "notificationReadyEnabled": True,
    "notificationLimitReachedEnabled": True,
}
NOTIFICATION_THRESHOLDS = (5, 10, 15, 20, 25)
RESET_INCREASE = 25.0
RECOVERY_HYSTERESIS = 5.0


def _setting_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def normalize_notification_settings(raw: dict | None) -> dict:
    source = raw if isinstance(raw, dict) else {}
    settings = dict(NOTIFICATION_SETTING_DEFAULTS)
    for key in (
        "notificationsEnabled",
        "notificationReadyEnabled",
        "notificationLimitReachedEnabled",
    ):
        settings[key] = _setting_bool(source.get(key), settings[key])
    try:
        requested = int(
            source.get(
                "notificationLowThreshold",
                settings["notificationLowThreshold"],
            )
        )
    except (TypeError, ValueError):
        requested = settings["notificationLowThreshold"]
    settings["notificationLowThreshold"] = min(
        NOTIFICATION_THRESHOLDS,
        key=lambda threshold: abs(threshold - requested),
    )
    return settings


@dataclass(frozen=True)
class AccountNotification:
    title: str
    message: str
    kind: str = "info"
    profile_id: str = ""
    provider_key: str = ""
    provider_label: str = "AI Account Hub"
    account_name: str = "AI Account Hub"
    value_text: str = ""
    percent_left: float | None = None
    meta: str = ""


@dataclass(frozen=True)
class _AccountSnapshot:
    name: str
    provider_key: str
    provider_label: str
    state: str
    weekly_left: float | None
    session_left: float | None
    weekly_reset: str
    session_reset: str
    ready_countdown: str


def _countdown(raw: object) -> str:
    value = str(L.format_countdown(raw) or "").strip()
    return "" if value in {"", "-", "now"} else value


def _snapshot(profile: dict) -> _AccountSnapshot:
    return _AccountSnapshot(
        name=str(profile.get("name") or "Account"),
        provider_key=data.provider_key(profile),
        provider_label=data.provider_label(profile),
        state=data.account_state(profile),
        weekly_left=data.percent_left(profile.get("weeklyLimitUsedPercent")),
        session_left=data.percent_left(profile.get("shortLimitUsedPercent")),
        weekly_reset=_countdown(
            profile.get("weeklyResetEstimateUtc")
            or profile.get("weeklyLimitResetUtc")
        ),
        session_reset=_countdown(profile.get("shortLimitResetUtc")),
        ready_countdown=str(L.ready_countdown(profile) or "").strip(),
    )


class AccountNotificationMonitor:
    """Detect meaningful account transitions without repeating alerts.

    The first profile set primes the monitor. Later calls compare complete
    refresh snapshots, while latches prevent a low account from warning again
    until it has recovered above the configured threshold plus hysteresis.
    """

    def __init__(self, settings: dict | None = None) -> None:
        self._settings = normalize_notification_settings(settings)
        self._snapshots: dict[str, _AccountSnapshot] = {}
        self._active_ids: set[str] = set()
        self._low_latches: set[tuple[str, str]] = set()
        self._initialized = False

    @property
    def settings(self) -> dict:
        return dict(self._settings)

    def apply_settings(self, settings: dict | None) -> None:
        previous_threshold = self._settings["notificationLowThreshold"]
        self._settings = normalize_notification_settings(settings)
        if self._settings["notificationLowThreshold"] != previous_threshold:
            self._low_latches.clear()

    def evaluate(
        self,
        profiles: list[dict],
        active_ids: set[str] | None = None,
    ) -> list[AccountNotification]:
        active = {str(pid) for pid in (active_ids or set()) if str(pid)}
        current = {
            data.profile_id(profile): _snapshot(profile)
            for profile in profiles
            if isinstance(profile, dict) and data.profile_id(profile)
        }
        threshold = float(self._settings["notificationLowThreshold"])

        if not self._initialized:
            self._snapshots = current
            self._active_ids = active
            self._prime_low_latches(current, active, threshold)
            self._initialized = True
            return []

        notifications: list[AccountNotification] = []
        if self._settings["notificationsEnabled"]:
            for pid, now in current.items():
                before = self._snapshots.get(pid)
                if before is None:
                    continue
                notifications.extend(
                    self._account_events(
                        pid,
                        before,
                        now,
                        active,
                        threshold,
                    )
                )

        self._snapshots = current
        self._active_ids = active
        return notifications

    def _prime_low_latches(
        self,
        snapshots: dict[str, _AccountSnapshot],
        active_ids: set[str],
        threshold: float,
    ) -> None:
        for pid in active_ids:
            snapshot = snapshots.get(pid)
            if snapshot is None:
                continue
            for label, value in (
                ("weekly", snapshot.weekly_left),
                ("session", snapshot.session_left),
            ):
                if value is not None and value <= threshold:
                    self._low_latches.add((pid, label))

    def _account_events(
        self,
        pid: str,
        before: _AccountSnapshot,
        now: _AccountSnapshot,
        active_ids: set[str],
        threshold: float,
    ) -> list[AccountNotification]:
        events: list[AccountNotification] = []
        active = pid in active_ids
        newly_active = active and pid not in self._active_ids

        # A reset is inferred from a large increase in percentage left. Codex's
        # backend guards impossible pre-reset rollovers before they reach this
        # monitor, so this layer can stay provider-neutral.
        reset_parts: list[str] = []
        reset_values: list[float] = []
        if self._did_reset(before.session_left, now.session_left, threshold):
            reset_parts.append(f"5-hour usage is back to {now.session_left:.0f}% left")
            reset_values.append(float(now.session_left))
        if self._did_reset(before.weekly_left, now.weekly_left, threshold):
            reset_parts.append(f"weekly usage is back to {now.weekly_left:.0f}% left")
            reset_values.append(float(now.weekly_left))
        became_ready = before.state in {"not_ready", "checking"} and now.state == "ready"
        if self._settings["notificationReadyEnabled"] and (reset_parts or became_ready):
            if reset_parts:
                message = f"{'; '.join(reset_parts)}."
            else:
                message = "This account is ready to use again."
            percent = max(reset_values) if reset_values else None
            events.append(
                AccountNotification(
                    "Account ready again",
                    message,
                    "success",
                    profile_id=pid,
                    provider_key=now.provider_key,
                    provider_label=now.provider_label,
                    account_name=now.name,
                    value_text=f"{percent:.0f}%" if percent is not None else "Ready",
                    percent_left=percent,
                    meta="Ready to use",
                )
            )

        reached_limit = (
            active
            and before.state == "ready"
            and now.state == "not_ready"
        )
        if reached_limit and self._settings["notificationLimitReachedEnabled"]:
            events.append(
                AccountNotification(
                    "Active account limit reached",
                    "This account is no longer ready. Choose another account.",
                    "danger",
                    profile_id=pid,
                    provider_key=now.provider_key,
                    provider_label=now.provider_label,
                    account_name=now.name,
                    value_text="0%",
                    percent_left=0.0,
                    meta=(
                        f"Ready in {now.ready_countdown}"
                        if now.ready_countdown
                        else "Switch account"
                    ),
                )
            )
            for label in ("weekly", "session"):
                self._low_latches.add((pid, label))
        elif active and now.state == "ready":
            low_parts: list[str] = []
            low_labels: list[str] = []
            low_values: list[tuple[float, str]] = []
            for label, caption, previous, value, reset in (
                (
                    "session",
                    "5-hour",
                    before.session_left,
                    now.session_left,
                    now.session_reset,
                ),
                (
                    "weekly",
                    "weekly",
                    before.weekly_left,
                    now.weekly_left,
                    now.weekly_reset,
                ),
            ):
                key = (pid, label)
                if value is not None and value > threshold + RECOVERY_HYSTERESIS:
                    self._low_latches.discard(key)
                crossed = (
                    previous is not None
                    and value is not None
                    and previous > threshold
                    and value <= threshold
                )
                if (
                    value is not None
                    and value <= threshold
                    and key not in self._low_latches
                    and (crossed or newly_active)
                ):
                    low_parts.append(f"{caption} {value:.0f}% left")
                    low_labels.append(label)
                    low_values.append((float(value), reset))
                    self._low_latches.add(key)
            if low_parts:
                percent, reset = min(low_values, key=lambda item: item[0])
                if len(low_labels) > 1:
                    title = "Account nearly exhausted"
                    message = "Both usage windows are below the warning threshold."
                elif low_labels[0] == "weekly":
                    title = "Weekly usage running low"
                    message = f"{percent:.0f}% remains in this weekly window."
                else:
                    title = "5-hour limit running low"
                    message = f"{percent:.0f}% remains in the active session."
                events.append(
                    AccountNotification(
                        title,
                        message,
                        "warning",
                        profile_id=pid,
                        provider_key=now.provider_key,
                        provider_label=now.provider_label,
                        account_name=now.name,
                        value_text=f"{percent:.0f}%",
                        percent_left=percent,
                        meta=f"Resets in {reset}" if reset else "Usage running low",
                    )
                )

        return events

    @staticmethod
    def _did_reset(
        before: float | None,
        after: float | None,
        threshold: float,
    ) -> bool:
        if before is None or after is None:
            return False
        was_low = before <= max(threshold, 20.0)
        return was_low and after - before >= RESET_INCREASE


class NotificationSettingsDialog(QDialog):
    test_requested = Signal()

    def __init__(self, settings: dict | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("notificationSettingsDialog")
        self.setWindowTitle("Notification settings")
        self.setModal(True)
        self.setMinimumWidth(410)
        self._build()
        self._set_values(normalize_notification_settings(settings))

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(12)

        title = QLabel("Hub notifications")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        note = QLabel(
            "Warnings follow the account currently in use. Reset notifications apply to every account."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.enabled = QCheckBox("Enable Signal Rail notifications")
        self.enabled.toggled.connect(self._update_enabled)
        layout.addWidget(self.enabled)

        form = QFormLayout()
        form.setContentsMargins(0, 2, 0, 0)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(9)
        self.threshold = QComboBox()
        for value in NOTIFICATION_THRESHOLDS:
            self.threshold.addItem(f"{value}% left", value)
        form.addRow("Warn when usage reaches", self.threshold)
        layout.addLayout(form)

        self.ready = QCheckBox("Notify when an account resets or becomes ready")
        self.reached = QCheckBox("Notify when the active account reaches a limit")
        layout.addWidget(self.ready)
        layout.addWidget(self.reached)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        reset = make_button("Reset defaults", "ghost")
        reset.clicked.connect(self._reset_defaults)
        test = make_button("Send test", "ghost")
        test.clicked.connect(lambda _checked=False: self.test_requested.emit())
        cancel = make_button("Cancel", "ghost")
        cancel.clicked.connect(self.reject)
        save = make_button("Save", "primary")
        save.clicked.connect(self.accept)
        actions.addWidget(reset)
        actions.addWidget(test)
        actions.addStretch(1)
        actions.addWidget(cancel)
        actions.addWidget(save)
        layout.addLayout(actions)

    def _set_values(self, settings: dict) -> None:
        self.enabled.setChecked(settings["notificationsEnabled"])
        index = self.threshold.findData(settings["notificationLowThreshold"])
        self.threshold.setCurrentIndex(max(0, index))
        self.ready.setChecked(settings["notificationReadyEnabled"])
        self.reached.setChecked(settings["notificationLimitReachedEnabled"])
        self._update_enabled(self.enabled.isChecked())

    def _update_enabled(self, enabled: bool) -> None:
        self.threshold.setEnabled(enabled)
        self.ready.setEnabled(enabled)
        self.reached.setEnabled(enabled)

    def _reset_defaults(self) -> None:
        self._set_values(dict(NOTIFICATION_SETTING_DEFAULTS))

    def values(self) -> dict:
        return normalize_notification_settings(
            {
                "notificationsEnabled": self.enabled.isChecked(),
                "notificationLowThreshold": self.threshold.currentData(),
                "notificationReadyEnabled": self.ready.isChecked(),
                "notificationLimitReachedEnabled": self.reached.isChecked(),
            }
        )
