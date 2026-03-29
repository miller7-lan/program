"""
ui/history_view.py
历史记录页 — 以表格形式展示所有每日记录，支持查询和删除。

骨架说明：
  - 顶部：日期范围筛选 + 查询按钮
  - 中部：QTableWidget 展示记录（日期 / 总利润 / 原始数据摘要）
  - 底部：删除选中行 + 导出 Excel 按钮
  - 监听 event_bus.data_changed → 自动刷新表格
"""

from PySide6.QtCore import QDate, QThread, Signal
from PySide6.QtWidgets import (
    QDateEdit,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.event_bus import event_bus
from core.field_config import summarize_raw_data


class ExportWorker(QThread):
    """负责在后台执行导出操作，避免主界面卡死。"""

    finished = Signal(str)
    error = Signal(str)

    def __init__(self, db, start, end, path):
        super().__init__()
        self.db = db
        self.start_date = start
        self.end_date = end
        self.export_path = path

    def run(self):
        from core.report_engine import ReportEngine

        try:
            engine = ReportEngine(self.db)
            exported_path = engine.export_to_excel(
                self.export_path,
                self.start_date,
                self.end_date,
            )
            self.finished.emit(str(exported_path))
        except Exception as e:
            self.error.emit(str(e))


class HistoryView(QWidget):
    """历史记录视图：表格展示、筛选、删除与导出。"""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._build_ui()
        event_bus.data_changed.connect(self.refresh)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(16)

        title = QLabel("📋 历史记录")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)

        filter_row.addWidget(QLabel("从："))
        self._start_date = QDateEdit(QDate.currentDate().addMonths(-1))
        self._start_date.setDisplayFormat("yyyy-MM-dd")
        self._start_date.setCalendarPopup(True)
        self._start_date.setObjectName("DateEdit")
        filter_row.addWidget(self._start_date)

        filter_row.addWidget(QLabel("至："))
        self._end_date = QDateEdit(QDate.currentDate())
        self._end_date.setDisplayFormat("yyyy-MM-dd")
        self._end_date.setCalendarPopup(True)
        self._end_date.setObjectName("DateEdit")
        filter_row.addWidget(self._end_date)

        query_btn = QPushButton("🔍  查询")
        query_btn.setObjectName("SecondaryButton")
        query_btn.clicked.connect(self.refresh)
        filter_row.addWidget(query_btn)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        self._table = QTableWidget()
        self._table.setObjectName("HistoryTable")
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["日期", "利润（元）", "原始数据摘要"])
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

        action_row = QHBoxLayout()
        action_row.addStretch()

        del_btn = QPushButton("🗑️  删除选中")
        del_btn.setObjectName("DangerButton")
        del_btn.clicked.connect(self._on_delete)
        action_row.addWidget(del_btn)

        export_btn = QPushButton("📤  导出 Excel")
        export_btn.setObjectName("SecondaryButton")
        export_btn.clicked.connect(self._on_export)
        action_row.addWidget(export_btn)
        layout.addLayout(action_row)

    def refresh(self) -> None:
        start = self._start_date.date().toString("yyyy-MM-dd")
        end = self._end_date.date().toString("yyyy-MM-dd")
        records = self._db.get_range(start, end)

        self._table.setRowCount(len(records))
        for row_idx, rec in enumerate(records):
            self._table.setItem(row_idx, 0, QTableWidgetItem(rec["date"]))
            self._table.setItem(row_idx, 1, QTableWidgetItem(f"{rec['total_profit']:.2f}"))
            self._table.setItem(row_idx, 2, QTableWidgetItem(summarize_raw_data(rec["raw_data"])))

    def _on_delete(self) -> None:
        selected = self._table.selectedItems()
        if not selected:
            QMessageBox.information(self, "提示", "请先选中要删除的行。")
            return

        row = self._table.currentRow()
        date_item = self._table.item(row, 0)
        if not date_item:
            return

        date_str = date_item.text()
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除 {date_str} 的记录吗？此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._db.delete_record(date_str)
            event_bus.data_changed.emit()

    def _on_export(self) -> None:
        from pathlib import Path

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 Excel",
            str(Path.home() / "利润报表.xlsx"),
            "Excel 文件 (*.xlsx)",
        )
        if not save_path:
            return

        start = self._start_date.date().toString("yyyy-MM-dd")
        end = self._end_date.date().toString("yyyy-MM-dd")

        sender = self.sender()
        if isinstance(sender, QPushButton):
            sender.setEnabled(False)
            sender.setText("⌛ 导出中...")

        self._export_thread = ExportWorker(self._db, start, end, save_path)

        def on_finished(path):
            if isinstance(sender, QPushButton):
                sender.setEnabled(True)
                sender.setText("📤  导出 Excel")
            QMessageBox.information(self, "导出成功", f"数据已成功导出至：\n{path}")

        def on_error(err):
            if isinstance(sender, QPushButton):
                sender.setEnabled(True)
                sender.setText("📤  导出 Excel")
            QMessageBox.critical(self, "导出失败", f"导出过程中发生错误：\n{err}")

        self._export_thread.finished.connect(on_finished)
        self._export_thread.error.connect(on_error)
        self._export_thread.start()

    def showEvent(self, event) -> None:
        self.refresh()
        super().showEvent(event)
