"""Statistics workspace controller and five-section navigation."""

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
    aggregate_base_model_groups,
    base_model_key,
    build_benchmark_analytics,  # compatibility hook for demo/privacy probes
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


class StatisticsScreen(QWidget):
    activity = Signal(str)
    history_updated = Signal()

    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self._tm = theme
        self._profiles: list[dict] = []
        self._snapshot: dict = {"tasks": [], "modelUsageRows": [], "modelCatalog": []}
        self._view: dict = {"groups": [], "journal": [], "summary": {}}
        self._worker: AnalyticsWorker | None = None
        self._pending_refresh = False
        self._scan_generation = 0
        self._community_config_error = ""
        try:
            self.community_api = configured_community_api()
        except CommunityApiError as exc:
            self.community_api = TestCommunityApi()
            self._community_config_error = str(exc)
        self._community_results: dict = {"groups": []}
        self._community_error = ""
        self._community_worker: CommunityResultsWorker | None = None
        self._community_refresh_pending = False
        self._build()
        # Account refresh-all updates profiles one at a time. Coalesce that
        # burst into one analytics scan instead of queueing a full history pass
        # for every account card that finishes.
        self._refresh_debounce = QTimer(self)
        self._refresh_debounce.setSingleShot(True)
        self._refresh_debounce.setInterval(900)
        self._refresh_debounce.timeout.connect(self.refresh_analytics)
        self._tm.changed.connect(lambda _name: self.apply_theme())

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        toolbar = QFrame()
        toolbar.setObjectName("panel")
        filters = QHBoxLayout(toolbar)
        filters.setContentsMargins(16, 8, 16, 8)
        filters.setSpacing(7)
        filters.addWidget(_label("Account scope", "sectionLabel"))
        self.account_filter = QComboBox()
        self.account_filter.setFixedWidth(190)
        self.account_filter.currentIndexChanged.connect(self._filter_changed)
        filters.addWidget(self.account_filter)
        filters.addWidget(_label("Range", "sectionLabel"))
        self.range_filter = QComboBox()
        for label, days in (
            ("7 days", 7), ("30 days", 30), ("90 days", 90),
            ("180 days", 180), ("365 days", 365),
        ):
            self.range_filter.addItem(label, days)
        self.range_filter.setCurrentIndex(1)
        self.range_filter.setFixedWidth(130)
        self.range_filter.currentIndexChanged.connect(self._filter_changed)
        filters.addWidget(self.range_filter)
        filters.addStretch(1)
        self.scan_spinner = Spinner(self._tm.tokens["accent"], size=14)
        self.scan_spinner.setVisible(False)
        filters.addWidget(self.scan_spinner)
        self.scan_status = _label("Waiting for account data", "faint")
        self.scan_status.setFixedWidth(112)
        self.scan_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        filters.addWidget(self.scan_status)
        self.refresh_button = make_button("Refresh analytics", "ghost")
        self.refresh_button.clicked.connect(self._manual_refresh)
        filters.addWidget(self.refresh_button)
        outer.addWidget(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._content = QWidget()
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(16, 13, 16, 24)
        layout.setSpacing(10)

        heading_host = QWidget()
        heading_host.setFixedHeight(32)
        heading = QHBoxLayout(heading_host)
        heading.setContentsMargins(0, 0, 0, 0)
        overview_copy, _page_title, _page_caption = _inline_copy(
            "Overview",
            "Usage, coding activity, and limits at a glance",
            title_size=18,
        )
        heading.addWidget(overview_copy, 1)
        layout.addWidget(heading_host)

        self.summary_panel = QFrame()
        self.summary_panel.setObjectName("card")
        self.summary_panel.setMinimumWidth(0)
        summary_layout = QVBoxLayout(self.summary_panel)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(0)
        summary_header_host = QWidget()
        summary_header = QHBoxLayout(summary_header_host)
        summary_header.setContentsMargins(12, 8, 12, 5)
        summary_header.addWidget(_label("Usage summary", bold=True, size=12))
        summary_header.addStretch(1)
        summary_layout.addWidget(summary_header_host)
        kpi_host = QWidget()
        kpis = QHBoxLayout(kpi_host)
        kpis.setContentsMargins(10, 0, 10, 7)
        kpis.setSpacing(0)
        self.tiles: dict[str, StatTile] = {}
        tile_specs = (
            ("tokens", "Model tokens"), ("models", "Models used"),
            ("cache", "Context reuse"), ("tasks", "Completed tasks"),
            ("short", "5h usage movement"), ("weekly", "Weekly usage movement"),
        )
        for index, (key, title_text) in enumerate(tile_specs):
            tile = StatTile(title_text)
            tile.setObjectName("summaryStat")
            tile.setProperty("last", index == len(tile_specs) - 1)
            tile.setMinimumHeight(64)
            self.tiles[key] = tile
            kpis.addWidget(tile, 1)
        summary_layout.addWidget(kpi_host)

        summary_divider = QFrame()
        summary_divider.setObjectName("summaryDivider")
        summary_divider.setFixedHeight(1)
        summary_layout.addWidget(summary_divider)
        self.density = DensityPanel()
        self.density.setObjectName("summaryDensity")
        summary_layout.addWidget(self.density)

        self.charts_host = QWidget()
        charts = QGridLayout(self.charts_host)
        charts.setContentsMargins(0, 0, 0, 0)
        charts.setSpacing(10)
        self.chart_panels = []
        self.charts = []
        self.chart_selectors = []
        for column in range(2):
            panel = QFrame()
            panel.setObjectName("card")
            panel.setMinimumWidth(0)
            panel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
            box = QVBoxLayout(panel)
            box.setContentsMargins(11, 9, 11, 9)
            box.setSpacing(4)
            top = QHBoxLayout()
            chart_copy, title_label, caption = _inline_copy("Chart", "")
            top.addWidget(chart_copy, 1)
            selector = QComboBox()
            selector.setFixedWidth(174)
            view_options = OVERVIEW_LINE_VIEWS if column == 0 else OVERVIEW_BAR_VIEWS
            for option_label, *_spec in view_options:
                selector.addItem(option_label)
            selector.setCurrentIndex(0)
            selector.currentIndexChanged.connect(
                lambda _index, pane=column: self._render_chart(pane)
            )
            reset = make_button("Reset", "ghost")
            focus = make_button("Focus", "ghost")
            top.addWidget(selector)
            top.addWidget(reset)
            top.addWidget(focus)
            box.addLayout(top)
            chart = BenchmarkChart(self._tm)
            reset.clicked.connect(chart.reset_view)
            focus.clicked.connect(lambda _checked=False, c=chart, t=title_label: self._focus_chart(c, t.text()))
            box.addWidget(chart, 1)
            charts.addWidget(panel, 0, column)
            self.chart_panels.append((panel, title_label, caption))
            self.charts.append(chart)
            self.chart_selectors.append(selector)
        charts.setColumnStretch(0, 3)
        charts.setColumnStretch(1, 2)
        layout.addWidget(self.charts_host)

        export_row = QHBoxLayout()
        export_row.addStretch(1)
        csv_button = make_button("Export CSV", "ghost")
        png_button = make_button("Export PNG", "ghost")
        csv_button.clicked.connect(self._choose_csv)
        png_button.clicked.connect(self._choose_png)
        export_row.addWidget(csv_button)
        export_row.addWidget(png_button)
        layout.addLayout(export_row)

        layout.addWidget(self.summary_panel)

        self.comparison_panel = QFrame()
        self.comparison_panel.setObjectName("card")
        self.comparison_panel.setMinimumWidth(0)
        self.comparison_panel.setMinimumHeight(350)
        self.comparison_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        comparison_layout = QVBoxLayout(self.comparison_panel)
        comparison_layout.setContentsMargins(0, 0, 0, 0)
        comparison_header = QHBoxLayout()
        comparison_header.setContentsMargins(12, 9, 12, 7)
        comparison_copy, self.bottom_title, self.bottom_caption = _inline_copy(
            "Model summary",
            "Totals by base model; reasoning settings stay visible",
        )
        comparison_header.addWidget(comparison_copy, 1)
        self.bottom_view = QComboBox()
        self.bottom_view.setFixedWidth(170)
        self.bottom_view.addItem("Model summary", "comparison")
        self.bottom_view.addItem("Recent work", "journal")
        comparison_header.addWidget(self.bottom_view)
        comparison_layout.addLayout(comparison_header)
        self.comparison = QTableWidget(0, 12)
        self.comparison.setMinimumWidth(0)
        self.comparison.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.comparison.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.comparison.setHorizontalHeaderLabels((
            "Model", "Reasoning", "Tokens", "Cache reuse", "Tasks", "Edits", "Files",
            "Tests", "Commands", "Active", "5h burn", "Weekly burn",
        ))
        self.comparison.verticalHeader().setVisible(False)
        self.comparison.setAlternatingRowColors(False)
        self.comparison.setSelectionMode(QTableWidget.NoSelection)
        self.comparison.setEditTriggers(QTableWidget.NoEditTriggers)
        self.comparison.setShowGrid(False)
        self.comparison.setMinimumHeight(300)
        comparison_table_header = self.comparison.horizontalHeader()
        comparison_table_header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column, width in enumerate((86, 86, 74, 58, 58, 58, 58, 74, 76, 78, 82), 1):
            comparison_table_header.setSectionResizeMode(column, QHeaderView.Fixed)
            self.comparison.setColumnWidth(column, width)
        comparison_layout.addWidget(self.comparison)
        self.journal = QTableWidget(0, 9)
        self.journal.setMinimumWidth(0)
        self.journal.setMinimumHeight(300)
        self.journal.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.journal.setHorizontalHeaderLabels((
            "Day", "Model", "Shape", "Status", "Tokens", "Active", "Edits", "Files", "Tests / commands",
        ))
        self.journal.verticalHeader().setVisible(False)
        self.journal.setEditTriggers(QTableWidget.NoEditTriggers)
        self.journal.setSelectionMode(QTableWidget.NoSelection)
        header = self.journal.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column in range(self.journal.columnCount()):
            if column != 1:
                header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        comparison_layout.addWidget(self.journal)
        self.journal.hide()
        self.bottom_view.currentIndexChanged.connect(self._bottom_view_changed)
        layout.addWidget(self.comparison_panel)

        # Recompose the original long page into the selected numbered Focus
        # Steps workspace while retaining the established widgets and actions.
        summary_layout.removeWidget(self.density)
        summary_divider.hide()
        layout.removeWidget(self.summary_panel)
        layout.insertWidget(1, self.summary_panel)
        layout.removeWidget(self.comparison_panel)
        scroll.setWidget(self._content)

        self.section_charts = {"overview": list(self.charts)}
        self.section_chart_selectors = {"overview": list(self.chart_selectors)}
        self.section_chart_panels = {"overview": list(self.chart_panels)}
        self.section_chart_options = {
            "overview": (OVERVIEW_LINE_VIEWS, OVERVIEW_BAR_VIEWS),
        }
        self.section_chart_hosts = {"overview": self.charts_host}
        model_scroll, self.model_content, model_layout = self._new_statistics_page()
        self._add_page_heading(
            model_layout,
            "Models",
            "Choose a model, then filter or sort its observed reasoning settings",
        )
        controls = QFrame()
        controls.setObjectName("card")
        control_layout = QGridLayout(controls)
        control_layout.setContentsMargins(11, 8, 11, 8)
        control_layout.setSpacing(7)
        self.model_control_layout = control_layout
        self.model_control_labels = {
            "model": _label("Model", "sectionLabel"),
            "reasoning": _label("Reasoning", "sectionLabel"),
            "sort": _label("Sort", "sectionLabel"),
        }
        self.base_model_filter = QComboBox()
        self.base_model_filter.setMinimumWidth(190)
        self.reasoning_filter = QComboBox()
        self.reasoning_filter.setMinimumWidth(145)
        self.model_sort = QComboBox()
        self.model_sort.addItem("Usage high to low", "usage")
        self.model_sort.addItem("Model name", "name")
        self.model_sort.addItem("Reasoning", "reasoning")
        self.model_sort.setMinimumWidth(150)
        self._arrange_model_controls(False)
        model_layout.addWidget(controls)
        self.base_model_filter.currentIndexChanged.connect(
            lambda _index: self._model_controls_changed()
        )
        self.reasoning_filter.currentIndexChanged.connect(
            lambda _index: self._model_controls_changed()
        )
        self.model_sort.currentIndexChanged.connect(
            lambda _index: self._model_controls_changed()
        )
        model_host = self._create_chart_pair(
            "models", model_layout, MODEL_LINE_VIEWS, MODEL_BAR_VIEWS,
        )
        self.section_chart_hosts["models"] = model_host
        self._add_section_export_row("models", model_layout)
        model_layout.addWidget(self.comparison_panel)
        model_scroll.setWidget(self.model_content)

        productivity_scroll, self.productivity_content, productivity_layout = self._new_statistics_page()
        self._add_page_heading(
            productivity_layout,
            "Productivity",
            "Coding activity observed alongside tokens, active time, and limit use",
        )
        productivity_layout.addWidget(self.density)
        productivity_host = self._create_chart_pair(
            "productivity", productivity_layout,
            PRODUCTIVITY_LINE_VIEWS, PRODUCTIVITY_BAR_VIEWS,
        )
        self.section_chart_hosts["productivity"] = productivity_host
        self._add_section_export_row("productivity", productivity_layout)
        self.productivity_journal_panel, self.productivity_journal = self._create_journal_panel()
        productivity_layout.addWidget(self.productivity_journal_panel)
        productivity_scroll.setWidget(self.productivity_content)

        compare_scroll, self.compare_content, compare_layout = self._new_statistics_page()
        self._add_page_heading(
            compare_layout,
            "Compare",
            "Compare two to four observed models against one clear baseline",
        )
        self.compare_roster = QFrame()
        self.compare_roster.setObjectName("card")
        roster_layout = QVBoxLayout(self.compare_roster)
        roster_layout.setContentsMargins(11, 9, 11, 10)
        roster_layout.setSpacing(7)
        roster_header = QHBoxLayout()
        roster_copy, _roster_title, _roster_caption = _inline_copy(
            "Comparison roster",
            "Select models and reasoning settings; the first selection sets the reference line",
        )
        roster_header.addWidget(roster_copy, 1)
        self.compare_reasoning_button = make_button("Compare reasoning", "ghost")
        self.compare_reasoning_button.setToolTip(
            "Compare the observed reasoning settings for the baseline model"
        )
        self.compare_reasoning_button.clicked.connect(
            lambda _checked=False: self._compare_reasoning_variants()
        )
        roster_header.addWidget(self.compare_reasoning_button)
        self.compare_add_button = make_button("+ Add model", "primary")
        self.compare_add_button.setToolTip("Add another model to this comparison")
        self.compare_add_button.clicked.connect(lambda _checked=False: self._add_compare_row())
        roster_header.addWidget(self.compare_add_button)
        roster_layout.addLayout(roster_header)
        self.compare_roster_layout = QVBoxLayout()
        self.compare_roster_layout.setContentsMargins(0, 2, 0, 0)
        self.compare_roster_layout.setSpacing(6)
        roster_layout.addLayout(self.compare_roster_layout)
        self.compare_rows: list[dict] = []
        self._reasoning_compare_state: dict | None = None
        self._reasoning_compare_edited = False
        self._add_compare_row(render=False)
        self._add_compare_row(render=False)
        self.compare_roster_note = ElidedLabel("")
        self.compare_roster_note.setObjectName("faint")
        roster_layout.addWidget(self.compare_roster_note)
        compare_layout.addWidget(self.compare_roster)

        compare_host = self._create_chart_pair(
            "compare", compare_layout, COMPARE_LINE_VIEWS, COMPARE_BAR_VIEWS,
        )
        self.section_chart_hosts["compare"] = compare_host
        self._add_section_export_row("compare", compare_layout)

        self.compare_table_panel = QFrame()
        self.compare_table_panel.setObjectName("card")
        self.compare_table_panel.setMinimumWidth(0)
        self.compare_table_panel.setMinimumHeight(330)
        compare_table_layout = QVBoxLayout(self.compare_table_panel)
        compare_table_layout.setContentsMargins(0, 0, 0, 0)
        compare_table_header = QVBoxLayout()
        compare_table_header.setContentsMargins(12, 9, 12, 7)
        compare_table_copy, _compare_title, _compare_caption = _inline_copy(
            "Head-to-head detail",
            "Each cell shows the observed value and its signed difference from the baseline",
        )
        compare_table_header.addWidget(compare_table_copy)
        compare_table_layout.addLayout(compare_table_header)
        self.compare_table = QTableWidget(0, 13)
        self.compare_table.setMinimumWidth(0)
        self.compare_table.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.compare_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.compare_table.setHorizontalHeaderLabels((
            "Model", "Reasoning", "Tokens", "Tasks", "Edits", "Files", "Tests",
            "Commands", "Active", "5h burn", "Weekly burn", "Tokens / task", "Tasks / 1M",
        ))
        self.compare_table.verticalHeader().setVisible(False)
        self.compare_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.compare_table.setSelectionMode(QTableWidget.NoSelection)
        self.compare_table.setShowGrid(False)
        self.compare_table.setMinimumHeight(280)
        compare_header = self.compare_table.horizontalHeader()
        compare_header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, self.compare_table.columnCount()):
            compare_header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        compare_table_layout.addWidget(self.compare_table)
        compare_layout.addWidget(self.compare_table_panel)
        compare_scroll.setWidget(self.compare_content)

        community_scroll = self._build_community_page()

        self.statistics_stack = QStackedWidget()
        self.statistics_stack.addWidget(scroll)
        self.statistics_stack.addWidget(model_scroll)
        self.statistics_stack.addWidget(productivity_scroll)
        self.statistics_stack.addWidget(compare_scroll)
        self.statistics_stack.addWidget(community_scroll)
        self._statistics_sections = ("overview", "models", "productivity", "compare", "community")
        self._active_statistics_section = "overview"
        self._dirty_chart_sections = set(self._statistics_sections)

        self.statistics_rail = QFrame()
        self.statistics_rail.setObjectName("statisticsRail")
        self.statistics_rail.setFixedWidth(116)
        rail_layout = QVBoxLayout(self.statistics_rail)
        rail_layout.setContentsMargins(8, 14, 8, 12)
        rail_layout.setSpacing(8)
        self.statistics_nav_buttons = {}
        for number, key, title in (
            ("01", "overview", "Overview"),
            ("02", "models", "Models"),
            ("03", "productivity", "Productivity"),
            ("04", "compare", "Compare"),
            ("05", "community", "Community"),
        ):
            button = make_button(f"{number}\n{title}", "ghost")
            button.setObjectName("statisticsNavButton")
            button.setCheckable(True)
            button.setMinimumHeight(66)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, section_key=key: self._select_statistics_section(section_key)
            )
            rail_layout.addWidget(button)
            self.statistics_nav_buttons[key] = button
        rail_layout.addStretch(1)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self.statistics_rail)
        body_layout.addWidget(self.statistics_stack, 1)
        outer.addWidget(body, 1)
        self._select_statistics_section("overview")
        self.apply_theme()

    def _new_statistics_page(self) -> tuple[QScrollArea, QWidget, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 13, 16, 24)
        layout.setSpacing(10)
        return scroll, content, layout

    def _add_page_heading(self, layout: QVBoxLayout, title: str, caption: str) -> None:
        host = QWidget()
        host.setFixedHeight(32)
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        copy, _title, _caption = _inline_copy(title, caption, title_size=18)
        row.addWidget(copy, 1)
        layout.addWidget(host)

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
            [("Dots", "dots"), ("Lines", "lines"), ("Bars", "bars")],
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
            title = "Community efficiency map"
            caption = "Tokens per task across the x-axis; tasks per 5h across the y-axis"
            kind, chart_metric = "community_scatter", "tasksPerSession"
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

        return build_submission_payload(self._visible_groups(), days=1)

    def _create_chart_pair(
        self,
        section: str,
        parent_layout: QVBoxLayout,
        line_views: tuple,
        bar_views: tuple,
    ) -> QWidget:
        host = ResponsiveChartHost()
        section_panels = []
        section_charts = []
        section_selectors = []
        for column, view_options in enumerate((line_views, bar_views)):
            panel = QFrame()
            panel.setObjectName("card")
            panel.setMinimumWidth(0)
            panel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
            box = QVBoxLayout(panel)
            box.setContentsMargins(11, 9, 11, 9)
            box.setSpacing(4)
            top = QHBoxLayout()
            chart_copy, title_label, caption = _inline_copy("Chart", "")
            top.addWidget(chart_copy, 1)
            selector = QComboBox()
            selector.setFixedWidth(174)
            for option_label, *_spec in view_options:
                selector.addItem(option_label)
            selector.currentIndexChanged.connect(
                lambda _index, section_key=section, pane=column: self._render_chart(section_key, pane)
            )
            reset = make_button("Reset", "ghost")
            focus = make_button("Focus", "ghost")
            top.addWidget(selector)
            top.addWidget(reset)
            top.addWidget(focus)
            box.addLayout(top)
            chart = BenchmarkChart(self._tm)
            reset.clicked.connect(chart.reset_view)
            focus.clicked.connect(
                lambda _checked=False, current=chart, label=title_label: self._focus_chart(current, label.text())
            )
            box.addWidget(chart, 1)
            host.add_panel(panel)
            section_panels.append((panel, title_label, caption))
            section_charts.append(chart)
            section_selectors.append(selector)
        parent_layout.addWidget(host)
        self.section_charts[section] = section_charts
        self.section_chart_selectors[section] = section_selectors
        self.section_chart_panels[section] = section_panels
        self.section_chart_options[section] = (line_views, bar_views)
        return host

    def _add_section_export_row(self, section: str, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.addStretch(1)
        csv_button = make_button("Export CSV", "ghost")
        png_button = make_button("Export PNG", "ghost")
        csv_button.clicked.connect(self._choose_csv)
        png_button.clicked.connect(self._choose_png)
        row.addWidget(csv_button)
        row.addWidget(png_button)
        layout.addLayout(row)

    def _create_journal_panel(self) -> tuple[QFrame, QTableWidget]:
        panel = QFrame()
        panel.setObjectName("card")
        panel.setMinimumWidth(0)
        panel.setMinimumHeight(410)
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.setContentsMargins(12, 9, 12, 7)
        journal_copy, _journal_title, _journal_caption = _inline_copy(
            "Recent observed work",
            "Latest numeric activity from local provider history",
        )
        header.addWidget(journal_copy, 1)
        layout.addLayout(header)
        table = QTableWidget(0, 9)
        table.setHorizontalHeaderLabels((
            "Day", "Model", "Work type", "Result", "Tokens", "Time",
            "Edits", "Files", "Tests / commands",
        ))
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(38)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionMode(QTableWidget.NoSelection)
        table.setMinimumWidth(0)
        table.setMinimumHeight(350)
        table.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        table_header = table.horizontalHeader()
        table_header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column in range(table.columnCount()):
            if column != 1:
                table_header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        layout.addWidget(table)
        return panel, table

    def _add_compare_row(self, *, render: bool = True) -> None:
        if len(getattr(self, "compare_rows", [])) >= 4:
            return
        host = QFrame()
        host.setObjectName("compareRosterRow")
        row_layout = QHBoxLayout(host)
        row_layout.setContentsMargins(9, 7, 9, 7)
        row_layout.setSpacing(8)
        role = _label("Model", "compareRole", bold=True)
        role.setFixedWidth(74)
        role.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        model = QComboBox()
        model.setMinimumWidth(220)
        reasoning = QComboBox()
        reasoning.setMinimumWidth(150)
        remove = make_button("-", "ghost")
        remove.setFixedSize(32, 30)
        remove.setToolTip("Remove this model from the comparison")
        row_layout.addWidget(role)
        row_layout.addWidget(model, 2)
        row_layout.addWidget(reasoning, 1)
        row_layout.addStretch(1)
        row_layout.addWidget(remove)
        entry = {
            "host": host, "role": role, "model": model,
            "reasoning": reasoning, "remove": remove,
        }
        self.compare_rows.append(entry)
        self.compare_roster_layout.addWidget(host)
        model.currentIndexChanged.connect(
            lambda _index, current=entry: self._compare_model_changed(current)
        )
        reasoning.currentIndexChanged.connect(
            lambda _index: self._compare_controls_changed()
        )
        remove.clicked.connect(
            lambda _checked=False, current=entry: self._remove_compare_row(current)
        )
        self._refresh_compare_row_roles()
        if render:
            self._mark_reasoning_compare_edited()
            self._sync_compare_controls(self._visible_groups())
            self._render()

    def _remove_compare_row(self, row: dict) -> None:
        if len(self.compare_rows) <= 2 or row not in self.compare_rows:
            return
        self.compare_rows.remove(row)
        row["host"].deleteLater()
        self._mark_reasoning_compare_edited()
        self._refresh_compare_row_roles()
        self._render()

    def _refresh_compare_row_roles(self) -> None:
        for index, row in enumerate(self.compare_rows):
            baseline = index == 0
            row["role"].setText("Baseline" if baseline else f"Model {index + 1}")
            row["role"].setProperty("baseline", baseline)
            row["role"].setToolTip(
                "Reference model for every signed difference"
                if baseline else
                "Compared with the baseline model"
            )
            row["role"].style().unpolish(row["role"])
            row["role"].style().polish(row["role"])
            row["remove"].setEnabled(len(self.compare_rows) > 2)
        if hasattr(self, "compare_add_button"):
            self.compare_add_button.setEnabled(len(self.compare_rows) < 4)

    def _sync_compare_controls(self, groups: list[dict]) -> None:
        if getattr(self, "_syncing_compare_controls", False):
            return
        self._syncing_compare_controls = True
        try:
            base_groups = aggregate_base_model_groups(groups)
            base_keys = [
                str(group.get("baseModelKey") or base_model_key(group))
                for group in base_groups
            ]
            used_defaults: set[str] = set()
            for index, row in enumerate(self.compare_rows):
                model: QComboBox = row["model"]
                selected_model = str(model.currentData() or "")
                model.blockSignals(True)
                model.clear()
                for group in base_groups:
                    model.addItem(
                        str(group.get("modelName") or "Model"),
                        str(group.get("baseModelKey") or base_model_key(group)),
                    )
                if selected_model not in base_keys:
                    selected_model = next(
                        (key for key in base_keys if key not in used_defaults),
                        base_keys[index % len(base_keys)] if base_keys else "",
                    )
                model_index = model.findData(selected_model)
                model.setCurrentIndex(model_index if model_index >= 0 else -1)
                model.blockSignals(False)
                if selected_model:
                    used_defaults.add(selected_model)
                self._sync_compare_reasoning(row, groups)
            self._refresh_compare_row_roles()
            self._update_compare_reasoning_button(groups)
        finally:
            self._syncing_compare_controls = False

    def _reasoning_variants_for_model(
        self, model_key: str, groups: list[dict]
    ) -> list[tuple[str, str]]:
        variants = {
            str(group.get("reasoningEffort") or ""):
            str(group.get("reasoningEffortName") or "") or "Default"
            for group in groups
            if base_model_key(group) == model_key
        }
        return sorted(
            variants.items(),
            key=lambda item: (self._reasoning_rank(item[0]), item[1].lower()),
        )

    def _update_compare_reasoning_button(self, groups: list[dict]) -> None:
        if not hasattr(self, "compare_reasoning_button") or not self.compare_rows:
            return
        if self._reasoning_compare_state is not None:
            self.compare_reasoning_button.setText("Restore comparison")
            self.compare_reasoning_button.setEnabled(True)
            self.compare_reasoning_button.setToolTip(
                "Restore the model and reasoning selections used before this comparison"
            )
            return
        baseline_model = str(self.compare_rows[0]["model"].currentData() or "")
        count = len(self._reasoning_variants_for_model(baseline_model, groups))
        self.compare_reasoning_button.setText("Compare reasoning")
        self.compare_reasoning_button.setEnabled(count >= 2)
        self.compare_reasoning_button.setToolTip(
            "Compare the observed reasoning settings for the baseline model"
            if count >= 2 else
            "This model has fewer than two observed reasoning settings"
        )

    def _sync_compare_reasoning(self, row: dict, groups: list[dict]) -> None:
        model_key = str(row["model"].currentData() or "")
        reasoning: QComboBox = row["reasoning"]
        selected_data = reasoning.currentData()
        selected = "all" if selected_data is None else str(selected_data)
        variants = self._reasoning_variants_for_model(model_key, groups)
        reasoning.blockSignals(True)
        reasoning.clear()
        reasoning.addItem("All reasoning", "all")
        for effort, label in variants:
            reasoning.addItem(label, effort)
        selected_index = reasoning.findData(selected)
        reasoning.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        reasoning.blockSignals(False)

    def _compare_model_changed(self, row: dict) -> None:
        if getattr(self, "_syncing_compare_controls", False):
            return
        self._sync_compare_reasoning(row, self._visible_groups())
        self._update_compare_reasoning_button(self._visible_groups())
        self._compare_controls_changed()

    def _compare_reasoning_variants(self) -> None:
        """Toggle between a model's reasoning efforts and the previous roster."""

        if not self.compare_rows:
            return
        groups = self._visible_groups()
        if self._reasoning_compare_state is not None:
            snapshot = list(self._reasoning_compare_state.get("snapshot") or [])
            self._reasoning_compare_state = None
            self._reasoning_compare_edited = False
            self._set_compare_selections(snapshot, groups)
            self._update_compare_reasoning_button(groups)
            self._render()
            return

        model_key = str(self.compare_rows[0]["model"].currentData() or "")
        all_variants = self._reasoning_variants_for_model(model_key, groups)
        if len(all_variants) < 2:
            self.compare_roster_note.setText(
                "This model needs at least two observed reasoning settings to compare."
            )
            return
        snapshot = [
            {
                "model": str(row["model"].currentData() or ""),
                "reasoning": (
                    "all"
                    if row["reasoning"].currentData() is None
                    else str(row["reasoning"].currentData())
                ),
            }
            for row in self.compare_rows
        ]
        variants = all_variants[:4]
        model_name = str(self.compare_rows[0]["model"].currentText() or "Model")
        selections = [
            {"model": model_key, "reasoning": effort}
            for effort, _label_text in variants
        ]
        self._set_compare_selections(selections, groups)
        self._reasoning_compare_state = {
            "snapshot": snapshot,
            "model": model_name,
            "shown": len(variants),
            "total": len(all_variants),
        }
        self._reasoning_compare_edited = False
        self._update_compare_reasoning_button(groups)
        self._render()

    def _set_compare_selections(self, selections: list[dict], groups: list[dict]) -> None:
        """Resize and populate the comparison roster without intermediate renders."""

        if len(selections) < 2:
            return
        while len(self.compare_rows) < len(selections):
            self._add_compare_row(render=False)
        while len(self.compare_rows) > len(selections):
            removed = self.compare_rows.pop()
            removed["host"].deleteLater()
        self._sync_compare_controls(groups)
        self._syncing_compare_controls = True
        try:
            for row, selection in zip(self.compare_rows, selections):
                model: QComboBox = row["model"]
                model.blockSignals(True)
                model.setCurrentIndex(model.findData(str(selection.get("model") or "")))
                model.blockSignals(False)
                self._sync_compare_reasoning(row, groups)
                reasoning: QComboBox = row["reasoning"]
                reasoning.blockSignals(True)
                reasoning.setCurrentIndex(
                    reasoning.findData(str(selection.get("reasoning") or ""))
                )
                reasoning.blockSignals(False)
        finally:
            self._syncing_compare_controls = False
        self._refresh_compare_row_roles()

    def _mark_reasoning_compare_edited(self) -> None:
        if self._reasoning_compare_state is not None:
            self._reasoning_compare_edited = True

    def _compare_controls_changed(self) -> None:
        if not getattr(self, "_syncing_compare_controls", False):
            self._mark_reasoning_compare_edited()
            self._render()

    def _head_to_head(self) -> dict:
        cached = getattr(self, "_head_to_head_cache", None)
        if cached is not None:
            return cached
        selections = [
            {
                "baseModelKey": str(row["model"].currentData() or ""),
                "reasoning": (
                    "all"
                    if row["reasoning"].currentData() is None
                    else str(row["reasoning"].currentData())
                ),
            }
            for row in self.compare_rows
        ]
        result = build_head_to_head(self._visible_groups(), selections)
        self._head_to_head_cache = result
        return result

    @staticmethod
    def _comparison_cell(metric: str, item: dict, baseline: bool) -> tuple[str, str]:
        value = float(item.get("value") or 0)
        delta = float(item.get("delta") or 0)
        if metric == "totalTokens":
            absolute = _format_tokens(value)
            delta_text = _format_tokens(abs(delta))
        elif metric == "activeMs":
            absolute = _format_duration(value)
            delta_text = _format_duration(abs(delta))
        elif metric in {"shortBurn", "weeklyBurn"}:
            absolute = _format_points(value)
            delta_text = _format_points(abs(delta))
        elif metric in {"tokensPerTask", "tasksPerMillion"}:
            absolute = _format_number(value, 1)
            delta_text = _format_number(abs(delta), 1)
        else:
            absolute = f"{int(value):,}"
            delta_text = f"{int(abs(delta)):,}"
        if baseline:
            return f"{absolute}\nBaseline", f"Observed value: {absolute}\nComparison role: Baseline"
        sign = "+" if delta >= 0 else "-"
        difference = "0" if delta == 0 else f"{sign}{delta_text}"
        return (
            f"{absolute}\n{difference}",
            f"Observed value: {absolute}\nDifference from baseline: {difference}",
        )

    def _fill_head_to_head(self, head_to_head: dict) -> None:
        rows = list(head_to_head.get("rows") or [])
        self.compare_table.setRowCount(len(rows))
        metrics = (
            "totalTokens", "completedTasks", "edits", "filesChanged", "tests",
            "commands", "activeMs", "shortBurn", "weeklyBurn",
            "tokensPerTask", "tasksPerMillion",
        )
        for row_index, row in enumerate(rows):
            group = row.get("group") or {}
            baseline = bool(row.get("baseline"))
            model_name = str(group.get("modelName") or "Model")
            if baseline:
                model_name = f"{model_name} [Baseline]"
            self.compare_table.setItem(row_index, 0, QTableWidgetItem(model_name))
            self.compare_table.setItem(
                row_index,
                1,
                QTableWidgetItem(str(group.get("comparisonReasoning") or "All reasoning")),
            )
            for column, metric in enumerate(metrics, 2):
                text, tooltip = self._comparison_cell(
                    metric, (row.get("metrics") or {}).get(metric) or {}, baseline
                )
                cell = QTableWidgetItem(text)
                cell.setToolTip(tooltip)
                cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.compare_table.setItem(row_index, column, cell)
            self.compare_table.setRowHeight(row_index, 48)

    def _select_statistics_section(self, section: str) -> None:
        if section not in getattr(self, "_statistics_sections", ()):
            section = "overview"
        self._active_statistics_section = section
        self.statistics_stack.setCurrentIndex(self._statistics_sections.index(section))
        for key, button in self.statistics_nav_buttons.items():
            active = key == section
            if bool(button.property("active")) != active:
                button.setChecked(active)
                button.setProperty("active", active)
                button.style().unpolish(button)
                button.style().polish(button)
        community = section == "community"
        self.account_filter.setEnabled(not community)
        self.account_filter.setToolTip(
            "Community results are anonymous and are not tied to a local account."
            if community else ""
        )
        if community:
            self._render_community()
        if section in getattr(self, "_dirty_chart_sections", set()):
            self._render_section_charts(section)

    def _arrange_model_controls(self, wide: bool) -> None:
        if not hasattr(self, "model_control_layout"):
            return
        if getattr(self, "_model_controls_wide", None) == wide:
            return
        self._model_controls_wide = wide
        layout = self.model_control_layout
        labels = self.model_control_labels
        if wide:
            placements = (
                (labels["model"], 0, 0, 1, 1),
                (self.base_model_filter, 0, 1, 1, 1),
                (labels["reasoning"], 0, 2, 1, 1),
                (self.reasoning_filter, 0, 3, 1, 1),
                (labels["sort"], 0, 4, 1, 1),
                (self.model_sort, 0, 5, 1, 1),
            )
            stretch_column = 6
        else:
            placements = (
                (labels["model"], 0, 0, 1, 1),
                (self.base_model_filter, 0, 1, 1, 2),
                (labels["reasoning"], 0, 3, 1, 1),
                (self.reasoning_filter, 0, 4, 1, 2),
                (labels["sort"], 1, 0, 1, 1),
                (self.model_sort, 1, 1, 1, 2),
            )
            stretch_column = 3
        for widget, row, column, row_span, column_span in placements:
            layout.addWidget(widget, row, column, row_span, column_span)
        for column in range(7):
            layout.setColumnStretch(column, 1 if column == stretch_column else 0)

    def resizeEvent(self, event) -> None:
        wide = event.size().width() >= 1450
        self._arrange_model_controls(event.size().width() >= 980)
        if hasattr(self, "density"):
            self.density._arrange_metrics(8 if wide else 4)
        for section in ("models", "productivity", "compare"):
            host = getattr(self, "section_chart_hosts", {}).get(section)
            if isinstance(host, ResponsiveChartHost):
                host._arrange(wide)
        super().resizeEvent(event)

    def set_profiles(self, profiles: list[dict]) -> None:
        self._profiles = [dict(profile) for profile in profiles]
        selected = self.account_filter.currentData()
        self.account_filter.blockSignals(True)
        self.account_filter.clear()
        self.account_filter.addItem("All visible accounts", "all")
        for profile in self._profiles:
            if not bool(profile.get("hidden")) and hub_core.provider_key(profile) in {"codex", "claude"}:
                self.account_filter.addItem(str(profile.get("name") or "Account"), hub_core.profile_id(profile))
        index = self.account_filter.findData(selected)
        self.account_filter.setCurrentIndex(index if index >= 0 else 0)
        self.account_filter.blockSignals(False)
        self._refresh_debounce.start()

    def _manual_refresh(self) -> None:
        self._refresh_debounce.stop()
        self.refresh_analytics()

    def refresh_analytics(self) -> None:
        self._refresh_debounce.stop()
        if self._worker is not None and self._worker.isRunning():
            self._pending_refresh = True
            return
        self._pending_refresh = False
        self.refresh_button.setEnabled(False)
        self.scan_spinner.start()
        self.scan_status.setText("Scanning history...")
        self._scan_generation += 1
        generation = self._scan_generation
        self._worker = AnalyticsWorker(self._profiles, self)
        self._worker.completed.connect(
            lambda snapshot, token=generation: self._analytics_ready(snapshot, token)
        )
        self._worker.failed.connect(
            lambda message, token=generation: self._analytics_failed(message, token)
        )
        self._worker.finished.connect(self._worker_finished)
        self._worker.start(QThread.LowPriority)

    def _analytics_ready(self, snapshot: dict, generation: int | None = None) -> None:
        if generation is not None and generation != self._scan_generation:
            return
        self._snapshot = snapshot
        generated = str(snapshot.get("generatedAtUtc") or "")
        try:
            when = dt.datetime.fromisoformat(generated).astimezone().strftime("%H:%M")
        except ValueError:
            when = "now"
        self.scan_status.setText(f"Updated {when}")
        self._filter_changed()
        self.history_updated.emit()
        self.activity.emit("Real-world model analytics refreshed.")

    def _analytics_failed(self, message: str, generation: int | None = None) -> None:
        if generation is not None and generation != self._scan_generation:
            return
        self.scan_status.setText("Analytics unavailable")
        self.activity.emit(f"Model analytics failed: {message}")

    def _worker_finished(self) -> None:
        self.scan_spinner.stop()
        self.refresh_button.setEnabled(True)
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        if self._pending_refresh:
            self.refresh_analytics()

    def _filter_changed(self) -> None:
        view = build_benchmark_view(
            self._snapshot,
            account_id=str(self.account_filter.currentData() or "all"),
            days=int(self.range_filter.currentData() or 30),
        )
        self._view = view
        self._render()
        if hasattr(self, "community_provider"):
            self._refresh_community_results()

    def _selected_profiles(self) -> list[dict]:
        """Return the account-filter input without changing model grouping."""
        selected = str(self.account_filter.currentData() or "all")
        return [
            profile for profile in self._profiles
            if not bool(profile.get("hidden"))
            and (selected == "all" or hub_core.profile_id(profile) == selected)
        ]

    def _visible_groups(self) -> list[dict]:
        return list(self._view.get("groups", []))

    @staticmethod
    def _reasoning_rank(value: object) -> tuple[int, str]:
        normalized = str(value or "").strip().lower()
        order = {"": 0, "low": 1, "medium": 2, "high": 3, "xhigh": 4, "ultra": 5}
        return order.get(normalized, 50), normalized

    def _sync_model_controls(self, groups: list[dict]) -> None:
        if getattr(self, "_syncing_model_controls", False):
            return
        self._syncing_model_controls = True
        try:
            selected_model = self.base_model_filter.currentData()
            base_groups = aggregate_base_model_groups(groups)
            self.base_model_filter.blockSignals(True)
            self.base_model_filter.clear()
            self.base_model_filter.addItem("All base models", "all")
            for group in base_groups:
                self.base_model_filter.addItem(
                    str(group.get("modelName") or "Model"),
                    str(group.get("baseModelKey") or base_model_key(group)),
                )
            model_index = self.base_model_filter.findData(selected_model)
            self.base_model_filter.setCurrentIndex(model_index if model_index >= 0 else 0)
            self.base_model_filter.blockSignals(False)

            selected_model = str(self.base_model_filter.currentData() or "all")
            selected_reasoning = self.reasoning_filter.currentData()
            candidates = [
                group for group in groups
                if selected_model == "all" or base_model_key(group) == selected_model
            ]
            efforts = {}
            for group in candidates:
                effort = str(group.get("reasoningEffort") or "")
                efforts[effort] = str(group.get("reasoningEffortName") or "") or "Default"
            self.reasoning_filter.blockSignals(True)
            self.reasoning_filter.clear()
            self.reasoning_filter.addItem("All reasoning", "all")
            for effort, name in sorted(
                efforts.items(), key=lambda item: (self._reasoning_rank(item[0]), item[1].lower())
            ):
                self.reasoning_filter.addItem(name, effort)
            reasoning_index = self.reasoning_filter.findData(selected_reasoning)
            self.reasoning_filter.setCurrentIndex(reasoning_index if reasoning_index >= 0 else 0)
            self.reasoning_filter.blockSignals(False)
        finally:
            self._syncing_model_controls = False

    def _model_variant_groups(self) -> list[dict]:
        selected_model = str(self.base_model_filter.currentData() or "all")
        selected_reasoning = str(self.reasoning_filter.currentData() or "all")
        output = []
        for source in self._visible_groups():
            if selected_model != "all" and base_model_key(source) != selected_model:
                continue
            effort = str(source.get("reasoningEffort") or "")
            if selected_reasoning != "all" and effort != selected_reasoning:
                continue
            group = dict(source)
            group["baseModelKey"] = base_model_key(source)
            if selected_model != "all":
                group["modelLabel"] = str(source.get("reasoningEffortName") or "") or "Default"
            output.append(group)

        sort_mode = str(self.model_sort.currentData() or "usage")
        if sort_mode == "name":
            output.sort(key=lambda item: (
                str(item.get("modelName") or "").lower(),
                self._reasoning_rank(item.get("reasoningEffort")),
            ))
        elif sort_mode == "reasoning":
            output.sort(key=lambda item: (
                self._reasoning_rank(item.get("reasoningEffort")),
                str(item.get("modelName") or "").lower(),
            ))
        else:
            output.sort(key=lambda item: -int(item.get("totalTokens") or 0))
        return output

    def _groups_for_section(self, section: str) -> list[dict]:
        if section == "models":
            return self._model_variant_groups()
        if section == "compare":
            return list(self._head_to_head().get("groups") or [])
        if section == "community":
            return self._community_ranked_groups()
        return self._visible_groups()

    def _model_controls_changed(self) -> None:
        if getattr(self, "_syncing_model_controls", False):
            return
        self._render()

    def _render(self) -> None:
        self._head_to_head_cache = None
        groups = self._visible_groups()
        self._sync_model_controls(groups)
        self._sync_compare_controls(groups)
        base_groups = aggregate_base_model_groups(groups)
        model_groups = self._model_variant_groups()
        model_base_groups = aggregate_base_model_groups(model_groups)
        sort_mode = str(self.model_sort.currentData() or "usage")
        if sort_mode == "name":
            model_base_groups.sort(key=lambda item: str(item.get("modelName") or "").lower())
        elif sort_mode == "reasoning":
            model_base_groups.sort(key=lambda item: (
                min(
                    (self._reasoning_rank(variant.get("effort")) for variant in item.get("reasoningVariants", [])),
                    default=(99, ""),
                ),
                str(item.get("modelName") or "").lower(),
            ))
        input_side = sum(
            int(group.get("inputTokens") or 0)
            + int(group.get("cachedInputTokens") or 0)
            + int(group.get("cacheCreationTokens") or 0)
            for group in groups
        )
        cached = sum(int(group.get("cachedInputTokens") or 0) for group in groups)
        cache_percent = cached * 100 / input_side if input_side else None
        summary = {
            "tokens": sum(int(group.get("totalTokens") or 0) for group in groups),
            "tasks": sum(int(group.get("completedTasks") or 0) for group in groups),
            "edits": sum(int(group.get("edits") or 0) for group in groups),
            "tests": sum(int(group.get("tests") or 0) for group in groups),
            "short": sum(float(group.get("shortBurn") or 0) for group in groups),
            "weekly": sum(float(group.get("weeklyBurn") or 0) for group in groups),
        }
        self.tiles["tokens"].set_data(
            _format_tokens(summary["tokens"]), "Tokens reported in local provider history"
        )
        self.tiles["models"].set_data(
            str(len(base_groups)), f"{len(groups)} observed model/reasoning configurations"
        )
        self.tiles["cache"].set_data(
            "Not exposed" if cache_percent is None else f"{cache_percent:.0f}%",
            "Cached input share where the provider exposes it",
        )
        self.tiles["tasks"].set_data(f"{summary['tasks']:,}", "Observed completed coding tasks")
        self.tiles["short"].set_data(
            _format_points(summary["short"]), "Measured between snapshots less than 20m apart"
        )
        self.tiles["weekly"].set_data(
            _format_points(summary["weekly"]), "Measured increases; reset decreases excluded"
        )
        self.density.set_groups(base_groups)
        self._fill_comparison(model_base_groups)
        self._fill_journal()
        head_to_head = self._head_to_head()
        self._fill_head_to_head(head_to_head)
        compare_groups = list(head_to_head.get("groups") or [])
        requested_compare_rows = sum(
            1 for row in self.compare_rows if str(row["model"].currentData() or "")
        )
        reasoning_state = self._reasoning_compare_state
        if reasoning_state is not None and self._reasoning_compare_edited:
            compare_note = "Reasoning comparison edited. Restore returns to the previous roster."
        elif reasoning_state is not None:
            shown = int(reasoning_state.get("shown") or 0)
            total = int(reasoning_state.get("total") or shown)
            count_text = (
                f"{shown} of {total} observed settings"
                if shown < total else
                f"{shown} observed settings"
            )
            compare_note = (
                f"Comparing {reasoning_state.get('model') or 'model'} reasoning: "
                f"{count_text}. Restore returns to the previous roster."
            )
        elif len(compare_groups) < 2:
            compare_note = "Choose at least two different model/reasoning combinations."
        elif len(compare_groups) < requested_compare_rows:
            compare_note = "An exact duplicate selection is shown once; choose another model or reasoning setting."
        else:
            compare_note = "The first row is the baseline. Change either dropdown to update only that series."
        self.compare_roster_note.setText(compare_note)
        self._render_charts()
        self._render_community()

    def _render_charts(self) -> None:
        self._dirty_chart_sections.update(self.section_charts)
        self._render_section_charts(self._active_statistics_section)

    def _render_section_charts(self, section: str) -> None:
        charts = self.section_charts.get(section, [])
        for pane in range(len(charts)):
            self._render_chart(section, pane)
        self._dirty_chart_sections.discard(section)

    def _render_chart(self, section_or_pane, pane: int | None = None) -> None:
        section = "overview" if pane is None else str(section_or_pane)
        pane = int(section_or_pane) if pane is None else pane
        charts = self.section_charts.get(section, [])
        selectors = self.section_chart_selectors.get(section, [])
        panels = self.section_chart_panels.get(section, [])
        if pane < 0 or pane >= len(charts):
            return
        groups = self._groups_for_section(section)
        selected = selectors[pane].currentIndex()
        view_options = self.section_chart_options[section][pane]
        if selected < 0 or selected >= len(view_options):
            selected = 0
        _option, title, caption, kind, metric, segments = view_options[selected]
        if section == "models":
            selected_model = self.base_model_filter.currentText()
            selected_reasoning = self.reasoning_filter.currentText()
            if str(self.base_model_filter.currentData() or "all") != "all":
                title = f"{selected_model}: {title}"
            caption = f"{caption} | Reasoning: {selected_reasoning}"
        elif section == "compare":
            caption = f"{caption} | {len(groups)} selected series | shared date range"
        _panel, title_label, caption_label = panels[pane]
        title_label.setText(title)
        caption_label.setText(caption)
        charts[pane].set_chart(kind, groups, metric, list(segments))

    def _fill_comparison(self, groups: list[dict]) -> None:
        self.comparison.setRowCount(len(groups))
        profile_by_provider = {
            hub_core.provider_key(profile): profile for profile in self._profiles
        }
        for row_index, group in enumerate(groups):
            provider = str(group.get("provider") or "")
            profile = profile_by_provider.get(provider) or {"provider": provider}
            icon_path = str(data.provider_icon_path(profile) or "")
            model_item = QTableWidgetItem(
                QIcon(icon_path) if icon_path else QIcon(),
                str(group.get("modelName") or group.get("modelLabel") or "Model"),
            )
            reasoning_names = [
                str(variant.get("name") or "Default")
                for variant in group.get("reasoningVariants", [])
            ]
            reasoning_text = ", ".join(reasoning_names) or "Default"
            model_item.setToolTip(
                f"{group.get('modelName') or 'Model'}\n"
                f"Reasoning: {reasoning_text}\n"
                f"Source: {_friendly_work_scope(group.get('workScope'))}"
            )
            self.comparison.setItem(row_index, 0, model_item)
            self.comparison.setItem(row_index, 1, QTableWidgetItem(reasoning_text))
            input_side = (
                int(group.get("inputTokens") or 0)
                + int(group.get("cachedInputTokens") or 0)
                + int(group.get("cacheCreationTokens") or 0)
            )
            cache = int(group.get("cachedInputTokens") or 0)
            cache_text = "Not exposed" if not input_side else f"{cache * 100 / input_side:.0f}%"
            values = (
                _format_tokens(group.get("totalTokens", 0)),
                cache_text,
                f"{int(group.get('completedTasks') or 0):,}",
                f"{int(group.get('edits') or 0):,}",
                f"{int(group.get('filesChanged') or 0):,}",
                f"{int(group.get('tests') or 0):,}",
                f"{int(group.get('commands') or 0):,}",
                _format_duration(group.get("activeMs")),
                _format_points(group.get("shortBurn", 0)),
                _format_points(group.get("weeklyBurn", 0)),
            )
            for column, value in enumerate(values, 2):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.comparison.setItem(row_index, column, item)
            self.comparison.setRowHeight(row_index, 40)

    def _fill_journal_table(self, table: QTableWidget, groups: list[dict]) -> None:
        visible_keys = {str(group.get("filterKey") or "") for group in groups}
        visible_labels = {str(group.get("modelLabel") or "") for group in groups}
        rows = [
            row for row in self._view.get("journal", [])
            if str(row.get("filterKey") or "") in visible_keys
            or str(row.get("modelLabel") or "") in visible_labels
        ]
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            model_name = str(row.get("modelName") or row.get("modelLabel") or "")
            effort_name = str(row.get("reasoningEffortName") or "")
            model_text = f"{model_name} · {effort_name}" if effort_name else model_name
            result_text = str(row.get("status") or "").replace("_", " ").title()
            values = (
                row.get("day", ""), model_text, row.get("activityShape", ""),
                result_text, _format_tokens(row.get("tokens", 0)),
                _format_duration(row.get("activeMs")), str(row.get("edits", 0)),
                str(row.get("files", 0)), f"{row.get('tests', 0)} / {row.get('commands', 0)}",
            )
            for column, value in enumerate(values):
                table.setItem(row_index, column, QTableWidgetItem(str(value)))
            table.setRowHeight(row_index, 38)

    def _fill_journal(self) -> None:
        self._fill_journal_table(self.journal, self._model_variant_groups())
        self._fill_journal_table(self.productivity_journal, self._visible_groups())

    def _bottom_view_changed(self) -> None:
        journal_selected = self.bottom_view.currentData() == "journal"
        self.comparison.setVisible(not journal_selected)
        self.journal.setVisible(journal_selected)
        if journal_selected:
            self.bottom_title.setText("Recent observed work")
            self.bottom_caption.setText(
                "Numeric activity only; prompts, responses, command text, and paths stay excluded"
            )
            self._fill_journal()
        else:
            self.bottom_title.setText("Model summary")
            self.bottom_caption.setText(
                "Totals by base model; reasoning settings stay visible"
            )

    def _focus_chart(self, chart: BenchmarkChart, title: str) -> None:
        ChartFocusDialog(chart, title, self._tm, self).exec()

    def export_csv_to(self, path: str | Path) -> None:
        groups = self._groups_for_section(self._active_statistics_section)
        Path(path).write_text(productivity_density_csv({"groups": groups}), encoding="utf-8")

    def export_png_to(self, path: str | Path) -> bool:
        if self._active_statistics_section == "community":
            return self.community_chart.save_png(path)
        charts = self.section_charts.get(self._active_statistics_section) or self.charts
        return bool(charts and charts[0].save_png(path))

    def _choose_csv(self) -> None:
        path, _selected = QFileDialog.getSaveFileName(self, "Export model activity", "model-activity.csv", "CSV (*.csv)")
        if path:
            self.export_csv_to(path)
            self.activity.emit(f"Exported model activity to {Path(path).name}.")

    def _choose_png(self) -> None:
        path, _selected = QFileDialog.getSaveFileName(self, "Export chart", "model-activity.png", "PNG (*.png)")
        if path and self.export_png_to(path):
            self.activity.emit(f"Exported chart to {Path(path).name}.")

    def apply_theme(self) -> None:
        tokens = self._tm.tokens
        self.setStyleSheet(f"background:{tokens['bg']};")
        self._content.setStyleSheet(f"background:{tokens['bg']};")
        self.model_content.setStyleSheet(f"background:{tokens['bg']};")
        self.productivity_content.setStyleSheet(f"background:{tokens['bg']};")
        self.compare_content.setStyleSheet(f"background:{tokens['bg']};")
        self.community_content.setStyleSheet(f"background:{tokens['bg']};")
        self.scan_spinner.set_color(tokens["accent"])
        self.statistics_rail.setStyleSheet(
            f"QFrame#statisticsRail{{background:{tokens['panel']};"
            f"border:0;border-right:1px solid {tokens['border']};}}"
            f"QPushButton#statisticsNavButton{{background:transparent;color:{tokens['text2']};"
            f"border:0;border-left:2px solid {tokens['border']};border-radius:0;"
            "text-align:left;padding:7px 8px;font-weight:600;}"
            f"QPushButton#statisticsNavButton:hover{{background:{tokens['panel2']};color:{tokens['text']};}}"
            f"QPushButton#statisticsNavButton[active=\"true\"]{{background:transparent;"
            f"color:{tokens['text']};border-left:2px solid {tokens['accent']};}}"
        )
        self.compare_roster.setStyleSheet(
            "QFrame#compareRosterRow{background:transparent;border:0;}"
            f"QLabel#compareRole{{background:transparent;color:{tokens['text3']};border:0;}}"
            f"QLabel#compareRole[baseline=\"true\"]{{color:{tokens['accent']};}}"
        )
        self.summary_panel.setStyleSheet(
            f"QFrame#summaryStat{{background:transparent;border:0;"
            f"border-right:1px solid {tokens['border']};border-radius:0;}}"
            "QFrame#summaryStat[last=\"true\"]{border-right:0;}"
            f"QFrame#summaryDivider{{background:{tokens['border']};border:0;}}"
            f"QFrame#summaryDensity{{background:{tokens['panel2']};border:0;"
            "border-bottom-left-radius:7px;border-bottom-right-radius:7px;}"
            "QFrame#summaryDensity QLabel{background:transparent;border:0;}"
            f"QFrame#densityMetric{{background:transparent;border:0;"
            f"border-right:1px solid {tokens['border']};border-radius:0;}}"
            "QFrame#densityMetric[last=\"true\"]{border-right:0;}"
        )
        self.density.setStyleSheet(
            f"QFrame#summaryDensity{{background:{tokens['panel']};"
            f"border:1px solid {tokens['border']};border-top:2px solid {tokens['accent']};"
            "border-radius:7px;}"
            "QFrame#summaryDensity QLabel{background:transparent;border:0;}"
            f"QFrame#densityMetric{{background:transparent;border:0;"
            f"border-right:1px solid {tokens['border']};border-radius:0;}}"
            "QFrame#densityMetric[last=\"true\"]{border-right:0;}"
            f"QFrame#densityMetric[lowerRow=\"true\"]{{border-top:1px solid {tokens['border']};}}"
            f"QFrame#summaryDivider{{background:{tokens['border']};border:0;}}"
        )
        table_style = (
            f"QTableWidget{{background:{tokens['panel']};color:{tokens['text']};border:0;}}"
            f"QTableWidget::item{{border-bottom:1px solid {tokens['border']};padding:7px;font-size:11px;}}"
            f"QHeaderView::section{{background:{tokens['panel2']};color:{tokens['text3']};"
            f"border:0;border-bottom:1px solid {tokens['border']};padding:8px;font-size:11px;}}"
        )
        self.comparison.setStyleSheet(table_style)
        self.journal.setStyleSheet(table_style)
        self.productivity_journal.setStyleSheet(table_style)
        self.compare_table.setStyleSheet(table_style)
        self.community_table.setStyleSheet(table_style)
        for chart in (
            chart
            for charts in self.section_charts.values()
            for chart in charts
        ):
            chart.apply_theme()
        self.community_chart.apply_theme()

    def close_worker(self) -> None:
        self._refresh_debounce.stop()
        if self._worker is not None:
            self._scan_generation += 1
            if self._worker.isRunning():
                self._worker.requestInterruption()
                self._worker.wait(5000)
        if self._community_worker is not None and self._community_worker.isRunning():
            self._community_worker.requestInterruption()
            self._community_worker.wait(12000)


LineChart = BenchmarkChart

__all__ = [
    "AnalyticsWorker", "BenchmarkChart", "LineChart", "StatisticsScreen",
    "_chart_rows", "_format_tokens",
]
