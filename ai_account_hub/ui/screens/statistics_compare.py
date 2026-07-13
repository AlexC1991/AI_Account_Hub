"""Compare / head-to-head section of the Statistics screen (mixin)."""

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


class StatisticsCompareMixin:
    """Extracted from StatisticsScreen; mixed back in (1400-line rule)."""
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
        raw_value = item.get("value")
        raw_delta = item.get("delta")
        if raw_value is None:
            return "Not exposed", "This metric is not available for the selected model and range."
        value = float(raw_value)
        delta = float(raw_delta) if raw_delta is not None else None
        if metric in {"totalTokens", "workTokens"}:
            absolute = _format_tokens(value)
            delta_text = _format_tokens(abs(delta)) if delta is not None else "Not comparable"
        elif metric == "activeMs":
            absolute = _format_duration(value)
            delta_text = _format_duration(abs(delta)) if delta is not None else "Not comparable"
        elif metric in {"shortBurn", "weeklyBurn"}:
            absolute = _format_points(value)
            delta_text = _format_points(abs(delta)) if delta is not None else "Not comparable"
        elif metric in {"tokensPerTask", "tasksPerMillion"}:
            absolute = _format_number(value, 1)
            delta_text = _format_number(abs(delta), 1) if delta is not None else "Not comparable"
        else:
            absolute = _format_number(value)
            delta_text = _format_number(abs(delta), 1) if delta is not None else "Not comparable"
        if baseline:
            return f"{absolute}\nBaseline", f"Observed value: {absolute}\nComparison role: Baseline"
        if delta is None:
            return (
                f"{absolute}\nNot comparable",
                f"Observed value: {absolute}\nThe baseline does not expose this metric.",
            )
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
            "totalTokens", "workTokens", "completedTasks", "edits", "filesChanged", "tests",
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
