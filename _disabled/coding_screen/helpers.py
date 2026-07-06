"""Free-standing helpers for the Coding screen: time/preview and color
formatting, the ComposerInput widget, the rail nav-row factory, and the
CODING_UI_ENABLED feature flag."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel, QPlainTextEdit, QPushButton,
)


# The Coding view is deactivated in the UI for this release (see
# main_window's SegmentedSlider.set_disabled + the Window menu). Because the
# screen is built but can never be shown, its native background loaders — the
# project/thread discovery and the per-thread history reader, each of which
# reads the provider's on-disk state on a worker thread — are gated off here so
# the app does no provider I/O for a surface the user cannot reach. Flip this to
# True when the Coding workbench is finished and re-enabled in the UI.
CODING_UI_ENABLED = False

def _relative_time(updated: object) -> str:
    """Compact 'time ago' from an epoch float or ISO-8601 string ('7m ago')."""
    import datetime as _dt

    ts: float = 0.0
    if isinstance(updated, (int, float)):
        ts = float(updated)
    elif isinstance(updated, str) and updated.strip():
        raw = updated.strip()
        try:
            ts = float(raw)
        except ValueError:
            try:
                ts = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return ""
    if ts <= 0:
        return ""
    # Epoch may be seconds or milliseconds; normalise ms → s.
    if ts > 1e12:
        ts /= 1000.0
    secs = max(0.0, _dt.datetime.now().timestamp() - ts)
    if secs < 60:
        return "just now"
    mins = secs / 60
    if mins < 60:
        return f"{int(mins)}m ago"
    hours = mins / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    if days < 7:
        return f"{int(days)}d ago"
    weeks = days / 7
    if weeks < 5:
        return f"{int(weeks)}w ago"
    months = days / 30
    if months < 12:
        return f"{int(months)}mo ago"
    return f"{int(days / 365)}y ago"


def _thread_preview(th: dict) -> str:
    """First line of a thread's title/preview for the sidebar."""
    raw = str(th.get("preview") or th.get("title") or th.get("name") or "Untitled chat")
    line = raw.splitlines()[0].strip() if raw.strip() else "Untitled chat"
    return line or "Untitled chat"


def _soft(hexcolor: str, alpha: float = 0.16) -> str:
    from ai_account_hub.ui.tokens import rgba
    return rgba(hexcolor, alpha)


def _wrap(text: str, t: dict) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color:{t['text2']};font-size:11px;")
    return lab


def _faint(text: str, t: dict) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color:{t['text3']};font-size:9px;")
    return lab


def _colorize_diff(diff: str) -> str:
    lines = []
    for line in diff.splitlines():
        esc = html.escape(line)
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(f'<span style="color:#45b164">{esc}</span>')
        elif line.startswith("-") and not line.startswith("---"):
            lines.append(f'<span style="color:#e1514e">{esc}</span>')
        elif line.startswith(("diff --git", "@@", "+++", "---")):
            lines.append(f'<span style="color:#2c90e8">{esc}</span>')
        else:
            lines.append(esc)
    return "<br>".join(lines)


class ComposerInput(QPlainTextEdit):
    """Text input where Enter sends and Shift+Enter inserts a newline."""

    def __init__(self, on_submit) -> None:
        super().__init__()
        self._on_submit = on_submit

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            event.accept()
            self._on_submit()
            return
        super().keyPressEvent(event)


def _nav_row(icon: str, text: str, shortcut: str = "") -> QPushButton:
    btn = QPushButton(f"  {icon}   {text}")
    btn.setObjectName("navRow")
    btn.setCursor(Qt.PointingHandCursor)
    if shortcut:
        btn.setText(f"  {icon}   {text}")
    return btn


