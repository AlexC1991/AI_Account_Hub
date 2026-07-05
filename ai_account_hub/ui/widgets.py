"""Small reusable widgets: frameless title bar, avatar, status pill, bars."""

from __future__ import annotations

import math
import weakref

from PySide6.QtCore import (
    Property, QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt,
    QTimer, Signal,
)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget,
)

from ai_account_hub.ui.tokens import accent_gradient, rgba, severity_color


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


class ToggleSwitch(QWidget):
    """A pill-track sliding-knob toggle (design 4c Cursor "Auto-run on/off")."""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, on_color: str = "#3eb268",
                 off_color: str = "#282f36", knob: str = "#ffffff") -> None:
        super().__init__()
        self._checked = bool(checked)
        self._on = on_color
        self._off = off_color
        self._knob = knob
        self.setFixedSize(34, 18)
        self.setCursor(Qt.PointingHandCursor)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, value: bool) -> None:
        value = bool(value)
        if value != self._checked:
            self._checked = value
            self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._checked = not self._checked
            self.update()
            self.toggled.emit(self._checked)
            event.accept()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        r = h / 2
        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, w, h), r, r)
        p.fillPath(track, QColor(self._on if self._checked else self._off))
        kr = h - 4
        kx = (w - kr - 2) if self._checked else 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self._knob))
        p.drawEllipse(QRectF(kx, 2, kr, kr))


class SegmentedControl(QWidget):
    """Always-visible N-way segmented control (design 4c Antigravity autonomy)."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], value: str, tokens: dict) -> None:
        super().__init__()
        self._options = list(options)
        self._value = value if value in {v for _, v in options} else (options[0][1] if options else "")
        self._tokens = tokens
        self._btns: dict[str, QPushButton] = {}
        row = QHBoxLayout(self)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        for label, val in self._options:
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, v=val: self._pick(v))
            row.addWidget(b)
            self._btns[val] = b
        self._restyle()

    def value(self) -> str:
        return self._value

    def _pick(self, value: str) -> None:
        changed = value != self._value
        self._value = value
        self._restyle()
        if changed:
            self.changed.emit(value)

    def _restyle(self) -> None:
        t = self._tokens
        grad = accent_gradient(t)
        self.setStyleSheet(
            f"QWidget{{background:{t['panel2']};border:1px solid {t['border']};border-radius:8px;}}"
        )
        for val, btn in self._btns.items():
            active = val == self._value
            if active:
                btn.setStyleSheet(
                    f"QPushButton{{background:{grad};color:{t['accentText']};border:none;"
                    f"border-radius:6px;padding:3px 9px;font-size:11px;font-weight:600;}}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton{{background:transparent;color:{t['text2']};border:none;"
                    f"border-radius:6px;padding:3px 9px;font-size:11px;}}"
                    f"QPushButton:hover{{color:{t['text']};background:{rgba(t['panelHover'], 0.6)};}}"
                )


class CyclePill(QPushButton):
    """Click-to-cycle pill (design 4c Claude permission mode; Shift+Tab style)."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], value: str, tokens: dict) -> None:
        super().__init__()
        self._options = list(options)
        self._value = value if value in {v for _, v in options} else (options[0][1] if options else "")
        self._tokens = tokens
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("variant", "ghost")
        self.clicked.connect(self._advance)
        self._sync()

    def value(self) -> str:
        return self._value

    def _advance(self) -> None:
        vals = [v for _, v in self._options]
        idx = vals.index(self._value) if self._value in vals else -1
        self._value = vals[(idx + 1) % len(vals)] if vals else ""
        self._sync()
        self.changed.emit(self._value)

    def _sync(self) -> None:
        label = next((lab for lab, v in self._options if v == self._value), self._value)
        self.setText(f"⟳  {label}")


class Spinner(QWidget):
    """A small indeterminate spinning ring (header 'Refreshing…' affordance)."""

    def __init__(self, color: str, size: int = 14) -> None:
        super().__init__()
        self._color = color
        self._angle = 0
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start(40)
        self.setVisible(True)

    def stop(self) -> None:
        self._timer.stop()
        self.setVisible(False)

    def _tick(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        m = 2.0
        rect = QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)
        pen = QPen(QColor(self._color))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, int(-self._angle * 16), int(270 * 16))


class SegmentedSlider(QWidget):
    """A flush segmented control (the header Coding|Accounts switch). The accent
    thumb is drawn in paintEvent from the *current* width every frame, so it is
    correct on the very first paint — there is no child-widget geometry to get
    mis-timed on startup. Switching animates the thumb's position smoothly."""

    changed = Signal(str)  # emits the newly-active key

    def __init__(self, options: list[tuple[str, str]], tokens: dict, height: int = 32) -> None:
        super().__init__()
        self._options = list(options)
        self._tokens = tokens
        self._active = options[0][1] if options else ""
        self._disabled: set[str] = set()
        self._pad = 3
        self._frac = float(self._index())  # thumb position in slot units
        self.setFixedHeight(height)
        row = QHBoxLayout(self)
        row.setContentsMargins(self._pad + 3, 0, self._pad + 3, 0)
        row.setSpacing(0)
        self._buttons: list[tuple[str, QPushButton]] = []
        for label, key in self._options:
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.setFlat(True)
            b.clicked.connect(lambda _c=False, k=key: self.set_active(k, emit=True))
            row.addWidget(b)
            self._buttons.append((key, b))
        self._anim = QPropertyAnimation(self, b"thumbFrac", self)
        self._anim.setDuration(190)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._restyle()

    def _get_frac(self) -> float:
        return self._frac

    def _set_frac(self, value: float) -> None:
        self._frac = float(value)
        self.update()

    thumbFrac = Property(float, _get_frac, _set_frac)

    def set_theme(self, tokens: dict) -> None:
        self._tokens = tokens
        self._restyle()
        self.update()

    def set_disabled(self, keys, tooltip: str = "") -> None:
        """Grey out and make un-clickable the given option keys (e.g. a section
        that isn't ready yet)."""
        self._disabled = set(keys)
        for key, btn in self._buttons:
            off = key in self._disabled
            btn.setCursor(Qt.ArrowCursor if off else Qt.PointingHandCursor)
            btn.setToolTip(tooltip if off else "")
        self._restyle()

    def set_active(self, key: str, emit: bool = False, animate: bool = True) -> None:
        if key not in {k for _, k in self._options} or key in self._disabled:
            return
        was = self._active
        self._active = key
        target = float(self._index())
        if animate and self.isVisible() and abs(self._frac - target) > 1e-3:
            self._anim.stop()
            self._anim.setStartValue(self._frac)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._anim.stop()
            self._frac = target
            self.update()
        self._restyle()
        if emit and key != was:
            self.changed.emit(key)

    def _index(self) -> int:
        return next((i for i, (_, k) in enumerate(self._options) if k == self._active), 0)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self._tokens
        w, h = self.width(), self.height()
        # Flush container: transparent fill + hairline border.
        border = QPainterPath()
        border.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 8, 8)
        pen = QPen(QColor(t["border"]))
        pen.setWidthF(1.0)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(border)
        # Accent thumb at the (possibly animating) fractional slot position.
        n = max(1, len(self._options))
        seg_w = (w - 2 * self._pad) / n
        x = self._pad + self._frac * seg_w
        thumb = QPainterPath()
        thumb.addRoundedRect(QRectF(x, self._pad, seg_w, h - 2 * self._pad), 6, 6)
        p.setPen(Qt.NoPen)
        p.fillPath(thumb, QColor(t["accent"]))

    def _restyle(self) -> None:
        t = self._tokens
        for key, btn in self._buttons:
            if key in self._disabled:
                btn.setStyleSheet(
                    f"QPushButton{{background:transparent;border:none;color:{t['text3']};"
                    f"font-size:12px;font-weight:600;padding:0 16px;}}"
                )
                continue
            active = key == self._active
            color = t["accentText"] if active else t["text2"]
            btn.setStyleSheet(
                f"QPushButton{{background:transparent;border:none;color:{color};"
                f"font-size:12px;font-weight:600;padding:0 16px;}}"
                + ("" if active else f"QPushButton:hover{{color:{t['text']};}}")
            )
