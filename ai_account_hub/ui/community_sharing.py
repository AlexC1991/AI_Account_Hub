"""Direct community-sharing control, consent, and payload preview UI."""

from __future__ import annotations

import datetime as dt
import json

from PySide6.QtCore import QPoint, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from ai_account_hub.ui.theme import ThemeManager
from ai_account_hub.ui.widgets import ToggleSwitch, make_button


class CommunityUploadWorker(QThread):
    """Run signed network writes away from Qt's UI thread."""

    succeeded = Signal(dict)
    failed = Signal(str)

    def __init__(self, api, *, payload: dict | None = None, withdraw: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._api = api
        self._payload = payload
        self._withdraw = bool(withdraw)

    def run(self) -> None:
        try:
            result = self._api.withdraw() if self._withdraw else self._api.submit(self._payload)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(dict(result))


def _text(value: str, object_name: str = "") -> QLabel:
    label = QLabel(value)
    if object_name:
        label.setObjectName(object_name)
    return label


class CommunitySharingControl(QFrame):
    """Compact header control that remains visible on every primary screen."""

    toggle_requested = Signal(bool)
    details_requested = Signal()

    def __init__(self, enabled: bool = False, *, test_mode: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("communityShareControl")
        self.setFixedSize(202, 40)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Open Community sharing controls")
        self._test_mode = bool(test_mode)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 5, 4)
        row.setSpacing(8)
        copy = QVBoxLayout()
        copy.setContentsMargins(0, 0, 0, 0)
        copy.setSpacing(0)
        self.title = _text("Community sharing", "communityShareTitle")
        self.status = _text("", "communityShareStatus")
        copy.addWidget(self.title)
        copy.addWidget(self.status)
        row.addLayout(copy, 1)

        self.toggle = QPushButton()
        self.toggle.setObjectName("communityShareToggle")
        self.toggle.setCheckable(True)
        self.toggle.setFixedSize(48, 28)
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.setToolTip("Turn anonymous daily Community sharing on or off")
        self.toggle.clicked.connect(lambda checked: self.toggle_requested.emit(bool(checked)))
        row.addWidget(self.toggle)
        self.set_enabled(enabled)

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self.toggle.blockSignals(True)
        self.toggle.setChecked(enabled)
        self.toggle.setProperty("on", "true" if enabled else "false")
        self.toggle.setText("On" if enabled else "Off")
        self.toggle.blockSignals(False)
        self.status.setText(
            "Test API enabled" if enabled and self._test_mode
            else "Anonymous daily summary" if enabled
            else "Nothing leaves this device"
        )
        ThemeManager.repolish(self.toggle)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.details_requested.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class CommunitySharingPopover(QFrame):
    """Compact sharing dashboard anchored below the fixed header control."""

    toggle_requested = Signal(bool)
    preview_requested = Signal()
    settings_requested = Signal()
    withdraw_requested = Signal()

    def __init__(self, tokens: dict, parent=None) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("communitySharePopover")
        self.setFixedSize(332, 300)
        self._tokens = dict(tokens)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 11, 12, 11)
        layout.setSpacing(9)

        header = QHBoxLayout()
        icon = _text("⇧", "communityPopoverIcon")
        icon.setFixedWidth(18)
        icon.setAlignment(Qt.AlignCenter)
        header.addWidget(icon)
        title = _text("Community sharing", "communityPopoverTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.state = _text("Off", "communityPopoverState")
        self.state.setAlignment(Qt.AlignCenter)
        self.state.setFixedWidth(34)
        header.addWidget(self.state)
        layout.addLayout(header)

        self.summary = _text("Anonymous model statistics stay on this device.", "communityPopoverSummary")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        layout.addWidget(self._divider())

        setting = QFrame()
        setting.setObjectName("communityPopoverSection")
        setting_layout = QHBoxLayout(setting)
        setting_layout.setContentsMargins(10, 8, 10, 8)
        setting_layout.setSpacing(8)
        setting_copy = QVBoxLayout()
        setting_copy.setContentsMargins(0, 0, 0, 0)
        setting_copy.setSpacing(1)
        setting_copy.addWidget(_text("Contribute anonymous stats", "communityPopoverSectionTitle"))
        self.setting_caption = _text("Off - no automatic uploads", "communityPopoverCaption")
        setting_copy.addWidget(self.setting_caption)
        setting_layout.addLayout(setting_copy, 1)
        self.toggle = ToggleSwitch(False)
        self.toggle.setFixedSize(38, 20)
        self.toggle.toggled.connect(self._toggle)
        setting_layout.addWidget(self.toggle)
        layout.addWidget(setting)

        metrics = QHBoxLayout()
        metrics.setSpacing(8)
        last_card, self.last_upload = self._metric("LAST UPLOAD", "Never")
        schedule_card, self.schedule = self._metric("SCHEDULE", "Daily summary")
        metrics.addWidget(last_card, 1)
        metrics.addWidget(schedule_card, 1)
        layout.addLayout(metrics)
        layout.addWidget(self._divider())

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.preview = make_button("Preview next upload", "ghost")
        self.settings = make_button("Sharing settings", "ghost")
        self.preview.clicked.connect(self._preview)
        self.settings.clicked.connect(self._settings)
        action_row.addWidget(self.preview, 1)
        action_row.addWidget(self.settings, 1)
        layout.addLayout(action_row)

        delete_row = QHBoxLayout()
        self.withdraw = make_button("Delete my contribution", "danger")
        self.withdraw.clicked.connect(self._withdraw)
        delete_row.addWidget(self.withdraw)
        delete_row.addStretch(1)
        layout.addLayout(delete_row)
        self.set_theme(tokens)

    @staticmethod
    def _divider() -> QFrame:
        divider = QFrame()
        divider.setObjectName("communityPopoverDivider")
        divider.setFixedHeight(1)
        return divider

    @staticmethod
    def _metric(title: str, value: str) -> tuple[QFrame, QLabel]:
        card = QFrame()
        card.setObjectName("communityPopoverMetric")
        box = QVBoxLayout(card)
        box.setContentsMargins(9, 7, 9, 7)
        box.setSpacing(2)
        box.addWidget(_text(title, "communityPopoverMetricTitle"))
        value_label = _text(value, "communityPopoverMetricValue")
        box.addWidget(value_label)
        return card, value_label

    def set_theme(self, tokens: dict) -> None:
        self._tokens = dict(tokens)
        self.toggle._on = str(tokens.get("success") or "#3eb268")
        self.toggle._off = str(tokens.get("borderStrong") or "#39434c")
        self.toggle._knob = str(tokens.get("text") or "#ffffff")
        self.toggle.update()

    def set_state(
        self,
        enabled: bool,
        status: dict,
        *,
        payload_available: bool,
        can_withdraw: bool,
        test_mode: bool,
    ) -> None:
        enabled = bool(enabled)
        self.toggle.blockSignals(True)
        self.toggle.setChecked(enabled)
        self.toggle.blockSignals(False)
        self.state.setText("On" if enabled else "Off")
        self.state.setProperty("on", "true" if enabled else "false")
        self.summary.setText(
            "One anonymous model summary is contributed each day."
            if enabled and not test_mode else
            "Offline test sharing is enabled; no network request is made."
            if enabled else
            "Anonymous model statistics stay on this device."
        )
        self.setting_caption.setText(
            "On - one automatic daily upload" if enabled and not test_mode
            else "On - offline test only" if enabled
            else "Off - no automatic uploads"
        )
        self.last_upload.setText(self._format_upload_time(status.get("acceptedAtUtc")))
        self.schedule.setText("Offline test" if test_mode else "Daily summary")
        self.preview.setEnabled(payload_available)
        self.withdraw.setEnabled(can_withdraw)
        ThemeManager.repolish(self.state)

    def show_for(self, anchor: QWidget) -> None:
        origin = anchor.mapToGlobal(QPoint(anchor.width() - self.width(), anchor.height() + 7))
        screen = anchor.screen().availableGeometry()
        x = max(screen.left() + 8, min(origin.x(), screen.right() - self.width() - 8))
        y = origin.y()
        if y + self.height() > screen.bottom() - 8:
            y = anchor.mapToGlobal(QPoint(0, -self.height() - 7)).y()
        self.move(x, max(screen.top() + 8, y))
        self.show()
        self.raise_()

    @staticmethod
    def _format_upload_time(value) -> str:
        raw = str(value or "").strip()
        if not raw:
            return "Never"
        try:
            moment = dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()
        except ValueError:
            return "Recorded"
        if moment.date() == dt.datetime.now().astimezone().date():
            return f"Today {moment.strftime('%H:%M')}"
        return moment.strftime("%d %b %Y")

    def _toggle(self, enabled: bool) -> None:
        self.close()
        self.toggle_requested.emit(bool(enabled))

    def _preview(self) -> None:
        self.close()
        self.preview_requested.emit()

    def _settings(self) -> None:
        self.close()
        self.settings_requested.emit()

    def _withdraw(self) -> None:
        self.close()
        self.withdraw_requested.emit()


class CommunityPayloadDialog(QDialog):
    def __init__(self, payload: dict, *, network_request: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("communityDialog")
        self.setWindowTitle("Community sharing preview")
        self.resize(720, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        layout.addWidget(_text("Exact daily payload", "dialogTitle"))
        copy = _text(
            "This is the complete object sent to the Community Worker. No hidden fields are added."
            if network_request else
            "This is the complete object accepted by the offline test API. No hidden fields are added.",
            "muted",
        )
        copy.setWordWrap(True)
        layout.addWidget(copy)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        editor.setPlainText(json.dumps(payload, indent=2, sort_keys=True))
        layout.addWidget(editor, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = make_button("Close", "primary")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)


class CommunityConsentDialog(QDialog):
    """First-use consent shown before the Direct Toggle can remain enabled."""

    def __init__(
        self,
        payload: dict | None,
        error: str = "",
        *,
        test_mode: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("communityDialog")
        self.setWindowTitle("Community sharing")
        self.setModal(True)
        self.setFixedSize(650, 520)
        self._payload = payload
        self._test_mode = bool(test_mode)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)
        layout.addWidget(_text("Help build real-world community results", "dialogTitle"))
        intro = _text(
            "Send one anonymous numeric summary per day. It is signed by a machine-local key and sent to the Community staging Worker."
            if not self._test_mode else
            "Send one anonymous numeric summary per day to the offline test API. No network request is made.",
            "muted",
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        columns = QHBoxLayout()
        columns.setSpacing(10)
        columns.addWidget(self._list_card(
            "Shared",
            (
                "Provider, model ID and reasoning setting",
                "Token and completed-task totals",
                "Active time and engineering activity counts",
                "5-hour and weekly limit movement",
            ),
            "communitySharedCard",
        ), 1)
        columns.addWidget(self._list_card(
            "Never shared",
            (
                "Account names, emails or profile IDs",
                "Prompts, responses or transcript content",
                "File paths, project names or command text",
                "Cookies, OAuth data or authentication tokens",
            ),
            "communityPrivateCard",
        ), 1)
        layout.addLayout(columns, 1)

        notice = _text(
            error or "Strict schema v1 rejects any field outside the list above.",
            "communityError" if error else "faint",
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)

        actions = QHBoxLayout()
        preview = make_button("Preview exact payload", "ghost")
        preview.setEnabled(payload is not None)
        preview.clicked.connect(self._preview)
        actions.addWidget(preview)
        actions.addStretch(1)
        cancel = make_button("Cancel", "ghost")
        cancel.clicked.connect(self.reject)
        enable = make_button("Turn on sharing", "primary")
        enable.setEnabled(payload is not None)
        enable.clicked.connect(self.accept)
        actions.addWidget(cancel)
        actions.addWidget(enable)
        layout.addLayout(actions)

    @staticmethod
    def _list_card(title: str, lines: tuple[str, ...], object_name: str) -> QFrame:
        card = QFrame()
        card.setObjectName(object_name)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(13, 12, 13, 12)
        layout.setSpacing(7)
        layout.addWidget(_text(title, "communityCardTitle"))
        for line in lines:
            label = _text(f"•  {line}", "communityListItem")
            label.setWordWrap(True)
            layout.addWidget(label)
        layout.addStretch(1)
        return card

    def _preview(self) -> None:
        if self._payload is not None:
            CommunityPayloadDialog(
                self._payload,
                network_request=not self._test_mode,
                parent=self,
            ).exec()


class CommunityStatusDialog(QDialog):
    def __init__(self, enabled: bool, status: dict, payload: dict | None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("communityDialog")
        self.setWindowTitle("Community sharing status")
        self.setFixedSize(630, 400)
        self._payload = payload
        self.withdraw_requested = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(11)
        layout.addWidget(_text("Community sharing", "dialogTitle"))
        state = _text("Enabled" if enabled else "Off")
        state.setProperty("pill", "ready" if enabled else "idle")
        state.setFixedWidth(76)
        state.setAlignment(Qt.AlignCenter)
        layout.addWidget(state)
        details = (
            ("Transport", str(status.get("endpoint") or "test://local/community/v1")),
            ("Installation", str(status.get("installationId") or "Not registered")),
            ("Last accepted", str(status.get("acceptedAtUtc") or "No submission yet")),
            ("Receipt", str(status.get("receiptId") or "-")),
            ("Publication", str(status.get("publicationSource") or "Waiting for first upload")),
            ("Network request", "No" if not status.get("networkRequest") else "Yes"),
        )
        for title, value in details:
            row = QHBoxLayout()
            row.addWidget(_text(title, "faint"))
            row.addStretch(1)
            value_label = _text(value, "muted")
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(value_label)
            layout.addLayout(row)
        layout.addStretch(1)
        actions = QHBoxLayout()
        preview = make_button("Preview payload", "ghost")
        preview.setEnabled(payload is not None)
        preview.clicked.connect(
            lambda: CommunityPayloadDialog(
                self._payload,
                network_request=bool(status.get("networkRequest", True)),
                parent=self,
            ).exec()
            if self._payload is not None else None
        )
        actions.addWidget(preview)
        withdraw = make_button("Withdraw shared data", "danger")
        withdraw.setEnabled(bool(status.get("installationId")))
        withdraw.setToolTip(
            "Delete this installation's accepted raw submissions and its local signing identity"
        )
        withdraw.clicked.connect(self._request_withdrawal)
        actions.addWidget(withdraw)
        actions.addStretch(1)
        close = make_button("Close", "primary")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)

    def _request_withdrawal(self) -> None:
        self.withdraw_requested = True
        self.accept()
