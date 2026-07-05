"""App-chrome widgets: network logo/icon, accent + window buttons, the button factory, and the frameless title bar. Holds the shared active-token state that accent buttons repaint against."""

from __future__ import annotations

import math
import weakref

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget


class NetworkLogo(QWidget):
    """Animated 'Network' logo mark: a central hub with orbiting satellite
    nodes and dashed connectors, colored with the active accent."""

    def __init__(self, accent: str, size: int = 24) -> None:
        super().__init__()
        self._accent = accent
        self.setFixedSize(size, size)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def set_accent(self, accent: str) -> None:
        self._accent = accent
        self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.03) % (2 * math.pi)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        r = min(w, h) * 0.34
        accent = QColor(self._accent)
        # dashed connectors + orbiting nodes
        pen = QPen(accent)
        pen.setWidthF(1.0)
        pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        nodes = []
        for i in range(3):
            ang = self._phase + i * (2 * math.pi / 3)
            nx, ny = cx + r * math.cos(ang), cy + r * math.sin(ang)
            nodes.append((nx, ny, i))
            p.drawLine(int(cx), int(cy), int(nx), int(ny))
        p.setPen(Qt.NoPen)
        for nx, ny, i in nodes:
            pulse = 0.5 + 0.5 * abs(math.sin(self._phase + i))
            c = QColor(accent)
            c.setAlphaF(0.55 + 0.45 * pulse)
            rad = 2.4 + 1.2 * pulse
            p.setBrush(c)
            p.drawEllipse(QRectF(nx - rad, ny - rad, rad * 2, rad * 2))
        p.setBrush(accent)
        hub = min(w, h) * 0.14
        p.drawEllipse(QRectF(cx - hub, cy - hub, hub * 2, hub * 2))


def network_icon(accent: str, size: int = 64) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    color = QColor(accent)
    center = size / 2
    radius = size * 0.31
    pen = QPen(color)
    pen.setWidthF(max(1.0, size * 0.035))
    painter.setPen(pen)
    points = []
    for index in range(3):
        angle = -math.pi / 2 + index * (2 * math.pi / 3)
        x = center + radius * math.cos(angle)
        y = center + radius * math.sin(angle)
        points.append((x, y))
        painter.drawLine(int(center), int(center), int(x), int(y))
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    for x, y in points:
        node = size * 0.105
        painter.drawEllipse(QRectF(x - node, y - node, node * 2, node * 2))
    hub = size * 0.14
    painter.drawEllipse(QRectF(center - hub, center - hub, hub * 2, hub * 2))
    painter.end()
    return QIcon(pixmap)


# Shared active-theme tokens for custom-painted buttons. ThemeManager pushes the
# current theme here on every apply() so AccentButtons repaint without per-widget
# wiring. (Qt won't reliably paint a QPushButton *fill* from a QSS rule, so the
# primary CTA is drawn by hand instead.)
_ACTIVE_TOKENS: dict[str, str] = {}
_ACCENT_BUTTONS: list = []


def set_active_tokens(tokens: dict[str, str]) -> None:
    global _ACTIVE_TOKENS
    _ACTIVE_TOKENS = dict(tokens)
    for ref in list(_ACCENT_BUTTONS):
        btn = ref()
        if btn is None:
            _ACCENT_BUTTONS.remove(ref)
        else:
            btn.update()


class AccentButton(QPushButton):
    """A filled accent CTA drawn in paintEvent (reliable fill in every theme)."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)
        self._hover = False
        _ACCENT_BUTTONS.append(weakref.ref(self))

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()

    def sizeHint(self) -> QSize:
        s = super().sizeHint()
        return QSize(s.width() + 10, max(30, s.height()))

    def paintEvent(self, event) -> None:
        t = _ACTIVE_TOKENS
        accent = t.get("accent", "#2c90e8")
        accent_text = t.get("accentText", "#ffffff")
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self.width(), self.height()), 7, 7)
        col = QColor(accent)
        if not self.isEnabled():
            col.setAlphaF(0.4)
        elif self.isDown():
            col = col.darker(115)
        elif self._hover:
            col = col.lighter(112)
        p.fillPath(path, col)
        pen_col = QColor(accent_text)
        if not self.isEnabled():
            pen_col.setAlphaF(0.6)
        p.setPen(pen_col)
        f = self.font()
        f.setPixelSize(12)
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, self.text())


class WinButton(QPushButton):
    """A frameless-window control button (minimize / maximize / close) with a
    crisp hand-drawn icon instead of a font glyph, and a flat hover fill
    (red for close)."""

    def __init__(self, kind: str) -> None:  # 'min' | 'max' | 'close'
        super().__init__()
        self._kind = kind
        self._hover = False
        self.setFixedSize(46, 34)
        self.setCursor(Qt.PointingHandCursor)
        _ACCENT_BUTTONS.append(weakref.ref(self))

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, event) -> None:
        t = _ACTIVE_TOKENS
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # hover background
        if self._hover:
            p.fillRect(self.rect(), QColor("#e81123") if self._kind == "close" else QColor(t.get("panelHover", "#2a2a2a")))
        # icon color
        if self._kind == "close" and self._hover:
            col = QColor("#ffffff")
        else:
            col = QColor(t.get("text2", "#a0a0a0"))
            if self._hover:
                col = QColor(t.get("text", "#ececec"))
        pen = QPen(col)
        pen.setWidthF(1.2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        cx, cy = int(w / 2), int(h / 2)
        r = 5
        if self._kind == "min":
            p.drawLine(cx - r, cy, cx + r, cy)
        elif self._kind == "max":
            p.drawRoundedRect(QRectF(cx - r, cy - r, 2 * r, 2 * r), 1.5, 1.5)
        else:  # close
            p.drawLine(cx - r, cy - r, cx + r, cy + r)
            p.drawLine(cx - r, cy + r, cx + r, cy - r)


def make_button(text: str, variant: str = "ghost", *, object_name: str | None = None) -> QPushButton:
    if variant == "primary":
        btn: QPushButton = AccentButton(text)
    else:
        btn = QPushButton(text)
        btn.setProperty("variant", variant)
    btn.setCursor(Qt.PointingHandCursor)
    if object_name:
        btn.setObjectName(object_name)
    return btn


class TitleBar(QWidget):
    """Custom 34px frameless title bar with drag-to-move + window buttons."""

    def __init__(self, window: QWidget, accent: str, menu_bar: QWidget | None = None) -> None:
        super().__init__()
        self.setObjectName("titlebar")
        self.setFixedHeight(34)
        self._window = window
        self._drag_offset: QPoint | None = None

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 0, 0)
        row.setSpacing(8)

        # The app name lives in the header just below; the title bar keeps only
        # the logo mark + menus to avoid repeating "AI Account Hub" twice.
        self._swatch = NetworkLogo(accent, size=16)
        row.addWidget(self._swatch)
        if menu_bar is not None:
            row.addSpacing(10)
            row.addWidget(menu_bar)
        row.addStretch(1)

        self._btn_min = WinButton("min")
        self._btn_max = WinButton("max")
        self._btn_close = WinButton("close")
        for b, slot in (
            (self._btn_min, self._minimize),
            (self._btn_max, self._toggle_max),
            (self._btn_close, self._window.close),
        ):
            b.clicked.connect(slot)
            row.addWidget(b)

    def set_accent(self, accent: str) -> None:
        self._set_swatch(accent)

    def _set_swatch(self, accent: str) -> None:
        self._swatch.set_accent(accent)

    def _minimize(self) -> None:
        self._window.showMinimized()

    def _toggle_max(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    # drag-to-move
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and not self._window.isMaximized():
            self._drag_offset = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None

    def mouseDoubleClickEvent(self, event) -> None:
        self._toggle_max()


