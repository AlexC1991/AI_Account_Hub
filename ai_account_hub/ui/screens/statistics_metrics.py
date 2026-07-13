"""Declarative metric labels, colors, and chart choices for Statistics."""

from __future__ import annotations

import math
import re


MODEL_COLORS = (
    "#45a3ff", "#55c47d", "#e2b93f", "#d47be8", "#ef8354", "#42b8c5",
    "#8b9bea", "#df6c88", "#82b05a", "#bd8b62", "#7ac6a6", "#b58ce2",
)
METRIC_COLORS = {
    "edits": "#45a3ff", "filesChanged": "#55c47d", "fileTouches": "#55c47d", "tests": "#e2b93f",
    "commands": "#d47be8", "taskTokenMinimum": "#45b8ce",
    "taskTokenQ1": "#55c47d", "taskTokenMedian": "#e2b93f",
    "taskTokenQ3": "#d47be8", "taskTokenMaximum": "#ef8354",
    "durationMinimum": "#45b8ce", "durationQ1": "#55c47d",
    "durationMedian": "#e2b93f", "durationQ3": "#d47be8",
    "durationMaximum": "#ef8354",
}
METRIC_LABELS = {
    "edits": "Edits", "filesChanged": "Unique files", "fileTouches": "File touches", "tests": "Tests",
    "commands": "Commands", "taskTokenMinimum": "Minimum",
    "taskTokenQ1": "Q1", "taskTokenMedian": "Median",
    "taskTokenQ3": "Q3", "taskTokenMaximum": "Maximum",
    "durationMinimum": "Minimum", "durationQ1": "Q1",
    "durationMedian": "Median", "durationQ3": "Q3",
    "durationMaximum": "Maximum",
}
DISPLAY_METRIC_LABELS = {
    "totalTokens": "Attributed provider tokens", "workTokens": "Work tokens",
    "completedTasks": "Completed tasks", "shortBurn": "5h limit burn",
    "weeklyBurn": "Weekly limit burn", "edits": "Edits",
    "filesChanged": "Unique files", "fileTouches": "File touches",
    "tests": "Tests", "commands": "Commands", "activeMs": "Active time",
    "tokensPerTask": "Tokens per completed task",
    "tasksPerMillion": "Tasks per 1M work tokens",
    "tasksPerSession": "Tasks per 5h capacity",
    "weeklyBurnPerTask": "Weekly burn per task", "observations": "Observed tasks",
    "inputTokens": "Input tokens", "cachedInputTokens": "Cached input tokens",
    "cacheCreationTokens": "Cache write tokens", "reasoningTokens": "Reasoning tokens",
    "outputTokens": "Output tokens", "unclassifiedTokens": "Unclassified tokens",
    "taskTokenMinimum": "Minimum task work tokens",
    "taskTokenQ1": "Task-work-token first quartile",
    "taskTokenMedian": "Median task work tokens",
    "taskTokenQ3": "Task-work-token third quartile",
    "taskTokenMaximum": "Maximum task work tokens",
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
    ("outputTokens", "Output", "#e77f52"),
    ("unclassifiedTokens", "Unclassified", "#84909a"),
)
COMMUNITY_SCATTER_AXES = {
    "tasksPerSession": ("tokensPerTask", "tasksPerSession"),
    "tokensPerTask": ("tasksPerSession", "tokensPerTask"),
    "weeklyBurnPerTask": ("tasksPerSession", "weeklyBurnPerTask"),
    "observations": ("tokensPerTask", "observations"),
}
LINE_CHART_VIEWS = (
    ("Work tokens", "Work tokens over time", "Non-cache tokens (excludes cache re-reads)", "line", "workTokens", ()),
    ("Completed tasks", "Completed tasks over time", "Completed work recorded for each model", "line", "completedTasks", ()),
    ("5h limit burn", "5h limit burn over time", "Measured movement from trustworthy intervals", "line", "shortBurn", ()),
    ("Weekly limit burn", "Weekly limit burn over time", "Positive movement with reset decreases excluded", "line", "weeklyBurn", ()),
    ("Edits", "Edit activity over time", "Observed edit operations by model", "line", "edits", ()),
    ("File touches", "File touches over time", "Changed-file operations; repeat touches remain visible", "line", "fileTouches", ()),
    ("Tests", "Test activity over time", "Observed test commands by model", "line", "tests", ()),
    ("Commands", "Command activity over time", "Observed command activity by model", "line", "commands", ()),
    ("Active time", "Active task time over time", "Time recorded in model sessions", "line", "activeMs", ()),
)
BAR_CHART_VIEWS = (
    ("Completed work", "Completed work", "Task completions by used model", "bar", "completedTasks", ()),
    ("Work tokens by model", "Work tokens by model", "Non-cache tokens; cache re-reads excluded", "bar", "workTokens", ()),
    ("5h limit burn", "5h limit burn by model", "Measured percentage-point movement", "bar", "shortBurn", ()),
    ("Weekly limit burn", "Weekly limit burn by model", "Reset decreases and long gaps excluded", "bar", "weeklyBurn", ()),
    ("Token category mix", "Token category mix", "Output includes provider-reported reasoning where separately exposed", "stack", "totalTokens", ()),
    ("Engineering activity", "Engineering activity bundle", "Edits, file touches, tests and commands remain separate", "multi_bar", "", ("edits", "fileTouches", "tests", "commands")),
    ("Tokens per task", "Work tokens per completed task", "Task-attributed non-cache resources per completion", "bar", "tokensPerTask", ()),
    ("Tasks per 1M tokens", "Tasks per 1M work tokens", "Observed completions normalized by task-attributed non-cache tokens", "bar", "tasksPerMillion", ()),
    ("Task token quartiles", "Task work-token distribution", "Minimum, quartiles, median and maximum", "multi_bar", "", ("taskTokenMinimum", "taskTokenQ1", "taskTokenMedian", "taskTokenQ3", "taskTokenMaximum")),
    ("Task duration quartiles", "Task duration distribution", "Observed task-span quartiles", "multi_bar", "", ("durationMinimum", "durationQ1", "durationMedian", "durationQ3", "durationMaximum")),
)

OVERVIEW_LINE_VIEWS = tuple(
    item for item in LINE_CHART_VIEWS
    if item[0] in {"Work tokens", "Completed tasks", "5h limit burn", "Weekly limit burn"}
)
OVERVIEW_BAR_VIEWS = tuple(
    item for item in BAR_CHART_VIEWS
    if item[0] in {"Completed work", "Work tokens by model", "5h limit burn", "Weekly limit burn"}
)
MODEL_LINE_VIEWS = tuple(
    item for item in LINE_CHART_VIEWS
    if item[0] in {"Work tokens", "Completed tasks", "Active time"}
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
    if item[0] in {"Edits", "File touches", "Tests", "Commands", "Active time", "5h limit burn", "Weekly limit burn"}
)
PRODUCTIVITY_BAR_VIEWS = tuple(
    item for item in BAR_CHART_VIEWS
    if item[0] in {"Engineering activity", "5h limit burn", "Weekly limit burn", "Tokens per task", "Tasks per 1M tokens"}
)
COMPARE_LINE_VIEWS = tuple(
    item for item in LINE_CHART_VIEWS
    if item[0] in {
        "Work tokens", "Completed tasks", "Edits", "File touches", "Tests",
        "Commands", "Active time", "5h limit burn", "Weekly limit burn",
    }
)
COMPARE_BAR_VIEWS = (
    ("Work tokens", "Work tokens by model", "Non-cache totals from zero; differences use the baseline", "comparison_bar", "workTokens", ()),
    ("Completed tasks", "Completed tasks by model", "Completed work totals from zero", "comparison_bar", "completedTasks", ()),
    ("Edits", "Edits by model", "Observed edit totals from zero", "comparison_bar", "edits", ()),
    ("File touches", "File touches by model", "Changed-file operations from zero", "comparison_bar", "fileTouches", ()),
    ("Tests", "Tests by model", "Observed test totals from zero", "comparison_bar", "tests", ()),
    ("Commands", "Commands by model", "Observed command totals from zero", "comparison_bar", "commands", ()),
    ("Active time", "Active time by model", "Recorded session time from zero", "comparison_bar", "activeMs", ()),
    ("5h limit burn", "5h limit burn by model", "Measured percentage-point movement from zero", "comparison_bar", "shortBurn", ()),
    ("Weekly limit burn", "Weekly limit burn by model", "Measured percentage-point movement from zero", "comparison_bar", "weeklyBurn", ()),
    ("Tokens per task", "Work tokens per completed task", "Task-attributed non-cache resources per completion", "comparison_bar", "tokensPerTask", ()),
    ("Tasks per 1M", "Tasks per 1M work tokens", "Completion density from task-attributed non-cache tokens", "comparison_bar", "tasksPerMillion", ()),
)


def metric_label(metric: object) -> str:
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


def community_scatter_axes(metric: object) -> tuple[str, str]:
    """Return an independent metric pair for the selected Community ranking."""
    return COMMUNITY_SCATTER_AXES.get(
        str(metric or "tasksPerSession"), COMMUNITY_SCATTER_AXES["tasksPerSession"]
    )


def scatter_domain(values: list[float], padding: float = 0.1) -> tuple[float, float]:
    """Scale scatter data to its observed range instead of fixed model slots."""
    clean = [float(value) for value in values if math.isfinite(float(value))]
    if not clean:
        return 0.0, 1.0
    lower, upper = min(clean), max(clean)
    margin = max(1.0, abs(upper) * padding) if lower == upper else (upper - lower) * padding
    return max(0.0, lower - margin), upper + margin


__all__ = [
    "BAR_CHART_VIEWS", "COMPARE_BAR_VIEWS", "COMPARE_LINE_VIEWS",
    "COMMUNITY_SCATTER_AXES", "community_scatter_axes",
    "DISPLAY_METRIC_LABELS", "LINE_CHART_VIEWS", "METRIC_COLORS", "METRIC_LABELS",
    "MODEL_BAR_VIEWS", "MODEL_COLORS", "MODEL_LINE_VIEWS", "OVERVIEW_BAR_VIEWS",
    "OVERVIEW_LINE_VIEWS", "PRODUCTIVITY_BAR_VIEWS", "PRODUCTIVITY_LINE_VIEWS",
    "TOKEN_SEGMENTS", "metric_label", "scatter_domain",
]
