"""Interactive controls: toggle switch, segmented control, segmented slider, cycle pill, and the spinner."""

from __future__ import annotations

from PySide6.QtCore import (
    Property, QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer, Signal,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from ai_account_hub.ui.tokens import rgba


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
        self.setStyleSheet(
            f"QWidget{{background:{t['panel2']};border:1px solid {t['border']};border-radius:8px;}}"
        )
        for val, btn in self._btns.items():
            active = val == self._value
            if active:
                btn.setStyleSheet(
                    f"QPushButton{{background:{t['accent']};color:{t['accentText']};border:none;"
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
    """A flush segmented control for switching top-level Hub screens. The accent
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
