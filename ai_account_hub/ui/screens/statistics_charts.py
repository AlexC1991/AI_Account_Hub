"""Charts, compact panels, and background scanning for Statistics."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QScrollArea, QSizePolicy, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QStackedWidget,
)

from ai_account_hub import data, demo_data
from ai_account_hub.core import hub_core
from ai_account_hub.core.benchmark_analytics import build_benchmark_analytics
from ai_account_hub.core.benchmark_view import (
    aggregate_base_model_groups,
    base_model_key,
    build_benchmark_view,
    build_head_to_head,
    productivity_density_csv,
)
from ai_account_hub.core.model_analytics import reconcile_claude_history
from ai_account_hub.ui.widgets import ElidedLabel, Spinner, make_button


MODEL_COLORS = (
    "#45a3ff", "#55c47d", "#e2b93f", "#d47be8", "#ef8354", "#42b8c5",
    "#8b9bea", "#df6c88", "#82b05a", "#bd8b62", "#7ac6a6", "#b58ce2",
)
METRIC_COLORS = {
    "edits": "#45a3ff", "filesChanged": "#55c47d", "tests": "#e2b93f",
    "commands": "#d47be8", "taskTokenMinimum": "#45b8ce",
    "taskTokenQ1": "#55c47d", "taskTokenMedian": "#e2b93f",
    "taskTokenQ3": "#d47be8", "taskTokenMaximum": "#ef8354",
    "durationMinimum": "#45b8ce", "durationQ1": "#55c47d",
    "durationMedian": "#e2b93f", "durationQ3": "#d47be8",
    "durationMaximum": "#ef8354",
}
METRIC_LABELS = {
    "edits": "Edits", "filesChanged": "Files", "tests": "Tests",
    "commands": "Commands", "taskTokenMinimum": "Minimum",
    "taskTokenQ1": "Q1", "taskTokenMedian": "Median",
    "taskTokenQ3": "Q3", "taskTokenMaximum": "Maximum",
    "durationMinimum": "Minimum", "durationQ1": "Q1",
    "durationMedian": "Median", "durationQ3": "Q3",
    "durationMaximum": "Maximum",
}
DISPLAY_METRIC_LABELS = {
    "totalTokens": "Tokens",
    "completedTasks": "Completed tasks",
    "shortBurn": "5h limit burn",
    "weeklyBurn": "Weekly limit burn",
    "edits": "Edits",
    "filesChanged": "Files",
    "tests": "Tests",
    "commands": "Commands",
    "activeMs": "Active time",
    "tokensPerTask": "Tokens per completed task",
    "tasksPerMillion": "Tasks per 1M tokens",
    "tasksPerSession": "Tasks per 5h capacity",
    "weeklyBurnPerTask": "Weekly burn per task",
    "observations": "Observed tasks",
    "inputTokens": "Input tokens",
    "cachedInputTokens": "Cached input tokens",
    "cacheCreationTokens": "Cache write tokens",
    "reasoningTokens": "Reasoning tokens",
    "outputTokens": "Output tokens",
    "unclassifiedTokens": "Unclassified tokens",
    "taskTokenMinimum": "Minimum task tokens",
    "taskTokenQ1": "Task-token first quartile",
    "taskTokenMedian": "Median task tokens",
    "taskTokenQ3": "Task-token third quartile",
    "taskTokenMaximum": "Maximum task tokens",
    "durationMinimum": "Minimum task duration",
    "durationQ1": "Task-duration first quartile",
    "durationMedian": "Median task duration",
    "durationQ3": "Task-duration third quartile",
    "durationMaximum": "Maximum task duration",
}
TOKEN_SEGMENTS = (
    ("inputTokens", "Input", "#45b8ce"),
    ("cachedInputTokens", "Cached", "#55c47d"),
    ("cacheCreationTokens", "Cache write", "#d8b044"),
    ("reasoningTokens", "Reasoning", "#a879e6"),
    ("outputTokens", "Output", "#e77f52"),
    ("unclassifiedTokens", "Unclassified", "#84909a"),
)
LINE_CHART_VIEWS = (
    ("Work tokens", "Work tokens over time", "Non-cache tokens (excludes cache re-reads)", "line", "workTokens", ()),
    ("Completed tasks", "Completed tasks over time", "Completed work recorded for each model", "line", "completedTasks", ()),
    ("5h limit burn", "5h limit burn over time", "Measured movement from trustworthy intervals", "line", "shortBurn", ()),
    ("Weekly limit burn", "Weekly limit burn over time", "Positive movement with reset decreases excluded", "line", "weeklyBurn", ()),
    ("Edits", "Edit activity over time", "Observed edit operations by model", "line", "edits", ()),
    ("Files", "File activity over time", "Observed changed-file counts by model", "line", "filesChanged", ()),
    ("Tests", "Test activity over time", "Observed test commands by model", "line", "tests", ()),
    ("Commands", "Command activity over time", "Observed command activity by model", "line", "commands", ()),
    ("Active time", "Active task time over time", "Time recorded in model sessions", "line", "activeMs", ()),
)
BAR_CHART_VIEWS = (
    ("Completed work", "Completed work", "Task completions by used model", "bar", "completedTasks", ()),
    ("Work tokens by model", "Work tokens by model", "Non-cache tokens; cache re-reads excluded", "bar", "workTokens", ()),
    ("5h limit burn", "5h limit burn by model", "Measured percentage-point movement", "bar", "shortBurn", ()),
    ("Weekly limit burn", "Weekly limit burn by model", "Reset decreases and long gaps excluded", "bar", "weeklyBurn", ()),
    ("Token category mix", "Token category mix", "Input, cache, reasoning, output and unclassified", "stack", "totalTokens", ()),
    ("Engineering activity", "Engineering activity bundle", "Edits, files, tests and commands remain separate", "multi_bar", "", ("edits", "filesChanged", "tests", "commands")),
    ("Tokens per task", "Tokens per completed task", "A resource ratio, not a quality score", "bar", "tokensPerTask", ()),
    ("Tasks per 1M tokens", "Tasks per 1M tokens", "Observed completions normalized by tokens", "bar", "tasksPerMillion", ()),
    ("Task token quartiles", "Task token distribution", "Minimum, quartiles, median and maximum", "multi_bar", "", ("taskTokenMinimum", "taskTokenQ1", "taskTokenMedian", "taskTokenQ3", "taskTokenMaximum")),
    ("Task duration quartiles", "Task duration distribution", "Observed task-span quartiles", "multi_bar", "", ("durationMinimum", "durationQ1", "durationMedian", "durationQ3", "durationMaximum")),
)

OVERVIEW_LINE_VIEWS = tuple(
    item for item in LINE_CHART_VIEWS
    if item[0] in {"Token activity", "Completed tasks", "5h limit burn", "Weekly limit burn"}
)
OVERVIEW_BAR_VIEWS = tuple(
    item for item in BAR_CHART_VIEWS
    if item[0] in {"Completed work", "Tokens by model", "5h limit burn", "Weekly limit burn"}
)
MODEL_LINE_VIEWS = tuple(
    item for item in LINE_CHART_VIEWS
    if item[0] in {"Token activity", "Completed tasks", "Active time"}
)
MODEL_BAR_VIEWS = tuple(
    item for item in BAR_CHART_VIEWS
    if item[0] in {
        "Token category mix", "Tokens per task", "Tasks per 1M tokens",
        "Task token quartiles", "Task duration quartiles",
    }
)
PRODUCTIVITY_LINE_VIEWS = tuple(
    item for item in LINE_CHART_VIEWS
    if item[0] in {"Edits", "Files", "Tests", "Commands", "Active time", "5h limit burn", "Weekly limit burn"}
)
PRODUCTIVITY_BAR_VIEWS = tuple(
    item for item in BAR_CHART_VIEWS
    if item[0] in {"Engineering activity", "5h limit burn", "Weekly limit burn", "Tokens per task", "Tasks per 1M tokens"}
)
COMPARE_LINE_VIEWS = tuple(
    item for item in LINE_CHART_VIEWS
    if item[0] in {
        "Token activity", "Completed tasks", "Edits", "Files", "Tests",
        "Commands", "Active time", "5h limit burn", "Weekly limit burn",
    }
)
COMPARE_BAR_VIEWS = (
    ("Work tokens", "Work tokens by model", "Non-cache totals from zero; differences use the baseline", "comparison_bar", "workTokens", ()),
    ("Completed tasks", "Completed tasks by model", "Completed work totals from zero", "comparison_bar", "completedTasks", ()),
    ("Edits", "Edits by model", "Observed edit totals from zero", "comparison_bar", "edits", ()),
    ("Files", "Changed files by model", "Unique changed-file totals from zero", "comparison_bar", "filesChanged", ()),
    ("Tests", "Tests by model", "Observed test totals from zero", "comparison_bar", "tests", ()),
    ("Commands", "Commands by model", "Observed command totals from zero", "comparison_bar", "commands", ()),
    ("Active time", "Active time by model", "Recorded session time from zero", "comparison_bar", "activeMs", ()),
    ("5h limit burn", "5h limit burn by model", "Measured percentage-point movement from zero", "comparison_bar", "shortBurn", ()),
    ("Weekly limit burn", "Weekly limit burn by model", "Measured percentage-point movement from zero", "comparison_bar", "weeklyBurn", ()),
    ("Tokens per task", "Tokens per completed task", "Resources per completion from zero", "comparison_bar", "tokensPerTask", ()),
    ("Tasks per 1M", "Tasks per 1M tokens", "Completion density from zero", "comparison_bar", "tasksPerMillion", ()),
)


def _metric_label(metric: object) -> str:
    """Return readable UI copy for provider metric identifiers."""
    key = str(metric or "").strip()
    if not key:
        return "Value"
    known = DISPLAY_METRIC_LABELS.get(key) or METRIC_LABELS.get(key)
    if known:
        return known
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key)
    words = re.sub(r"[_-]+", " ", words)
    return " ".join(words.split()).capitalize()


def _friendly_work_scope(value: object) -> str:
    """Turn analytics provenance into concise user-facing source copy."""

    text = str(value or "").strip()
    known = {
        "shared Codex history": "Shared Codex activity",
        "visible account history": "Account activity",
        "isolated profile history": "Profile activity",
    }
    return known.get(text, text.replace(" history", " activity"))


def _label(text: str, kind: str = "", *, bold: bool = False, size: int = 0) -> QLabel:
    label = QLabel(text)
    if kind:
        label.setObjectName(kind)
    rules = []
    if bold:
        rules.append("font-weight:600")
    if size:
        rules.append(f"font-size:{size}px")
    if rules:
        label.setStyleSheet(";".join(rules))
    return label


def _inline_copy(
    title: str,
    caption: str,
    *,
    title_size: int = 12,
) -> tuple[QWidget, QLabel, ElidedLabel]:
    """Return one compact title/caption line that elides before controls clip."""
    host = QWidget()
    host.setMinimumWidth(0)
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(7)
    title_label = _label(title, bold=True, size=title_size)
    title_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
    separator = _label("·", "faint", bold=True)
    caption_label = ElidedLabel(caption)
    caption_label.setObjectName("faint")
    row.addWidget(title_label)
    row.addWidget(separator)
    row.addWidget(caption_label, 1)
    return host, title_label, caption_label


def _format_tokens(value: int | float) -> str:
    amount = max(0, float(value or 0))
    if amount >= 1_000_000_000:
        number = amount / 1_000_000_000
        return f"{number:.2f}B" if number < 10 else f"{number:.1f}B"
    if amount >= 1_000_000:
        number = amount / 1_000_000
        return f"{number:.1f}M" if number < 100 else f"{number:.0f}M"
    if amount >= 1_000:
        return f"{amount / 1_000:.1f}K"
    return str(int(amount))


def _format_duration(value_ms: int | float | None) -> str:
    if not value_ms:
        return "Not exposed"
    seconds = int(float(value_ms) / 1000)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, remainder = divmod(minutes, 60)
    return f"{hours}h {remainder:02d}m"


def _format_number(value: float | int | None, decimals: int = 1) -> str:
    if value is None:
        return "Not exposed"
    amount = float(value)
    if abs(amount) >= 1_000_000:
        return _format_tokens(amount)
    if abs(amount) >= 100:
        return f"{amount:,.0f}"
    return f"{amount:,.{decimals}f}"


def _format_points(value: float | int | None) -> str:
    amount = max(0.0, float(value or 0))
    return f"{amount:,.0f} pts" if amount >= 100 else f"{amount:,.1f} pts"


def _format_metric_value(value: object, metric: str, *, category: str = "") -> str:
    """Format a chart value using the same semantic unit as its axis."""
    metric_text = str(metric or "")
    lowered = metric_text.lower()
    if category == "tokens" or "token" in lowered:
        return _format_tokens(float(value or 0))
    if "active time" in lowered or "duration" in lowered:
        return _format_duration(float(value or 0))
    if "burn" in lowered:
        return _format_points(float(value or 0))
    return _format_number(float(value or 0))


def _chart_rows(rows: list[dict], maximum: int | None = None) -> list[dict]:
    """Return every distinct used model/effort stream; never silently truncate."""
    output: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        key = str(row.get("filterKey") or "").lower()
        if key and key not in seen and int(row.get("totalTokens") or 0) > 0:
            output.append(row)
            seen.add(key)
    return output


def _metric_value(group: dict, metric: str) -> float:
    if metric == "tokensPerTask":
        return float((group.get("normalized") or {}).get("tokensPerCompletedTask") or 0)
    if metric == "tasksPerMillion":
        return float((group.get("normalized") or {}).get("tasksPerMillionTokens") or 0)
    distribution_fields = {
        "taskTokenMinimum": ("taskTokenDistribution", "minimum"),
        "taskTokenQ1": ("taskTokenDistribution", "q1"),
        "taskTokenMedian": ("taskTokenDistribution", "median"),
        "taskTokenQ3": ("taskTokenDistribution", "q3"),
        "taskTokenMaximum": ("taskTokenDistribution", "maximum"),
        "durationMinimum": ("durationDistribution", "minimum"),
        "durationQ1": ("durationDistribution", "q1"),
        "durationMedian": ("durationDistribution", "median"),
        "durationQ3": ("durationDistribution", "q3"),
        "durationMaximum": ("durationDistribution", "maximum"),
    }
    if metric in distribution_fields:
        source, field = distribution_fields[metric]
        return float((group.get(source) or {}).get(field) or 0)
    return float(group.get(metric) or 0)


def _day_value(bucket: dict, metric: str) -> float:
    aliases = {
        "totalTokens": "tokens", "completedTasks": "tasks", "filesChanged": "files",
    }
    return float(bucket.get(aliases.get(metric, metric)) or 0)


class AnalyticsWorker(QThread):
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, profiles: list[dict], parent=None) -> None:
        super().__init__(parent)
        self._profiles = [dict(profile) for profile in profiles]
        self._process: subprocess.Popen | None = None

    def _build_in_subprocess(self) -> dict:
        """Build analytics without letting parser CPU work stall Qt's GIL."""
        with tempfile.TemporaryDirectory(prefix="ai-hub-analytics-") as directory:
            root = Path(directory)
            input_path = root / "profiles.json"
            output_path = root / "snapshot.json"
            input_path.write_text(
                json.dumps(self._profiles, default=str, ensure_ascii=True),
                encoding="utf-8",
            )
            command = [
                sys.executable,
                "-m",
                "ai_account_hub.core.analytics_worker_process",
                str(input_path),
                str(output_path),
            ]
            flags = 0
            if os.name == "nt":
                flags = (
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
                )
            repo_root = Path(__file__).resolve().parents[3]
            cwd = repo_root if (repo_root / "ai_account_hub").is_dir() else Path.cwd()
            self._process = subprocess.Popen(
                command,
                cwd=str(cwd),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=flags,
            )
            while self._process.poll() is None:
                if self.isInterruptionRequested():
                    self._process.terminate()
                    self._process.wait(timeout=5)
                    raise RuntimeError("Analytics refresh cancelled")
                self.msleep(50)
            stderr = self._process.stderr.read().strip() if self._process.stderr else ""
            exit_code = int(self._process.returncode or 0)
            self._process = None
            if exit_code != 0 or not output_path.is_file():
                raise RuntimeError(stderr or f"Analytics process exited with {exit_code}")
            return json.loads(output_path.read_text(encoding="utf-8"))

    def run(self) -> None:
        try:
            # Demo mode must remain completely isolated from provider history.
            # Besides keeping screenshots private, this makes the Help demo
            # deterministic and useful on a machine with no configured tools.
            if demo_data.DEMO:
                snapshot = demo_data.demo_benchmark_analytics(self._profiles)
            elif (
                not getattr(sys, "frozen", False)
                and os.environ.get("AI_HUB_ANALYTICS_IN_PROCESS") != "1"
            ):
                snapshot = self._build_in_subprocess()
            else:
                snapshot = build_benchmark_analytics(
                    self._profiles,
                    cancelled=self.isInterruptionRequested,
                )
            if not self.isInterruptionRequested():
                if not demo_data.DEMO:
                    reconcile_claude_history(self._profiles, snapshot)
                self.completed.emit(snapshot)
        except Exception as error:
            if not self.isInterruptionRequested():
                self.failed.emit(str(error))


class StatTile(QFrame):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setMinimumHeight(80)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(2)
        title_label = ElidedLabel(title)
        title_label.setObjectName("faint")
        title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(title_label)
        self.value = _label("-", bold=True, size=18)
        self.note = ElidedLabel("")
        self.note.setObjectName("faint")
        self.value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.value)
        layout.addWidget(self.note)

    def set_data(self, value: str, note: str) -> None:
        self.value.setText(value)
        self.note.setText(note)


class ChartHoverCard(QFrame):
    """Stable in-chart tooltip that does not flicker between mouse events."""

    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setObjectName("chartHoverCard")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedWidth(238)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        self.title = _label("", "chartHoverTitle", bold=True, size=11)
        self.body = _label("", "chartHoverBody")
        self.body.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        self.hide()
        self.apply_theme()

    def show_copy(self, title: str, body: str, anchor: QPointF) -> None:
        self.title.setText(title)
        self.body.setText(body)
        self.adjustSize()
        parent = self.parentWidget()
        if parent is None:
            return
        x = int(anchor.x()) + 14
        y = int(anchor.y()) + 14
        if x + self.width() > parent.width() - 8:
            x = int(anchor.x()) - self.width() - 14
        if y + self.height() > parent.height() - 8:
            y = int(anchor.y()) - self.height() - 14
        self.move(max(8, x), max(8, y))
        self.show()
        self.raise_()

    def apply_theme(self) -> None:
        tokens = self._theme.tokens
        self.setStyleSheet(
            f"QFrame#chartHoverCard{{background:{tokens['panel']};"
            f"border:1px solid {tokens['borderStrong']};border-radius:7px;}}"
            f"QLabel#chartHoverTitle{{color:{tokens['text']};background:transparent;border:0;}}"
            f"QLabel#chartHoverBody{{color:{tokens['text2']};background:transparent;border:0;}}"
        )


class BenchmarkChart(QWidget):
    """Theme-aware model-only chart with line, bar, stack, box, and scatter modes."""

    hovered = Signal(dict)

    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._groups: list[dict] = []
        self._kind = "line"
        self._metric = "totalTokens"
        self._segments: list[str] = []
        self._points: list[tuple[QPointF, dict]] = []
        self._hit_targets: list[tuple[QRectF, dict]] = []
        self._legend_hits: list[tuple[QRectF, str, str]] = []
        self._legend_overflow: tuple[QRectF, list[str]] | None = None
        self._hidden_keys: set[str] = set()
        self._x_zoom = 1.0
        self._x_pan = 0.0
        self._y_zoom = 1.0
        self._drag: QPoint | None = None
        self.setMinimumHeight(330)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._hover_card = ChartHoverCard(theme, self)

    def set_chart(
        self,
        kind: str,
        groups: list[dict],
        metric: str,
        segments: list[str] | None = None,
    ) -> None:
        self._kind = kind
        self._groups = list(groups)
        self._metric = metric
        self._segments = list(segments or [])
        current_keys = {str(group.get("filterKey") or "") for group in self._groups}
        self._hidden_keys.intersection_update(current_keys)
        self._points = []
        self._hit_targets = []
        self._hover_card.hide()
        self._x_zoom = 1.0
        self._x_pan = 0.0
        self._y_zoom = 1.0
        self.update()

    def reset_view(self) -> None:
        self._x_zoom = 1.0
        self._x_pan = 0.0
        self._y_zoom = 1.0
        self.update()

    def save_png(self, path: str | Path) -> bool:
        return bool(self.grab().save(str(path), "PNG"))

    def chart_state(self) -> tuple[str, list[dict], str, list[str]]:
        return self._kind, list(self._groups), self._metric, list(self._segments)

    def hidden_keys(self) -> set[str]:
        return set(self._hidden_keys)

    def set_hidden_keys(self, keys: set[str]) -> None:
        available = {str(group.get("filterKey") or "") for group in self._groups}
        self._hidden_keys = set(keys) & available
        self.update()

    def apply_theme(self) -> None:
        self._hover_card.apply_theme()
        self.update()

    def _active_groups(self) -> list[dict]:
        return [
            group for group in self._groups
            if str(group.get("filterKey") or "") not in self._hidden_keys
        ]

    def _group_color(self, group: dict) -> QColor:
        key = str(group.get("filterKey") or "")
        index = next(
            (position for position, item in enumerate(self._groups)
             if str(item.get("filterKey") or "") == key),
            0,
        )
        return self._color(index)

    def _plot(self) -> QRectF:
        return QRectF(48, 58, max(40, self.width() - 64), max(40, self.height() - 94))

    def _color(self, index: int) -> QColor:
        return QColor(MODEL_COLORS[index % len(MODEL_COLORS)])

    def _draw_y_labels(self, painter: QPainter, plot: QRectF, maximum: float) -> None:
        painter.setPen(QColor(self._theme.tokens["text3"]))
        for step in range(5):
            value = maximum * step / 4
            if "token" in self._metric.lower() or any("token" in metric.lower() for metric in self._segments):
                label = _format_tokens(value)
            elif self._metric == "activeMs" or any(metric.startswith("duration") for metric in self._segments):
                label = _format_duration(value)
            else:
                label = _format_number(value, 0)
            y = plot.bottom() - plot.height() * step / 4
            painter.drawText(QRectF(0, y - 8, 42, 16), Qt.AlignRight | Qt.AlignVCenter, label)

    @staticmethod
    def point_text(payload: dict) -> str:
        raw_day = str(payload.get("date") or "")
        try:
            day = dt.date.fromisoformat(raw_day).strftime("%d %b %Y")
        except ValueError:
            day = (
                _metric_label(raw_day)
                if re.search(r"[a-z][A-Z]|[_-]", raw_day)
                else raw_day
            )
        value = payload.get("value", payload.get("tokens", 0))
        metric = _metric_label(payload.get("metricLabel") or "Tokens")
        formatted = _format_metric_value(
            value,
            metric,
            category=str(payload.get("valueKind") or ""),
        )
        lines = [day, str(payload.get("model") or "Model"), f"{metric}: {formatted}"]
        lines.extend(str(line) for line in payload.get("detailLines", []) if line)
        return "\n".join(lines)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        tokens = self._theme.tokens
        painter.fillRect(self.rect(), QColor(tokens["panel2"]))
        plot = self._plot()
        painter.setPen(QPen(QColor(tokens["border"]), 1))
        for step in range(5):
            y = plot.bottom() - plot.height() * step / 4
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        self._draw_legend(painter)
        self._points = []
        self._hit_targets = []
        if not self._groups:
            painter.setPen(QColor(tokens["text3"]))
            painter.drawText(plot, Qt.AlignCenter, "No model activity in this range")
            return
        if not self._active_groups():
            painter.setPen(QColor(tokens["text3"]))
            painter.drawText(plot, Qt.AlignCenter, "No visible model series")
            return
        if self._kind == "line":
            self._draw_line(painter, plot)
        elif self._kind in {"bar", "multi_bar"}:
            self._draw_bar(painter, plot)
        elif self._kind == "comparison_bar":
            self._draw_comparison_bar(painter, plot)
        elif self._kind in {"stack", "stack100"}:
            self._draw_stack(painter, plot)
        elif self._kind == "box":
            self._draw_box(painter, plot)
        elif self._kind in {"scatter", "community_scatter"}:
            self._draw_scatter(painter, plot)
        self._draw_scale_badge(painter, plot)

    def legend_entries(self) -> list[tuple[str, str, QColor, bool]]:
        if self._kind == "multi_bar":
            return [
                (str(metric), _metric_label(metric), QColor(METRIC_COLORS.get(str(metric), "#84909a")), False)
                for metric in self._segments
            ]
        if self._kind in {"stack", "stack100"}:
            return [
                (key, label, QColor(color), False)
                for key, label, color in TOKEN_SEGMENTS
            ]
        if self._kind == "comparison_bar":
            return [
                (
                    str(group.get("filterKey") or ""),
                    str(group.get("modelLabel") or "Model"),
                    QColor(self._theme.tokens["accent"]) if index == 0 else self._color(index),
                    True,
                )
                for index, group in enumerate(self._groups)
            ]
        return [
            (
                str(group.get("filterKey") or ""),
                str(group.get("modelLabel") or "Model"),
                self._color(index),
                True,
            )
            for index, group in enumerate(self._groups)
        ]

    def _draw_legend(self, painter: QPainter) -> None:
        tokens = self._theme.tokens
        x, y = 8, 16
        self._legend_hits = []
        self._legend_overflow = None
        painter.setFont(self.font())
        entries = self.legend_entries()
        for index, (key, full_label, base_color, clickable) in enumerate(entries):
            label = full_label
            label = painter.fontMetrics().elidedText(label, Qt.ElideRight, 142)
            width = painter.fontMetrics().horizontalAdvance(label) + 24
            if x + width > self.width() - 8:
                x = 8
                y += 18
                if y > 34:
                    hidden_labels = [entry[1] for entry in entries[index:]]
                    overflow_text = f"+{len(hidden_labels)} more"
                    overflow_width = painter.fontMetrics().horizontalAdvance(overflow_text) + 18
                    overflow_rect = QRectF(
                        max(8, self.width() - overflow_width - 8),
                        23,
                        overflow_width,
                        18,
                    )
                    painter.setPen(QColor(tokens["accent"]))
                    painter.drawText(overflow_rect, Qt.AlignVCenter, overflow_text)
                    self._legend_overflow = (overflow_rect, hidden_labels)
                    break
            hidden = clickable and key in self._hidden_keys
            color = QColor(base_color)
            color.setAlpha(75 if hidden else 255)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(x, y - 7, 8, 8), 2, 2)
            text_color = QColor(tokens["text3"] if hidden else tokens["text2"])
            painter.setPen(text_color)
            label_rect = QRectF(x + 13, y - 11, width - 13, 18)
            painter.drawText(label_rect, Qt.AlignVCenter, label)
            if clickable:
                self._legend_hits.append((QRectF(x, y - 11, width, 18), key, full_label))
            x += width

    def _scaled_x(self, index: int, count: int, plot: QRectF) -> float:
        if count <= 1:
            return plot.center().x()
        logical = index / (count - 1)
        return plot.left() + (logical * self._x_zoom + self._x_pan) * plot.width()

    def _visible_maximum(self, maximum: float) -> float:
        """Scale the value axis without changing or normalizing source values."""
        return max(float(maximum or 1) / self._y_zoom, 1e-9)

    def _draw_scale_badge(self, painter: QPainter, plot: QRectF) -> None:
        parts = []
        if self._y_zoom > 1.001:
            parts.append(f"Value {self._y_zoom:.1f}x")
        if self._x_zoom > 1.001:
            parts.append(f"Time {self._x_zoom:.1f}x")
        if not parts:
            return
        text = "  |  ".join(parts)
        width = painter.fontMetrics().horizontalAdvance(text) + 18
        rect = QRectF(plot.right() - width, plot.top() + 6, width, 22)
        painter.setPen(QPen(QColor(self._theme.tokens["borderStrong"]), 1))
        painter.setBrush(QColor(self._theme.tokens["panel"]))
        painter.drawRoundedRect(rect, 5, 5)
        painter.setPen(QColor(self._theme.tokens["text2"]))
        painter.drawText(rect, Qt.AlignCenter, text)

    def _draw_line(self, painter: QPainter, plot: QRectF) -> None:
        groups = self._active_groups()
        days = sorted({day for group in groups for day in group.get("days", {})})
        if not days:
            return
        raw_maximum = max(
            [_day_value((group.get("days") or {}).get(day, {}), self._metric) for group in groups for day in days]
            or [1]
        ) or 1
        maximum = self._visible_maximum(raw_maximum)
        self._draw_y_labels(painter, plot, maximum)
        painter.save()
        painter.setClipRect(plot.adjusted(-4, -4, 4, 4))
        for group in groups:
            path = QPainterPath()
            active = False
            for index, day in enumerate(days):
                x = self._scaled_x(index, len(days), plot)
                value = _day_value((group.get("days") or {}).get(day, {}), self._metric)
                y = plot.bottom() - value / maximum * plot.height()
                point = QPointF(x, y)
                path.moveTo(point) if not active else path.lineTo(point)
                active = True
                if value > 0 and plot.left() <= x <= plot.right() and y >= plot.top() - 4:
                    self._points.append((point, {
                        "date": day, "model": group.get("modelLabel"), "value": value,
                        "metricLabel": _metric_label(self._metric),
                    }))
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(self._group_color(group))
                    painter.drawEllipse(point, 3.2, 3.2)
            painter.setPen(QPen(self._group_color(group), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        painter.restore()
        painter.setPen(QColor(self._theme.tokens["text3"]))
        painter.drawText(QRectF(plot.left(), plot.bottom() + 5, plot.width(), 18), Qt.AlignLeft, days[0])
        painter.drawText(QRectF(plot.left(), plot.bottom() + 5, plot.width(), 18), Qt.AlignRight, days[-1])

    def _draw_bar(self, painter: QPainter, plot: QRectF) -> None:
        groups = self._active_groups()
        metrics = self._segments or [self._metric]
        values = [[_metric_value(group, metric) for metric in metrics] for group in groups]
        maximum = self._visible_maximum(max([value for row in values for value in row] or [1]) or 1)
        self._draw_y_labels(painter, plot, maximum)
        group_width = plot.width() / max(1, len(groups))
        bar_width = min(34.0, max(5.0, group_width * 0.7 / max(1, len(metrics))))
        for group_index, (group, row) in enumerate(zip(groups, values)):
            center = plot.left() + group_width * (group_index + 0.5)
            for metric_index, (metric, value) in enumerate(zip(metrics, row)):
                x = center - len(metrics) * bar_width / 2 + metric_index * bar_width
                height = min(plot.height(), value / maximum * plot.height())
                color = (
                    self._group_color(group)
                    if len(metrics) == 1
                    else QColor(METRIC_COLORS.get(metric, "#84909a"))
                )
                painter.setPen(Qt.NoPen)
                painter.setBrush(color)
                painter.drawRoundedRect(QRectF(x, plot.bottom() - height, bar_width - 2, height), 3, 3)
                payload = {
                    "date": metric, "model": group.get("modelLabel"), "value": value,
                    "metricLabel": _metric_label(metric),
                }
                bar_rect = QRectF(x, plot.bottom() - height, bar_width - 2, max(3.0, height))
                self._points.append((QPointF(x + bar_width / 2, plot.bottom() - height), payload))
                self._hit_targets.append((bar_rect.adjusted(-4, -4, 4, 4), payload))
            label = painter.fontMetrics().elidedText(
                str(group.get("modelLabel") or "Model"),
                Qt.ElideRight,
                max(28, int(group_width - 6)),
            )
            painter.setPen(QColor(self._theme.tokens["text3"]))
            painter.drawText(
                QRectF(center - group_width / 2, plot.bottom() + 5, group_width, 18),
                Qt.AlignHCenter | Qt.AlignTop,
                label,
            )

    def _draw_comparison_bar(self, painter: QPainter, plot: QRectF) -> None:
        groups = self._active_groups()
        if not groups:
            return
        baseline = _metric_value(groups[0], self._metric)
        values = [_metric_value(group, self._metric) for group in groups]
        maximum = self._visible_maximum(max(values or [1]) or 1)
        tokens = self._theme.tokens
        self._draw_y_labels(painter, plot, maximum)
        label_metric = _metric_label(self._metric)
        group_width = plot.width() / max(1, len(groups))
        bar_width = min(58.0, max(18.0, group_width * 0.46))
        for index, (group, value) in enumerate(zip(groups, values)):
            center = plot.left() + group_width * (index + 0.5)
            height = min(plot.height() - 24, value / maximum * max(1.0, plot.height() - 24))
            top = plot.bottom() - height
            color = QColor(tokens["accent"]) if index == 0 else self._group_color(group)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            bar_rect = QRectF(center - bar_width / 2, top, bar_width, max(3.0, height))
            painter.drawRoundedRect(bar_rect, 3, 3)
            delta = value - baseline
            delta_text = _format_metric_value(abs(delta), label_metric)
            annotation = "Baseline" if index == 0 else f"{'+' if delta >= 0 else '-'}{delta_text}"
            text_y = max(plot.top(), top - 20)
            painter.setPen(QColor(tokens["text2"]))
            painter.drawText(
                QRectF(center - group_width / 2, text_y, group_width, 18),
                Qt.AlignHCenter | Qt.AlignVCenter,
                annotation,
            )
            model_label = painter.fontMetrics().elidedText(
                str(group.get("modelLabel") or "Model"),
                Qt.ElideRight,
                max(34, int(group_width - 8)),
            )
            painter.setPen(QColor(tokens["text3"]))
            painter.drawText(
                QRectF(center - group_width / 2, plot.bottom() + 5, group_width, 18),
                Qt.AlignHCenter | Qt.AlignTop,
                model_label,
            )
            payload = {
                "date": "Compared with baseline",
                "model": group.get("modelLabel"),
                "value": value,
                "metricLabel": label_metric,
                "comparisonRole": "baseline" if index == 0 else "comparison",
                "detailLines": [
                    "Role: Baseline" if index == 0
                    else f"Difference: {'+' if delta >= 0 else '-'}{delta_text}"
                ],
            }
            self._points.append((QPointF(center, bar_rect.top()), payload))
            self._hit_targets.append((bar_rect.adjusted(-5, -5, 5, 5), payload))

        baseline_y = max(
            plot.top(),
            plot.bottom() - min(plot.height() - 24, baseline / maximum * max(1.0, plot.height() - 24)),
        )
        baseline_pen = QPen(QColor(tokens["accent"]), 1.5, Qt.DashLine)
        painter.setPen(baseline_pen)
        painter.drawLine(QPointF(plot.left(), baseline_y), QPointF(plot.right(), baseline_y))
        baseline_value = _format_metric_value(baseline, label_metric)
        baseline_label = f"Baseline: {baseline_value}"
        label_width = painter.fontMetrics().horizontalAdvance(baseline_label) + 10
        painter.setPen(QColor(tokens["accent"]))
        painter.drawText(
            QRectF(plot.right() - label_width, max(plot.top(), baseline_y - 19), label_width, 17),
            Qt.AlignRight | Qt.AlignVCenter,
            baseline_label,
        )

    def _draw_stack(self, painter: QPainter, plot: QRectF) -> None:
        groups = self._active_groups()
        segments = self._segments or [key for key, _label_text, _color_text in TOKEN_SEGMENTS]
        totals = [sum(_metric_value(group, segment) for segment in segments) for group in groups]
        maximum = (
            1
            if self._kind == "stack100"
            else self._visible_maximum(max(totals or [1]) or 1)
        )
        self._draw_y_labels(painter, plot, maximum)
        slot = plot.width() / max(1, len(groups))
        width = min(54.0, slot * 0.62)
        segment_colors = {key: color for key, _label_text, color in TOKEN_SEGMENTS}
        for index, (group, total) in enumerate(zip(groups, totals)):
            x = plot.left() + slot * (index + 0.5) - width / 2
            bottom = plot.bottom()
            for segment_index, segment in enumerate(segments):
                value = _metric_value(group, segment)
                ratio = (value / total) if self._kind == "stack100" and total else (value / maximum)
                height = min(max(0.0, bottom - plot.top()), ratio * plot.height())
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(segment_colors.get(segment, "#84909a")))
                segment_rect = QRectF(x, bottom - height, width, height)
                painter.drawRect(segment_rect)
                if height >= 2:
                    self._hit_targets.append((segment_rect, {
                        "date": "Token category",
                        "model": group.get("modelLabel"),
                        "value": value,
                        "valueKind": "tokens",
                        "metricLabel": dict(
                            (key, label) for key, label, _color in TOKEN_SEGMENTS
                        ).get(segment, _metric_label(segment)),
                    }))
                bottom -= height
            label = painter.fontMetrics().elidedText(
                str(group.get("modelLabel") or "Model"),
                Qt.ElideRight,
                max(34, int(slot - 8)),
            )
            painter.setPen(QColor(self._theme.tokens["text3"]))
            painter.drawText(
                QRectF(plot.left() + slot * index, plot.bottom() + 5, slot, 18),
                Qt.AlignHCenter | Qt.AlignTop,
                label,
            )

    def _draw_box(self, painter: QPainter, plot: QRectF) -> None:
        groups = self._active_groups()
        stats_key = self._metric
        stats = [group.get(stats_key) for group in groups]
        maximum = self._visible_maximum(
            max([float(item.get("maximum") or 0) for item in stats if item] or [1]) or 1
        )
        self._draw_y_labels(painter, plot, maximum)
        slot = plot.width() / max(1, len(groups))
        for index, item in enumerate(stats):
            if not item:
                continue
            x = plot.left() + slot * (index + 0.5)
            to_y = lambda value: max(
                plot.top(),
                plot.bottom() - float(value or 0) / maximum * plot.height(),
            )
            color = self._group_color(groups[index])
            painter.setPen(QPen(color, 2))
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), 45))
            q1, q3 = to_y(item["q1"]), to_y(item["q3"])
            painter.drawRect(QRectF(x - 18, q3, 36, q1 - q3))
            painter.drawLine(QPointF(x - 18, to_y(item["median"])), QPointF(x + 18, to_y(item["median"])))
            painter.drawLine(QPointF(x, to_y(item["minimum"])), QPointF(x, to_y(item["maximum"])))
            label = painter.fontMetrics().elidedText(
                str(groups[index].get("modelLabel") or "Model"), Qt.ElideRight, max(34, int(slot - 8))
            )
            painter.setPen(QColor(self._theme.tokens["text3"]))
            painter.drawText(
                QRectF(plot.left() + slot * index, plot.bottom() + 5, slot, 18),
                Qt.AlignHCenter | Qt.AlignTop,
                label,
            )

    def _draw_scatter(self, painter: QPainter, plot: QRectF) -> None:
        if self._kind == "community_scatter":
            self._draw_community_scatter(painter, plot)
            return
        points = []
        for group in self._active_groups():
            x = _metric_value(group, "tokensPerTask")
            y = _metric_value(group, "tasksPerMillion")
            if x > 0 and y > 0:
                points.append((group, x, y))
        max_x = max([item[1] for item in points] or [1])
        max_y = self._visible_maximum(max([item[2] for item in points] or [1]))
        self._draw_y_labels(painter, plot, max_y)
        for index, (group, x_value, y_value) in enumerate(points):
            point = QPointF(
                plot.left() + x_value / max_x * plot.width(),
                max(plot.top(), plot.bottom() - y_value / max_y * plot.height()),
            )
            painter.setPen(QPen(self._group_color(group), 2))
            painter.setBrush(self._group_color(group))
            painter.drawEllipse(point, 6, 6)
            self._points.append((point, {
                "date": "Resource / work", "model": group.get("modelLabel"),
                "value": y_value, "metricLabel": "Tasks per 1M tokens",
            }))

    def _draw_community_scatter(self, painter: QPainter, plot: QRectF) -> None:
        """Ranked efficiency map: real metrics remain visible, never one score."""

        points = []
        for group in self._active_groups():
            x = _metric_value(group, "tokensPerTask")
            y = _metric_value(group, "tasksPerSession")
            if x > 0 and y > 0:
                points.append((group, x, y))
        max_x = max([item[1] for item in points] or [1]) * 1.08
        max_y = self._visible_maximum(max([item[2] for item in points] or [1]) * 1.12)
        self._draw_y_labels(painter, plot, max_y)
        tokens = self._theme.tokens
        for index, (group, x_value, y_value) in enumerate(points):
            point = QPointF(
                plot.left() + x_value / max_x * plot.width(),
                max(plot.top() + 16, plot.bottom() - y_value / max_y * plot.height()),
            )
            color = self._group_color(group)
            painter.setPen(QPen(color, 2))
            painter.setBrush(QColor(tokens["panel"]))
            painter.drawEllipse(point, 13, 13)
            rank = int(group.get("communityRank") or index + 1)
            painter.setPen(color)
            painter.drawText(QRectF(point.x() - 12, point.y() - 12, 24, 24), Qt.AlignCenter, str(rank))
            # At the minimum application width, the leaderboard beside the
            # chart already carries every name and value. Keeping only ranked
            # markers here prevents labels from colliding; hover remains full.
            if self.width() >= 700:
                label = painter.fontMetrics().elidedText(
                    str(group.get("modelLabel") or "Model"), Qt.ElideRight, 126
                )
                label_rect = QRectF(point.x() + 17, point.y() - 17, 128, 18)
                if label_rect.right() > plot.right():
                    label_rect.moveRight(point.x() - 17)
                painter.setPen(QColor(tokens["text2"]))
                painter.drawText(label_rect, Qt.AlignVCenter, label)
                metric_rect = QRectF(label_rect.left(), label_rect.top() + 16, 128, 18)
                painter.setPen(QColor(tokens["text3"]))
                painter.drawText(
                    metric_rect,
                    Qt.AlignVCenter,
                    f"{y_value:.1f} tasks / 5h",
                )
            self._points.append((point, {
                "date": "Community result",
                "model": group.get("modelLabel"),
                "value": y_value,
                "metricLabel": "Tasks per 5h capacity",
                "detailLines": [
                    f"Tokens per task: {_format_tokens(x_value)}",
                    f"Weekly burn per task: {_format_points(group.get('weeklyBurnPerTask'))}",
                    f"Observed tasks: {int(group.get('observations') or 0):,}",
                    f"Contributors: {int(group.get('contributors') or 0):,}",
                ],
            }))
        painter.setPen(QColor(tokens["text3"]))
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 5, plot.width(), 18),
            Qt.AlignLeft | Qt.AlignTop,
            "Lower tokens per task",
        )
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 5, plot.width(), 18),
            Qt.AlignRight | Qt.AlignTop,
            "Higher tokens per task",
        )

    def wheelEvent(self, event) -> None:
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        if event.modifiers() & Qt.ShiftModifier:
            old_zoom = self._x_zoom
            new_zoom = min(8.0, max(1.0, old_zoom * factor))
            plot = self._plot()
            cursor_ratio = (event.position().x() - plot.left()) / max(1.0, plot.width())
            logical = (cursor_ratio - self._x_pan) / old_zoom
            self._x_zoom = new_zoom
            self._x_pan = cursor_ratio - logical * new_zoom
        else:
            self._y_zoom = min(20.0, max(1.0, self._y_zoom * factor))
        self.update()
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            position = event.position()
            for rect, key, _label_text in self._legend_hits:
                if rect.contains(position):
                    if key in self._hidden_keys:
                        self._hidden_keys.remove(key)
                    else:
                        self._hidden_keys.add(key)
                    self.update()
                    event.accept()
                    return
            if self._x_zoom > 1.001:
                self._drag = event.position().toPoint()

    def mouseMoveEvent(self, event) -> None:
        position = event.position()
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            delta = position.x() - self._drag.x()
            self._x_pan += delta / max(1, self._plot().width())
            self._drag = position.toPoint()
            self.update()
            return
        for rect, key, label_text in self._legend_hits:
            if rect.contains(position):
                self.setCursor(Qt.PointingHandCursor)
                action = "Show" if key in self._hidden_keys else "Hide"
                self._hover_card.show_copy(
                    label_text,
                    f"Click to {action.lower()} this series.",
                    position,
                )
                return
        if self._legend_overflow is not None:
            overflow_rect, labels = self._legend_overflow
            if overflow_rect.contains(position):
                self.setCursor(Qt.ArrowCursor)
                self._hover_card.show_copy(
                    "Additional series",
                    "\n".join(labels),
                    position,
                )
                return
        self.unsetCursor()
        hovered_payload = next(
            (payload for rect, payload in reversed(self._hit_targets) if rect.contains(position)),
            None,
        )
        nearest = hovered_payload
        distance = 20.0
        for point, payload in self._points:
            current = (point - position).manhattanLength()
            if current < distance:
                nearest = payload
                distance = current
        if nearest:
            lines = self.point_text(nearest).splitlines()
            title = lines[1] if len(lines) > 1 else str(nearest.get("model") or "Model")
            body_lines = [line for index, line in enumerate(lines) if index != 1]
            self._hover_card.show_copy(title, "\n".join(body_lines), position)
            self.hovered.emit(nearest)
        else:
            self._hover_card.hide()

    def mouseReleaseEvent(self, _event) -> None:
        self._drag = None

    def leaveEvent(self, event) -> None:
        self.unsetCursor()
        self._hover_card.hide()
        super().leaveEvent(event)


class DensityPanel(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumHeight(124)
        self.setMaximumHeight(142)
        self._groups: list[dict] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(_label("Selected model", "sectionLabel", bold=True))
        self.model = QComboBox()
        self.model.setMinimumWidth(210)
        self.model.currentIndexChanged.connect(self._render)
        header.addWidget(self.model)
        self.scope = ElidedLabel("")
        self.scope.setObjectName("faint")
        header.addWidget(self.scope, 1)
        header.addSpacing(5)
        header.addWidget(_label("Normalize by", "sectionLabel", bold=True))
        self.basis = QComboBox()
        self.basis.setFixedWidth(170)
        self.basis.addItem("Raw totals", "raw")
        self.basis.addItem("Per 1M tokens", "perMillionTokens")
        self.basis.addItem("Per 10 weekly points", "perTenWeeklyPoints")
        self.basis.addItem("Per active hour", "perActiveHour")
        self.basis.currentIndexChanged.connect(self._render)
        header.addWidget(self.basis)
        layout.addLayout(header)
        divider = QFrame()
        divider.setObjectName("summaryDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        self.metrics_layout = QGridLayout()
        self.metrics_layout.setContentsMargins(0, 0, 0, 0)
        self.metrics_layout.setHorizontalSpacing(0)
        self.metrics_layout.setVerticalSpacing(5)
        self.metric_cells: list[QFrame] = []
        self._metric_columns = 0
        self.values: dict[str, QLabel] = {}
        metric_specs = (
            ("tokens", "Tokens"), ("tasks", "Tasks"), ("edits", "Edits"),
            ("files", "Files"), ("tests", "Tests"), ("commands", "Commands"),
            ("lines", "Lines changed"), ("active", "Active time"),
        )
        for index, (key, label) in enumerate(metric_specs):
            cell = QFrame()
            cell.setObjectName("densityMetric")
            cell.setProperty("last", index == len(metric_specs) - 1)
            cell.setMinimumWidth(0)
            cell.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            box = QVBoxLayout(cell)
            box.setContentsMargins(10, 3, 10, 4)
            box.setSpacing(2)
            metric_label = _label(label, "faint")
            metric_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            box.addWidget(metric_label)
            value = _label("-", bold=True, size=16)
            value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            box.addWidget(value)
            self.values[key] = value
            self.metric_cells.append(cell)
        self._arrange_metrics(4)
        layout.addLayout(self.metrics_layout)

    def _arrange_metrics(self, columns: int) -> None:
        if columns == self._metric_columns:
            return
        self._metric_columns = columns
        self.setMinimumHeight(124 if columns == 8 else 176)
        self.setMaximumHeight(142 if columns == 8 else 204)
        for index, cell in enumerate(self.metric_cells):
            row, column = divmod(index, columns)
            self.metrics_layout.addWidget(cell, row, column)
            cell.setProperty("last", column == columns - 1 or index == len(self.metric_cells) - 1)
            cell.setProperty("lowerRow", row > 0)
            cell.style().unpolish(cell)
            cell.style().polish(cell)
        for column in range(columns):
            self.metrics_layout.setColumnStretch(column, 1)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)

    def set_groups(self, groups: list[dict]) -> None:
        selected = self.model.currentData()
        self._groups = list(groups)
        desired_keys = [str(group.get("filterKey") or "") for group in groups]
        current_keys = [
            str(self.model.itemData(index) or "") for index in range(self.model.count())
        ]
        if desired_keys != current_keys:
            self.model.blockSignals(True)
            self.model.clear()
            for group in groups:
                icon_path = str(data.provider_icon_path({"provider": group.get("provider")}) or "")
                self.model.addItem(
                    QIcon(icon_path) if icon_path else QIcon(),
                    str(group.get("modelLabel") or "Model"),
                    group.get("filterKey"),
                )
            index = self.model.findData(selected)
            self.model.setCurrentIndex(index if index >= 0 else 0)
            self.model.blockSignals(False)
        self._render()

    def _render(self) -> None:
        index = self.model.currentIndex()
        if index < 0 or index >= len(self._groups):
            for label in self.values.values():
                label.setText("-")
            self.scope.setText("No model activity in this range")
            return
        group = self._groups[index]
        basis = str(self.basis.currentData() or "raw")
        if basis == "raw":
            values = {
                "tokens": _format_tokens(group.get("totalTokens", 0)),
                "tasks": str(group.get("completedTasks", 0)), "edits": str(group.get("edits", 0)),
                "files": str(group.get("filesChanged", 0)), "tests": str(group.get("tests", 0)),
                "commands": str(group.get("commands", 0)),
                "lines": str(int(group.get("linesAdded", 0)) + int(group.get("linesDeleted", 0))),
                "active": _format_duration(group.get("activeMs")),
            }
        else:
            normalized = (group.get("normalized") or {}).get(basis) or {}
            values = {
                "tokens": "Basis", "tasks": _format_number(normalized.get("tasks")),
                "edits": _format_number(normalized.get("edits")), "files": _format_number(normalized.get("files")),
                "tests": _format_number(normalized.get("tests")), "commands": _format_number(normalized.get("commands")),
                "lines": _format_number(normalized.get("lines")), "active": "Normalized",
            }
        for key, value in values.items():
            self.values[key].setText(value)
        self.scope.setText(
            f"{group.get('provider', '').title()} | {_friendly_work_scope(group.get('workScope'))} | "
            f"5h burn {_format_points(group.get('shortBurn', 0))} | "
            f"Weekly burn {_format_points(group.get('weeklyBurn', 0))}"
        )


class ChartFocusDialog(QDialog):
    def __init__(self, source: BenchmarkChart, title: str, theme, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1050, 670)
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        heading.addWidget(_label(title, bold=True, size=15))
        heading.addStretch(1)
        reset = make_button("Reset view", "ghost")
        heading.addWidget(reset)
        layout.addLayout(heading)
        chart = BenchmarkChart(theme)
        kind, groups, metric, segments = source.chart_state()
        chart.set_chart(kind, groups, metric, segments)
        chart.set_hidden_keys(source.hidden_keys())
        reset.clicked.connect(chart.reset_view)
        layout.addWidget(chart, 1)


class ResponsiveChartHost(QWidget):
    """Keep chart controls usable by stacking panels before width is cramped."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)
        self._panels: list[QWidget] = []
        self._side_by_side = False

    def add_panel(self, panel: QWidget) -> None:
        self._panels.append(panel)
        self._arrange(False)

    def _arrange(self, side_by_side: bool) -> None:
        if side_by_side == self._side_by_side and self.grid.count() == len(self._panels):
            return
        self._side_by_side = side_by_side
        for index, panel in enumerate(self._panels):
            row, column = (0, index) if side_by_side else (index, 0)
            self.grid.addWidget(panel, row, column)
        self.grid.setColumnStretch(0, 3 if side_by_side else 1)
        self.grid.setColumnStretch(1, 2 if side_by_side else 0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)

__all__ = [
    "AnalyticsWorker", "BenchmarkChart", "ChartFocusDialog", "DensityPanel",
    "ResponsiveChartHost", "StatTile",
    "COMPARE_BAR_VIEWS", "COMPARE_LINE_VIEWS", "MODEL_BAR_VIEWS",
    "MODEL_LINE_VIEWS", "OVERVIEW_BAR_VIEWS", "OVERVIEW_LINE_VIEWS",
    "PRODUCTIVITY_BAR_VIEWS", "PRODUCTIVITY_LINE_VIEWS",
    "_chart_rows", "_format_duration", "_format_number", "_format_points",
    "_format_tokens", "_friendly_work_scope", "_inline_copy", "_label",
]
