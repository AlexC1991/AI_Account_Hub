"""Hub-styled Signal Rail notification overlays and stacking manager.

The overlays are ordinary Qt top-level tool windows. They deliberately avoid
platform notification APIs so every Hub theme has the same compact account and
quota presentation; TrayController retains a native-message fallback.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QPoint,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ai_account_hub import data
from ai_account_hub.ui.account_notifications import AccountNotification
from ai_account_hub.ui.widgets import Avatar, ElidedLabel


# The outer window includes ten pixels of translucent shadow space around the
# fixed card. Stable dimensions keep stacked notifications from shifting.
TOAST_WIDTH = 340
TOAST_HEIGHT = 124
CARD_WIDTH = 320
CARD_HEIGHT = 104
MAX_VISIBLE = 3
STACK_GAP = 6
SCREEN_MARGIN = 8


def _tone_color(tokens: dict[str, str], kind: str) -> str:
    return {
        "success": tokens["success"],
        "warning": tokens["warn"],
        "danger": tokens["danger"],
        "info": tokens["accent"],
    }.get(kind, tokens["accent"])


class _ToneProgressBar(QWidget):
    def __init__(
        self,
        tokens: dict[str, str],
        tone: str,
        percent_left: float | None,
    ) -> None:
        super().__init__()
        self._tokens = tokens
        self._tone = tone
        self._percent = percent_left
        self.setFixedHeight(5)

    def set_theme(self, tokens: dict[str, str], tone: str) -> None:
        self._tokens = tokens
        self._tone = tone
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        radius = height / 2
        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, width, height), radius, radius)
        painter.fillPath(track, QColor(self._tokens["border"]))
        if self._percent is None or self._percent <= 0:
            return
        fill_width = max(height, width * min(100.0, self._percent) / 100.0)
        fill = QPainterPath()
        fill.addRoundedRect(QRectF(0, 0, fill_width, height), radius, radius)
        painter.fillPath(fill, QColor(_tone_color(self._tokens, self._tone)))


class SignalRailToast(QWidget):
    """A single compact toast with provider identity and one event metric."""

    closed = Signal()
    activated = Signal(str)

    def __init__(
        self,
        notification: AccountNotification,
        tokens: dict[str, str],
        timeout_ms: int,
    ) -> None:
        super().__init__(
            None,
            # Qt.Tool keeps a top-level overlay out of the normal taskbar/dock
            # window list; ShowWithoutActivating prevents it stealing typing.
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint,
        )
        self.notification = notification
        self._tokens = tokens
        self._timeout_ms = max(1000, int(timeout_ms))
        self._remaining_ms = self._timeout_ms
        self._timer_started_at = 0.0
        self._closing = False
        self._show_animation: QParallelAnimationGroup | None = None
        self._move_animation: QPropertyAnimation | None = None
        self._close_animation: QPropertyAnimation | None = None

        self.setObjectName("signalRailWindow")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(TOAST_WIDTH, TOAST_HEIGHT)
        self._build()

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.dismiss)

    def _build(self) -> None:
        window_layout = QVBoxLayout(self)
        window_layout.setContentsMargins(10, 10, 10, 10)
        window_layout.setSpacing(0)

        self.card = QFrame()
        self.card.setObjectName("signalRailCard")
        self.card.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.card.setCursor(Qt.PointingHandCursor)
        window_layout.addWidget(self.card)

        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 5)
        self._shadow = shadow
        self.card.setGraphicsEffect(shadow)

        card_layout = QHBoxLayout(self.card)
        card_layout.setContentsMargins(1, 1, 1, 1)
        card_layout.setSpacing(0)

        self.rail = QFrame()
        self.rail.setObjectName("signalRailTone")
        self.rail.setFixedWidth(4)
        card_layout.addWidget(self.rail)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(8, 7, 8, 7)
        content_layout.setSpacing(5)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        identity = {
            "provider": self.notification.provider_key,
        }
        self.avatar = Avatar(
            data.provider_color(identity) if self.notification.provider_key else self._tokens["accent"],
            data.provider_monogram(identity) if self.notification.provider_key else "AI",
            size=30,
            radius=7,
            icon_path=(
                data.provider_icon_path(identity)
                if self.notification.provider_key
                else ""
            ),
        )
        top.addWidget(self.avatar, 0, Qt.AlignVCenter)

        copy = QVBoxLayout()
        copy.setContentsMargins(0, 0, 0, 0)
        copy.setSpacing(0)
        self.title = ElidedLabel(self.notification.title)
        self.title.setObjectName("signalTitle")
        account_line = self.notification.account_name
        if (
            self.notification.provider_label
            and self.notification.provider_label != self.notification.account_name
        ):
            account_line = f"{account_line} | {self.notification.provider_label}"
        self.account = ElidedLabel(account_line)
        self.account.setObjectName("signalAccount")
        self.message = ElidedLabel(self.notification.message)
        self.message.setObjectName("signalMessage")
        copy.addWidget(self.title)
        copy.addWidget(self.account)
        copy.addWidget(self.message)
        top.addLayout(copy, 1)

        metric_host = QWidget()
        metric_host.setFixedWidth(86)
        metric = QVBoxLayout(metric_host)
        metric.setContentsMargins(0, 0, 0, 0)
        metric.setSpacing(0)
        close_row = QHBoxLayout()
        close_row.setContentsMargins(0, 0, 0, 0)
        close_row.addStretch(1)
        self.close_button = QPushButton("\u00d7")
        self.close_button.setObjectName("signalClose")
        self.close_button.setFixedSize(20, 20)
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setToolTip("Dismiss notification")
        self.close_button.clicked.connect(lambda _checked=False: self.dismiss())
        close_row.addWidget(self.close_button)
        metric.addLayout(close_row)
        self.value = QLabel(self.notification.value_text or "Notice")
        self.value.setObjectName("signalValue")
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.meta = ElidedLabel(self.notification.meta)
        self.meta.setObjectName("signalMeta")
        self.meta.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        metric.addWidget(self.value)
        metric.addWidget(self.meta)
        top.addWidget(metric_host)
        content_layout.addLayout(top, 1)

        self.progress = _ToneProgressBar(
            self._tokens,
            self.notification.kind,
            self.notification.percent_left,
        )
        self.progress.setVisible(self.notification.percent_left is not None)
        content_layout.addWidget(self.progress)
        card_layout.addWidget(content, 1)
        self.set_theme(self._tokens)

    def set_theme(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens
        tone = _tone_color(tokens, self.notification.kind)
        self.rail.setStyleSheet(
            f"background:{tone};border:none;border-radius:2px;"
        )
        self.value.setStyleSheet(
            f"color:{tone};font-size:16px;font-weight:750;"
        )
        self.progress.set_theme(tokens, self.notification.kind)
        shadow = QColor("#000000")
        background = QColor(tokens.get("bg", "#000000"))
        shadow.setAlpha(112 if background.lightness() < 128 else 58)
        self._shadow.setColor(shadow)
        self.update()

    def show_at(self, target: QPoint, bottom_side: bool) -> None:
        offset = QPoint(0, 9 if bottom_side else -9)
        self._shadow.setEnabled(False)
        self.move(target + offset)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()

        group = QParallelAnimationGroup(self)
        opacity = QPropertyAnimation(self, b"windowOpacity", group)
        opacity.setDuration(180)
        opacity.setStartValue(0.0)
        opacity.setEndValue(1.0)
        opacity.setEasingCurve(QEasingCurve.OutCubic)
        position = QPropertyAnimation(self, b"pos", group)
        position.setDuration(180)
        position.setStartValue(target + offset)
        position.setEndValue(target)
        position.setEasingCurve(QEasingCurve.OutCubic)
        group.finished.connect(self._finish_show)
        self._show_animation = group
        group.start()
        self._resume_timer()

    def _finish_show(self) -> None:
        self._shadow.setEnabled(True)
        self.card.update()
        self.update()

    def move_to(self, target: QPoint) -> None:
        if not self.isVisible():
            self.move(target)
            return
        if (
            self._show_animation is not None
            and self._show_animation.state() == QAbstractAnimation.State.Running
        ):
            # A second event can arrive before the first toast's entrance
            # animation completes. Starting another position animation would
            # cancel the whole group and strand its opacity at zero.
            self._show_animation.stop()
            self.setWindowOpacity(1.0)
            self._finish_show()
            self.move(target)
            return
        if self._move_animation is not None:
            self._move_animation.stop()
        animation = QPropertyAnimation(self, b"pos", self)
        animation.setDuration(150)
        animation.setStartValue(self.pos())
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        self._move_animation = animation
        animation.start()

    def dismiss(self, immediate: bool = False) -> None:
        if self._closing:
            return
        self._closing = True
        self._dismiss_timer.stop()
        if immediate or not self.isVisible():
            self.hide()
            self.closed.emit()
            return
        animation = QPropertyAnimation(self, b"windowOpacity", self)
        animation.setDuration(120)
        animation.setStartValue(self.windowOpacity())
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.InCubic)
        animation.finished.connect(self._finish_dismiss)
        self._close_animation = animation
        animation.start()

    def _finish_dismiss(self) -> None:
        self.hide()
        self.closed.emit()

    def _pause_timer(self) -> None:
        if not self._dismiss_timer.isActive():
            return
        elapsed = int((time.monotonic() - self._timer_started_at) * 1000)
        self._remaining_ms = max(250, self._remaining_ms - elapsed)
        self._dismiss_timer.stop()

    def _resume_timer(self) -> None:
        if self._closing:
            return
        self._timer_started_at = time.monotonic()
        self._dismiss_timer.start(self._remaining_ms)

    def enterEvent(self, event) -> None:
        self._pause_timer()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._resume_timer()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and not self._closing:
            self.activated.emit(self.notification.profile_id)
            self.dismiss()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SignalRailManager(QObject):
    """Own up to three tray/work-area-anchored Signal Rail overlays."""

    activated = Signal(str)

    def __init__(
        self,
        tokens: dict[str, str],
        anchor_rect: Callable[[], QRect | None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._anchor_rect = anchor_rect
        self._toasts: list[SignalRailToast] = []

    @property
    def active_toasts(self) -> tuple[SignalRailToast, ...]:
        return tuple(self._toasts)

    def show_notification(
        self,
        notification: AccountNotification,
        timeout_ms: int | None = None,
    ) -> bool:
        if QApplication.primaryScreen() is None:
            return False
        # Remove the oldest card synchronously before placing the new one, so a
        # burst never creates an off-screen fourth window during animations.
        while len(self._toasts) >= MAX_VISIBLE:
            self._toasts[0].dismiss(immediate=True)
        duration = timeout_ms or (
            12000 if notification.kind in {"warning", "danger"} else 8000
        )
        toast = SignalRailToast(notification, self._tokens, duration)
        toast.closed.connect(lambda item=toast: self._remove(item))
        toast.activated.connect(lambda pid: self.activated.emit(pid))
        self._toasts.append(toast)
        targets, bottom_side = self._targets()
        for item, target in targets.items():
            if item is toast:
                item.show_at(target, bottom_side)
            else:
                item.move_to(target)
        return True

    def set_theme(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens
        for toast in self._toasts:
            toast.set_theme(tokens)

    def reposition(self) -> None:
        targets, _bottom_side = self._targets()
        for toast, target in targets.items():
            toast.move_to(target)

    def close_all(self) -> None:
        for toast in list(self._toasts):
            toast.dismiss(immediate=True)

    def _remove(self, toast: SignalRailToast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        toast.deleteLater()
        self.reposition()

    def _targets(self) -> tuple[dict[SignalRailToast, QPoint], bool]:
        anchor_rect = self._anchor_rect() if self._anchor_rect is not None else None
        if anchor_rect is not None and anchor_rect.isValid():
            anchor = anchor_rect.center()
            screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        else:
            # Tray hosts can briefly report an empty rectangle while restarting.
            # The current Windows fallback is bottom-right; a platform adapter
            # can select top-right for a macOS menu-bar status item.
            screen = QApplication.primaryScreen()
            anchor = screen.geometry().bottomRight() if screen is not None else QPoint()
        if screen is None:
            return {}, True
        available = screen.availableGeometry()
        geometry = screen.geometry()
        # A top menu bar/tray stacks downward; a bottom taskbar stacks upward.
        # This is also the decision point for a macOS/Linux platform adapter.
        right_side = anchor.x() >= geometry.center().x()
        bottom_side = anchor.y() >= geometry.center().y()
        x = (
            available.right() - TOAST_WIDTH - SCREEN_MARGIN
            if right_side
            else available.left() + SCREEN_MARGIN
        )
        x = max(
            available.left() + 2,
            min(x, available.right() - TOAST_WIDTH - 2),
        )

        targets: dict[SignalRailToast, QPoint] = {}
        if bottom_side:
            cursor_y = available.bottom() - SCREEN_MARGIN
            for toast in reversed(self._toasts):
                cursor_y -= TOAST_HEIGHT
                targets[toast] = QPoint(x, cursor_y)
                cursor_y -= STACK_GAP
        else:
            cursor_y = available.top() + SCREEN_MARGIN
            for toast in reversed(self._toasts):
                targets[toast] = QPoint(x, cursor_y)
                cursor_y += TOAST_HEIGHT + STACK_GAP
        return targets, bottom_side
