"""Responsive local-data report and safe Hub-cache cleanup dialog."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ai_account_hub.core.storage import (
    HISTORY_RETENTION_DAYS,
    cleanup_managed_storage,
    format_bytes,
    storage_report,
)
from ai_account_hub.ui.widgets import Spinner, make_button


class StorageWorker(QThread):
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, cleanup: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._cleanup = cleanup

    def run(self) -> None:
        try:
            result = cleanup_managed_storage() if self._cleanup else storage_report()
            self.completed.emit(result)
        except Exception as error:
            self.failed.emit(str(error))


class LocalDataDialog(QDialog):
    """Show ownership boundaries and clean only disposable Hub-managed data."""

    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self._tm = theme
        self._worker: StorageWorker | None = None
        self._report: dict = {}
        self.setObjectName("localDataDialog")
        self.setWindowTitle("Local data")
        self.resize(820, 590)
        self.setMinimumSize(680, 500)
        self._build()
        self.apply_theme()
        self._tm.changed.connect(lambda _name: self.apply_theme())
        self.refresh_report()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        heading = QLabel("Local data")
        heading.setObjectName("dialogTitle")
        caption = QLabel(
            "See what AI Account Hub manages and what remains owned by the official provider apps."
        )
        caption.setObjectName("muted")
        caption.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(caption)

        self.disk_card = QFrame()
        self.disk_card.setObjectName("storageSummary")
        disk_layout = QHBoxLayout(self.disk_card)
        disk_layout.setContentsMargins(12, 10, 12, 10)
        disk_copy = QVBoxLayout()
        disk_copy.setSpacing(2)
        disk_copy.addWidget(QLabel("System drive free space"))
        self.disk_value = QLabel("Scanning...")
        self.disk_value.setObjectName("storageValue")
        self.disk_note = QLabel("The report runs without reading account content.")
        self.disk_note.setObjectName("faint")
        disk_copy.addWidget(self.disk_value)
        disk_copy.addWidget(self.disk_note)
        disk_layout.addLayout(disk_copy)
        disk_layout.addStretch(1)
        self.spinner = Spinner(self._tm.tokens["accent"], 18)
        self.spinner.setFixedSize(18, 18)
        disk_layout.addWidget(self.spinner)
        layout.addWidget(self.disk_card)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("Data", "Owner", "Size", "Location"))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        boundary = QLabel(
            f"Safe cleanup keeps {HISTORY_RETENTION_DAYS} days of numeric analytics. "
            "It removes old database rows and disposable browser caches only. Profiles, "
            "cookies, saved logins, desktop states, and official Codex/Claude history are preserved."
        )
        boundary.setObjectName("faint")
        boundary.setWordWrap(True)
        layout.addWidget(boundary)

        actions = QHBoxLayout()
        self.open_button = make_button("Open selected location", "ghost")
        self.open_button.clicked.connect(self._open_selected_location)
        self.refresh_button = make_button("Refresh", "ghost")
        self.refresh_button.clicked.connect(self.refresh_report)
        self.cleanup_button = make_button("Clean managed caches", "primary")
        self.cleanup_button.clicked.connect(self._confirm_cleanup)
        close_button = make_button("Close", "ghost")
        close_button.clicked.connect(self.accept)
        actions.addWidget(self.open_button)
        actions.addStretch(1)
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.cleanup_button)
        actions.addWidget(close_button)
        layout.addLayout(actions)

    def refresh_report(self) -> None:
        self._start_worker(False)

    def _confirm_cleanup(self) -> None:
        answer = QMessageBox.question(
            self,
            "Clean managed caches",
            "Remove disposable isolated-browser caches and analytics rows older "
            f"than {HISTORY_RETENTION_DAYS} days? Saved accounts and provider history are not removed.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer == QMessageBox.Yes:
            self._start_worker(True)

    def _start_worker(self, cleanup: bool) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.refresh_button.setEnabled(False)
        self.cleanup_button.setEnabled(False)
        self.spinner.start()
        self.disk_note.setText("Cleaning Hub-managed data..." if cleanup else "Measuring local storage...")
        worker = StorageWorker(cleanup, self)
        worker.completed.connect(self._worker_completed)
        worker.failed.connect(self._worker_failed)
        worker.finished.connect(self._worker_finished)
        self._worker = worker
        worker.start()

    def _worker_completed(self, result: dict) -> None:
        if "report" in result:
            self._apply_report(result["report"])
            freed = format_bytes(int(result.get("freedBytes") or 0))
            self.disk_note.setText(
                f"Cleanup complete: {freed} released and "
                f"{int(result.get('removedRows') or 0):,} old rows removed."
            )
        else:
            self._apply_report(result)

    def _worker_failed(self, message: str) -> None:
        self.disk_note.setText(f"Storage report unavailable: {message}")

    def _worker_finished(self) -> None:
        self.spinner.stop()
        self.refresh_button.setEnabled(True)
        self.cleanup_button.setEnabled(True)
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()

    def _apply_report(self, report: dict) -> None:
        self._report = dict(report)
        disk = report.get("disk") or {}
        free = int(disk.get("free") or 0)
        total = int(disk.get("total") or 0)
        self.disk_value.setText(f"{format_bytes(free)} free of {format_bytes(total)}")
        if free and free < 2 * 1024**3:
            self.disk_note.setText(
                f"Low free space. Hub-managed: {format_bytes(report.get('managedBytes', 0))}; "
                f"listed provider locations: {format_bytes(report.get('providerBytes', 0))}."
            )
            self.disk_value.setProperty("low", True)
        else:
            self.disk_note.setText("Storage ownership and cleanup boundaries are healthy.")
            self.disk_value.setProperty("low", False)
        self.disk_value.style().unpolish(self.disk_value)
        self.disk_value.style().polish(self.disk_value)

        root = str(report.get("root") or "")
        rows = [
            ("Analytics database", "AI Account Hub", report.get("databaseBytes", 0), root),
            ("Isolated browser profiles", "AI Account Hub", report.get("browserBytes", 0), root),
            ("Disposable browser cache (included above)", "AI Account Hub", report.get("browserCacheBytes", 0), root),
            ("Saved desktop states", "AI Account Hub", report.get("desktopStateBytes", 0), root),
            ("Other Hub settings", "AI Account Hub", report.get("otherManagedBytes", 0), root),
        ]
        rows.extend(
            (item.get("label", "Provider data"), "Official provider", item.get("bytes", 0), item.get("path", ""))
            for item in report.get("providers", [])
        )
        self.table.setRowCount(len(rows))
        for row_index, (label, owner, size, path) in enumerate(rows):
            values = (label, owner, format_bytes(int(size or 0)), path)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(path))
                if column == 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row_index, column, item)
            self.table.setRowHeight(row_index, 36)
        if rows:
            self.table.setCurrentCell(0, 0)

    def _open_selected_location(self) -> None:
        selected = self.table.currentRow()
        item = self.table.item(selected, 3) if selected >= 0 else None
        path = str(item.text() if item is not None else self._report.get("root") or "")
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def apply_theme(self) -> None:
        tokens = self._tm.tokens
        self.spinner.set_color(tokens["accent"])
        self.setStyleSheet(
            f"QDialog#localDataDialog{{background:{tokens['bg']};color:{tokens['text']};}}"
            f"QLabel#dialogTitle{{font-size:20px;font-weight:650;color:{tokens['text']};}}"
            f"QLabel#muted{{color:{tokens['text2']};}} QLabel#faint{{color:{tokens['text3']};}}"
            f"QFrame#storageSummary{{background:{tokens['panel2']};border:1px solid {tokens['border']};border-radius:7px;}}"
            f"QLabel#storageValue{{font-size:18px;font-weight:650;color:{tokens['text']};}}"
            f"QLabel#storageValue[low='true']{{color:{tokens['danger']};}}"
            f"QTableWidget{{background:{tokens['panel']};color:{tokens['text']};border:1px solid {tokens['border']};border-radius:7px;}}"
            f"QTableWidget::item{{border-bottom:1px solid {tokens['border']};padding:6px;}}"
            f"QHeaderView::section{{background:{tokens['panel2']};color:{tokens['text3']};border:0;border-bottom:1px solid {tokens['border']};padding:7px;}}"
        )

    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            if not self._worker.wait(5000):
                self.disk_note.setText("Finishing the current storage operation before closing...")
                event.ignore()
                return
        super().closeEvent(event)


__all__ = ["LocalDataDialog", "StorageWorker"]
