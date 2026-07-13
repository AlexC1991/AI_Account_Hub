"""Community section of the Statistics screen (mixed into StatisticsScreen)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QScrollArea, QSizePolicy, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QStackedWidget,
)

from ai_account_hub import data
from ai_account_hub.core import hub_core
from ai_account_hub.core.community_api import (
    CommunityApiError,
    TestCommunityApi,
    build_submission_payload,
    configured_community_api,
)
from ai_account_hub.core.benchmark_analytics import (
    build_benchmark_analytics,  # compatibility hook for demo/privacy probes
)
from ai_account_hub.core.benchmark_view import (
    aggregate_base_model_groups,
    base_model_key,
    build_benchmark_view,
    build_head_to_head,
    productivity_density_csv,
)
from ai_account_hub.ui.widgets import ElidedLabel, SegmentedSlider, Spinner, make_button
from ai_account_hub.ui.screens.statistics_charts import (
    AnalyticsWorker,
    BenchmarkChart,
    ChartFocusDialog,
    COMPARE_BAR_VIEWS,
    COMPARE_LINE_VIEWS,
    DensityPanel,
    MODEL_BAR_VIEWS,
    MODEL_LINE_VIEWS,
    OVERVIEW_BAR_VIEWS,
    OVERVIEW_LINE_VIEWS,
    PRODUCTIVITY_BAR_VIEWS,
    PRODUCTIVITY_LINE_VIEWS,
    ResponsiveChartHost,
    StatTile,
    _chart_rows,
    _format_duration,
    _format_number,
    _format_points,
    _format_tokens,
    _friendly_work_scope,
    _inline_copy,
    _label,
)


class CommunityResultsWorker(QThread):
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, api, days: int, provider: str, parent=None) -> None:
        super().__init__(parent)
        self._api = api
        self._days = days
        self._provider = provider

    def run(self) -> None:
        try:
            self.completed.emit(
                self._api.fetch_results(days=self._days, provider=self._provider)
            )
        except CommunityApiError as exc:
            self.failed.emit(str(exc))




class StatisticsCommunityMixin:
    """Extracted from StatisticsScreen; mixed back in (1400-line rule)."""
    def _build_community_page(self) -> QScrollArea:
        scroll, self.community_content, layout = self._new_statistics_page()
        self._add_page_heading(
            layout,
            "Community",
            "Privacy-thresholded model comparisons from shared real-world usage",
        )

        status = QFrame()
        status.setObjectName("communityStatusCard")
        status_layout = QHBoxLayout(status)
        status_layout.setContentsMargins(12, 9, 12, 9)
        status_layout.setSpacing(12)
        test_api = self.community_api.mode == "test"
        status_copy, self.community_status_title, self.community_status_caption = _inline_copy(
            "Configuration error" if self._community_config_error
            else "Local test API" if test_api else "Cloudflare staging API",
            self._community_config_error
            or "Offline sample results; no network request is made"
            if test_api else "Signed collection and automatic privacy-thresholded publication",
        )
        status_layout.addWidget(status_copy, 1)
        self.community_contributors = _label("0 contributors", "muted", bold=True)
        self.community_observations = _label("0 observed tasks", "muted", bold=True)
        status_layout.addWidget(self.community_contributors)
        status_layout.addWidget(self.community_observations)
        self.community_api_pill = _label("TEST" if test_api else "STAGING")
        self.community_api_pill.setProperty("pill", "inuse")
        self.community_api_pill.setAlignment(Qt.AlignCenter)
        self.community_api_pill.setFixedWidth(74)
        self.community_api_pill.setToolTip(
            "Offline deterministic adapter; Cloudflare transport is not connected"
            if test_api else "Signed staging collection; direct R2 access is never used"
        )
        status_layout.addWidget(self.community_api_pill)
        layout.addWidget(status)

        controls = QFrame()
        controls.setObjectName("card")
        control_layout = QHBoxLayout(controls)
        control_layout.setContentsMargins(11, 8, 11, 8)
        control_layout.setSpacing(8)
        control_layout.addWidget(_label("Provider", "sectionLabel"))
        self.community_provider = QComboBox()
        self.community_provider.addItem("All providers", "all")
        self.community_provider.addItem("Codex", "codex")
        self.community_provider.addItem("Claude Code", "claude")
        self.community_provider.setFixedWidth(140)
        control_layout.addWidget(self.community_provider)
        control_layout.addWidget(_label("Ranking", "sectionLabel"))
        self.community_ranking = QComboBox()
        self.community_ranking.addItem("Tasks per 5h", "tasksPerSession")
        self.community_ranking.addItem("Lowest tokens per task", "tokensPerTask")
        self.community_ranking.addItem("Lowest weekly burn per task", "weeklyBurnPerTask")
        self.community_ranking.addItem("Observation volume", "observations")
        self.community_ranking.setFixedWidth(210)
        control_layout.addWidget(self.community_ranking)
        control_layout.addStretch(1)
        control_layout.addWidget(_label("View", "sectionLabel"))
        self.community_chart_mode = SegmentedSlider(
            [("Lines", "lines"), ("Dots", "dots"), ("Bars", "bars")],
            self._tm.tokens,
            height=32,
        )
        self.community_chart_mode.setFixedWidth(218)
        control_layout.addWidget(self.community_chart_mode)
        layout.addWidget(controls)

        visual_host = QWidget()
        visual_layout = QGridLayout(visual_host)
        visual_layout.setContentsMargins(0, 0, 0, 0)
        visual_layout.setHorizontalSpacing(10)
        visual_layout.setVerticalSpacing(10)

        chart_panel = QFrame()
        chart_panel.setObjectName("card")
        chart_layout = QVBoxLayout(chart_panel)
        chart_layout.setContentsMargins(11, 9, 11, 9)
        chart_layout.setSpacing(5)
        chart_header = QHBoxLayout()
        chart_copy, self.community_chart_title, self.community_chart_caption = _inline_copy(
            "Community efficiency map",
            "Separate observed metrics; no universal quality score",
        )
        chart_header.addWidget(chart_copy, 1)
        reset = make_button("Reset", "ghost")
        focus = make_button("Focus", "ghost")
        chart_header.addWidget(reset)
        chart_header.addWidget(focus)
        chart_layout.addLayout(chart_header)
        self.community_chart = BenchmarkChart(self._tm)
        self.community_chart.setMinimumHeight(430)
        reset.clicked.connect(self.community_chart.reset_view)
        focus.clicked.connect(
            lambda _checked=False: self._focus_chart(
                self.community_chart, self.community_chart_title.text()
            )
        )
        chart_layout.addWidget(self.community_chart, 1)
        visual_layout.addWidget(chart_panel, 0, 0)

        leaderboard_panel = QFrame()
        leaderboard_panel.setObjectName("card")
        leaderboard_layout = QVBoxLayout(leaderboard_panel)
        leaderboard_layout.setContentsMargins(0, 0, 0, 0)
        leaderboard_header = QHBoxLayout()
        leaderboard_header.setContentsMargins(11, 9, 11, 7)
        leaderboard_copy, self.community_rank_title, self.community_rank_caption = _inline_copy(
            "Ranked results",
            "One selected metric at a time",
        )
        leaderboard_header.addWidget(leaderboard_copy, 1)
        leaderboard_layout.addLayout(leaderboard_header)
        self.community_table = QTableWidget(0, 5)
        self.community_table.setHorizontalHeaderLabels(
            ("Rank", "Model / setting", "Result", "Supporting", "Sample")
        )
        self.community_table.verticalHeader().setVisible(False)
        self.community_table.setSelectionMode(QTableWidget.NoSelection)
        self.community_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.community_table.setShowGrid(False)
        self.community_table.setMinimumHeight(430)
        self.community_table.setMinimumWidth(430)
        table_header = self.community_table.horizontalHeader()
        table_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table_header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column in (2, 3, 4):
            table_header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        leaderboard_layout.addWidget(self.community_table, 1)
        visual_layout.addWidget(leaderboard_panel, 0, 1)
        visual_layout.setColumnStretch(0, 3)
        visual_layout.setColumnStretch(1, 2)
        layout.addWidget(visual_host)

        self.community_note = _label(
            "Community results are anonymous aggregates. They describe resource use and observed work, not model quality.",
            "faint",
        )
        self.community_note.setWordWrap(True)
        layout.addWidget(self.community_note)
        scroll.setWidget(self.community_content)

        self.community_provider.currentIndexChanged.connect(
            lambda _index: self._refresh_community_results()
        )
        self.community_ranking.currentIndexChanged.connect(
            lambda _index: self._render_community()
        )
        self.community_chart_mode.changed.connect(
            lambda _key: self._render_community()
        )
        self._tm.changed.connect(
            lambda _name: self.community_chart_mode.set_theme(self._tm.tokens)
        )
        self._refresh_community_results()
        return scroll

    def _refresh_community_results(self) -> None:
        days = int(self.range_filter.currentData() or 30)
        provider = str(self.community_provider.currentData() or "all")
        if self.community_api.mode == "test":
            try:
                self._community_results = self.community_api.fetch_results(
                    days=days, provider=provider
                )
            except CommunityApiError as exc:
                self._community_failed(str(exc))
                return
            self._render_community()
            return
        if self._community_worker is not None and self._community_worker.isRunning():
            self._community_refresh_pending = True
            return
        self._community_refresh_pending = False
        self.community_contributors.setText("Loading community results...")
        self.community_observations.setText("")
        self._community_worker = CommunityResultsWorker(
            self.community_api, days, provider, self
        )
        self._community_worker.completed.connect(self._community_ready)
        self._community_worker.failed.connect(self._community_failed)
        self._community_worker.finished.connect(self._community_worker_finished)
        self._community_worker.start(QThread.LowPriority)

    def _community_ready(self, results: dict) -> None:
        self._community_error = ""
        self._community_results = results
        self._render_community()

    def _community_failed(self, message: str) -> None:
        self._community_error = message
        self._community_results = {"groups": [], "contributors": 0, "observedTasks": 0}
        self._render_community()
        self.activity.emit(f"Community API unavailable: {message}")

    def _community_worker_finished(self) -> None:
        worker = self._community_worker
        self._community_worker = None
        if worker is not None:
            worker.deleteLater()
        if self._community_refresh_pending:
            self._refresh_community_results()

    def _community_ranked_groups(self) -> list[dict]:
        groups = [dict(group) for group in self._community_results.get("groups", [])]
        metric = str(self.community_ranking.currentData() or "tasksPerSession")
        lower_is_better = metric in {"tokensPerTask", "weeklyBurnPerTask"}
        groups.sort(
            key=lambda group: float(group.get(metric) or 0),
            reverse=not lower_is_better,
        )
        for rank, group in enumerate(groups, 1):
            group["communityRank"] = rank
        return groups

    @staticmethod
    def _community_result_text(metric: str, value: float) -> str:
        if metric == "tasksPerSession":
            return f"{value:.1f} tasks / 5h"
        if metric == "tokensPerTask":
            return f"{_format_tokens(value)} / task"
        if metric == "weeklyBurnPerTask":
            return f"{_format_points(value)} / task"
        return f"{int(value):,} tasks"

    def _render_community(self) -> None:
        if not hasattr(self, "community_chart"):
            return
        groups = self._community_ranked_groups()
        metric = str(self.community_ranking.currentData() or "tasksPerSession")
        mode = self.community_chart_mode._active
        titles = {
            "tasksPerSession": "Tasks per 5h capacity",
            "tokensPerTask": "Tokens per completed task",
            "weeklyBurnPerTask": "Weekly limit movement per task",
            "observations": "Observation volume",
        }
        captions = {
            "tasksPerSession": "Higher observed completion volume per 5-hour capacity",
            "tokensPerTask": "Lower observed token cost per completion ranks first",
            "weeklyBurnPerTask": "Lower weekly percentage-point movement per completion ranks first",
            "observations": "Larger samples rank first; volume is not a quality score",
        }
        if mode == "dots":
            scatter_copy = {
                "tasksPerSession": ("Throughput and token cost", "Token cost vs 5-hour task capacity"),
                "tokensPerTask": ("Token cost and throughput", "5-hour task capacity vs token cost"),
                "weeklyBurnPerTask": ("Weekly limit economics", "5-hour task capacity vs weekly burn per task"),
                "observations": ("Evidence coverage", "Token cost vs observed task volume"),
            }
            title, axis_copy = scatter_copy.get(metric, scatter_copy["tasksPerSession"])
            caption = f"{axis_copy}; position uses live values and marker size reflects observations"
            kind, chart_metric = "community_scatter", metric
        elif mode == "lines":
            title = f"{titles[metric]} over time"
            caption = f"{captions[metric]} across the selected date range"
            kind, chart_metric = "line", metric
        else:
            title = f"{titles[metric]} by model"
            caption = captions[metric]
            kind, chart_metric = "bar", metric
        self.community_chart_title.setText(title)
        self.community_chart_caption.setText(caption)
        self.community_rank_title.setText(titles[metric])
        self.community_rank_caption.setText(captions[metric])
        self.community_chart.set_chart(kind, groups, chart_metric)

        self.community_table.setRowCount(len(groups))
        for row, group in enumerate(groups):
            value = float(group.get(metric) or 0)
            supporting = (
                f"{float(group.get('tasksPerSession') or 0):.1f} / 5h"
                if metric != "tasksPerSession"
                else f"{_format_tokens(group.get('tokensPerTask'))} / task"
            )
            values = (
                f"#{row + 1}",
                str(group.get("modelLabel") or "Model"),
                self._community_result_text(metric, value),
                supporting,
                f"{int(group.get('observations') or 0):,}",
            )
            tooltip = (
                f"{group.get('modelLabel') or 'Model'}\n"
                f"Tasks per 5h: {float(group.get('tasksPerSession') or 0):.1f}\n"
                f"Tokens per task: {_format_tokens(group.get('tokensPerTask'))}\n"
                f"Weekly burn per task: {_format_points(group.get('weeklyBurnPerTask'))}\n"
                f"Observed tasks: {int(group.get('observations') or 0):,}\n"
                f"Contributors: {int(group.get('contributors') or 0):,}"
            )
            for column, value_text in enumerate(values):
                item = QTableWidgetItem(value_text)
                item.setToolTip(tooltip)
                if column != 1:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.community_table.setItem(row, column, item)
            self.community_table.setRowHeight(row, 42)
        if self._community_error:
            self.community_status_title.setText("Community unavailable")
            self.community_status_caption.setText("The staging Worker could not be reached")
            self.community_api_pill.setText("ERROR")
            self.community_contributors.setText("Community unavailable")
            self.community_observations.setText(self._community_error)
            self.community_observations.setToolTip(self._community_error)
        else:
            source = str(self._community_results.get("dataSource") or "")
            contributors = int(self._community_results.get("contributors") or 0)
            observed = int(self._community_results.get("observedTasks") or 0)
            collecting = int(self._community_results.get("collectionContributors") or 0)
            submissions = int(self._community_results.get("collectionSubmissions") or 0)
            minimum = int(self._community_results.get("minimumContributors") or 10)
            if self.community_api.mode == "test":
                self.community_status_title.setText("Offline sample results")
                self.community_status_caption.setText(
                    "Deterministic demonstration data; nothing is uploaded"
                )
                self.community_api_pill.setText("TEST")
                self.community_contributors.setText(f"{contributors:,} sample contributors")
                self.community_observations.setText(f"{observed:,} sample tasks")
            elif source == "real-community":
                self.community_status_title.setText("Real community aggregates")
                self.community_status_caption.setText(
                    f"Published cohorts meet the {minimum}-contributor privacy threshold"
                )
                self.community_api_pill.setText("LIVE")
                self.community_contributors.setText(f"{contributors:,} contributors")
                self.community_observations.setText(f"{observed:,} observed tasks")
            else:
                self.community_status_title.setText("Synthetic staging preview")
                self.community_status_caption.setText(
                    f"Real: {collecting:,} contributor(s), {submissions:,} day(s) | "
                    f"Publishes at {minimum}"
                )
                self.community_api_pill.setText("SAMPLE")
                self.community_contributors.setText(f"{contributors:,} sample contributors")
                self.community_observations.setText(f"{observed:,} sample tasks")
            self.community_note.setText(
                "Real Community cohorts suppress days below the contributor threshold. "
                "Results describe resource use and observed work, not model quality."
            )
            self.community_observations.setToolTip("")

    def community_payload(self) -> dict:
        """Return exactly what the sharing consent preview and test API receive."""
        # Community submissions are always a one-day per-provider-account mean.
        # They must not depend on whichever local UI scope/aggregation happens to
        # be selected, and owning three provider accounts must not contribute
        # three times the weight of owning one.
        view = build_benchmark_view(
            self._snapshot,
            account_id="all",
            days=1,
            aggregation_mode="per_provider_account",
        )
        return build_submission_payload(list(view.get("groups") or []), days=1)
