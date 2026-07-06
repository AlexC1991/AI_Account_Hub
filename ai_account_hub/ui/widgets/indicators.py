"""Read-only display widgets: avatar, status pill, elided label, severity/accent bars, dot, and folder tag."""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from ai_account_hub.ui.tokens import severity_color


class Avatar(QWidget):
    """Rounded provider identity tile with a real icon and monogram fallback."""

    def __init__(
        self,
        color: str,
        letters: str,
        size: int = 32,
        radius: int = 8,
        icon_path: str = "",
    ) -> None:
        super().__init__()
        self._color = color
        self._letters = letters
        self._radius = radius
        self._icon_path = icon_path
        self._pixmap = QPixmap(icon_path) if icon_path else QPixmap()
        self.setFixedSize(size, size)

    def set_identity(self, color: str, letters: str, icon_path: str = "") -> None:
        self._color = color
        self._letters = letters
        if icon_path != self._icon_path:
            self._icon_path = icon_path
            self._pixmap = QPixmap(icon_path) if icon_path else QPixmap()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(self.width(), self.height())

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        p.setClipPath(path)
        if not self._pixmap.isNull():
            p.fillPath(path, QColor("#00000000"))
            inset = max(0, int(self.width() * 0.04))
            target = self.rect().adjusted(inset, inset, -inset, -inset)
            scaled = self._pixmap.scaled(
                target.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            x = target.x() + (target.width() - scaled.width()) // 2
            y = target.y() + (target.height() - scaled.height()) // 2
            p.drawPixmap(x, y, scaled)
            return
        p.fillPath(path, QColor(self._color))
        p.setPen(QColor("#ffffff"))
        f = QFont("Segoe UI", max(7, int(self.height() * 0.34)))
        f.setBold(True)
        p.setFont(f)
        p.drawText(rect, Qt.AlignCenter, self._letters)


class StatusPill(QLabel):
    def __init__(self, text: str = "", kind: str = "idle") -> None:
        super().__init__(text)
        self.set_kind(kind)

    def set_kind(self, kind: str) -> None:
        self.setProperty("pill", kind)
        self.setAlignment(Qt.AlignCenter)
        st = self.style()
        st.unpolish(self)
        st.polish(self)


class ElidedLabel(QLabel):
    """Single-line label that yields width before neighbouring controls clip."""

    def __init__(self, text: str = "", mode: Qt.TextElideMode = Qt.ElideRight) -> None:
        super().__init__()
        self._full_text = str(text)
        self._elide_mode = mode
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setToolTip(self._full_text)
        self._apply_elision()

    def full_text(self) -> str:
        return self._full_text

    def setText(self, text: str) -> None:
        self._full_text = str(text)
        self.setToolTip(self._full_text)
        self._apply_elision()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_elision()

    def _apply_elision(self) -> None:
        width = max(0, self.contentsRect().width())
        shown = self.fontMetrics().elidedText(self._full_text, self._elide_mode, width)
        super().setText(shown)


class SeverityBar(QWidget):
    """Thin usage bar colored by severity (design 3a: <20 red, 20-49 amber, >=50 green)."""

    def __init__(self, theme_tokens: dict[str, str], height: int = 5) -> None:
        super().__init__()
        self._tokens = theme_tokens
        self._left: float | None = None
        self.setFixedHeight(height)

    def set_theme(self, theme_tokens: dict[str, str]) -> None:
        self._tokens = theme_tokens
        self.update()

    def set_percent_left(self, percent_left: float | None) -> None:
        self._left = percent_left
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        r = h / 2
        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, w, h), r, r)
        p.fillPath(track, QColor(self._tokens["border"]))
        if self._left is not None and self._left > 0:
            fill_w = max(h, w * min(100.0, self._left) / 100.0)
            fill = QPainterPath()
            fill.addRoundedRect(QRectF(0, 0, fill_w, h), r, r)
            p.fillPath(fill, QColor(severity_color(self._tokens, self._left)))


class AccentBar(QWidget):
    """A thin accent-filled proportional bar (0..1). Neutral usage magnitude —
    unlike SeverityBar it is not colored by a good/bad threshold."""

    def __init__(self, theme_tokens: dict[str, str], height: int = 6) -> None:
        super().__init__()
        self._tokens = theme_tokens
        self._frac = 0.0
        self.setFixedHeight(height)

    def set_fraction(self, fraction: float) -> None:
        self._frac = max(0.0, min(1.0, float(fraction)))
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        r = h / 2
        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, w, h), r, r)
        p.fillPath(track, QColor(self._tokens["border"]))
        if self._frac > 0:
            fw = max(h, w * self._frac)
            fill = QPainterPath()
            fill.addRoundedRect(QRectF(0, 0, fw, h), r, r)
            p.fillPath(fill, QColor(self._tokens["accent"]))


class Dot(QWidget):
    """Small status dot; optionally pulses (used for a live/"Working" thread)."""

    def __init__(self, color: str, pulse: bool = False, size: int = 8) -> None:
        super().__init__()
        self._color = color
        self._pulse = pulse
        self._phase = 0.0
        self.setFixedSize(size, size)
        if pulse:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
            self._timer.start(70)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.09) % (2 * math.pi)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(self._color)
        if self._pulse:
            c.setAlphaF(0.35 + 0.65 * abs(math.sin(self._phase)))
        w, h = self.width(), self.height()
        r = min(w, h) / 2
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawEllipse(QRectF(w / 2 - r, h / 2 - r, r * 2, r * 2))


class FolderTag(QWidget):
    """A small folder icon for a project row. Codex-style: a crisp outlined
    folder with a faint wash, in one muted tone (not a bright rainbow fill) so
    the sidebar reads as a calm list rather than a box of crayons."""

    def __init__(self, color: str, size: int = 15) -> None:
        super().__init__()
        self._color = color
        self.setFixedSize(size, size)

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        c = QColor(self._color)
        wash = QColor(c)
        wash.setAlpha(30)
        pen = QPen(c)
        pen.setWidthF(1.25)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(wash)
        folder = QPainterPath()
        x0, x1 = w * 0.14, w * 0.86
        top, bot = h * 0.30, h * 0.74
        notch = h * 0.10
        folder.moveTo(x0, bot)
        folder.lineTo(x0, top)
        folder.lineTo(x0 + (x1 - x0) * 0.40, top)          # tab top
        folder.lineTo(x0 + (x1 - x0) * 0.52, top + notch)  # tab step
        folder.lineTo(x1, top + notch)
        folder.lineTo(x1, bot)
        folder.closeSubpath()
        p.drawPath(folder)


