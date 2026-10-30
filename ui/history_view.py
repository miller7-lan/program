"""
ui/history_view.py
历史记录页 — 以表格形式展示每日记录，支持筛选、编辑、删除和导出。

  - 顶部：快捷区间（本月 / 上月 / 近 30 天 / 今年 / 全部）、自定义日期、备注搜索
  - 汇总条：记录天数、合计、日均、最高、最低
  - 表格：日期 / 星期 / 利润 / 各一级分类汇总 / 备注，可排序；双击打开编辑
  - 底部：编辑、删除选中（支持多选）、导出 Excel
  - 监听 event_bus.data_changed → 自动刷新表格
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDate, QSignalBlocker, Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDateEdit,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.event_bus import event_bus
from core.field_config import build_formula_variables
from core.report_engine import ReportEngine, load_field_tree, weekday_label
from ui import icons, theme
from ui.widgets import Card, label, make_button, page_header, set_tone


class ExportWorker(QThread):
    """负责在后台执行导出操作，避免主界面卡死。"""

    finished_ok = Signal(str)
    error = Signal(str)

    def __init__(self, db, start, end, path):
        super().__init__()
        self.db = db
        self.start_date = start
        self.end_date = end
        self.export_path = path

    def run(self):
        try:
            engine = ReportEngine(self.db)
            exported_path = engine.export_to_excel(self.export_path, self.start_date, self.end_date)
            self.finished_ok.emit(str(exported_path))
        except Exception as e:
            self.error.emit(str(e))


class SortableItem(QTableWidgetItem):
    """按 UserRole 中的原始值排序（数字按大小、日期按先后）。"""

    def __lt__(self, other):  # noqa: D105
        mine = self.data(Qt.ItemDataRole.UserRole)
        theirs = other.data(Qt.ItemDataRole.UserRole)
        if mine is not None and theirs is not None:
            return mine < theirs
        return super().__lt__(other)


class HistoryView(QWidget):
    """历史记录视图：表格展示、筛选、删除与导出。"""

    START_DATE_CONFIG_KEY = "history_start_date"
    PRESETS = ["本月", "上月", "近 30 天", "今年", "全部"]

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._export_thread: ExportWorker | None = None
        self._records: list[dict] = []
        self._active_preset = -1
        self._build_ui()
        event_bus.data_changed.connect(self.refresh)
        event_bus.config_changed.connect(self.refresh)

    # ─────────────────────────────────────
    # UI 构建
    # ─────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        header, self._subtitle, _ = page_header("历史记录", "双击任意一行可打开编辑")
        layout.addLayout(header)

        filter_card = Card()
        filters = QVBoxLayout(filter_card)
        filters.setContentsMargins(14, 12, 14, 12)
        filters.setSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._preset_group = QButtonGroup(self)
        self._preset_group.setExclusive(False)
        for index, text in enumerate(self.PRESETS):
            chip = make_button(text, "chip")
            chip.setCheckable(True)
            self._preset_group.addButton(chip, index)
            row.addWidget(chip)
        self._preset_group.idClicked.connect(self._apply_preset)
        row.addStretch()
        filters.addLayout(row)

        # 第二行：日期范围 + 搜索（窄窗口 / 125% 缩放时也不会被挤压）
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(label("日期范围", "Hint"))
        self._start_date = QDateEdit()
        self._start_date.setDisplayFormat("yyyy-MM-dd")
        self._start_date.setCalendarPopup(True)
        self._start_date.setMinimumWidth(140)
        self._start_date.dateChanged.connect(self._on_custom_range)
        row.addWidget(self._start_date)
        row.addWidget(label("至", "Hint"))
        self._end_date = QDateEdit()
        self._end_date.setDisplayFormat("yyyy-MM-dd")
        self._end_date.setCalendarPopup(True)
        self._end_date.setMinimumWidth(140)
        self._end_date.dateChanged.connect(self._on_custom_range)
        row.addWidget(self._end_date)
        row.addStretch()

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索备注或日期")
        self._search.setClearButtonEnabled(True)
        self._search.addAction(icons.icon("search", theme.TEXT_3, 16), QLineEdit.ActionPosition.LeadingPosition)
        self._search.setMinimumWidth(200)
        self._search.setMaximumWidth(280)
        self._search.textChanged.connect(self._render_table)
        row.addWidget(self._search)
        filters.addLayout(row)
        layout.addWidget(filter_card)

        summary = QHBoxLayout()
        summary.setSpacing(12)
        self._summary_labels: dict[str, object] = {}
        for key, caption in [("days", "记录天数"), ("total", "合计利润"), ("avg", "日均利润"),
                             ("max", "最高一天"), ("min", "最低一天")]:
            card = Card()
            box = QVBoxLayout(card)
            box.setContentsMargins(14, 10, 14, 10)
            box.setSpacing(2)
            box.addWidget(label(caption, "SummaryCaption"))
            value = label("—", "SummaryValue")
            box.addWidget(value)
            self._summary_labels[key] = value
            summary.addWidget(card, 1)
        layout.addLayout(summary)

        self._table = QTableWidget()
        self._table.setObjectName("HistoryTable")
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(38)
        self._table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._table.setSortingEnabled(True)
        self._table.cellDoubleClicked.connect(self._on_double_click)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.itemSelectionChanged.connect(self._update_actions)
        layout.addWidget(self._table, 1)

        action_row = QHBoxLayout()
        self._selection_label = label("", "Hint")
        action_row.addWidget(self._selection_label)
        action_row.addStretch()
        self._edit_btn = make_button("编辑", "", "edit")
        self._edit_btn.clicked.connect(self._edit_selected)
        action_row.addWidget(self._edit_btn)
        self._delete_btn = make_button("删除选中", "danger", "trash")
        self._delete_btn.clicked.connect(self._on_delete)
        action_row.addWidget(self._delete_btn)
        action_row.addSpacing(8)
        self._export_btn = make_button("导出 Excel", "primary", "sheet")
        self._export_btn.setToolTip("导出当前日期范围内的记录")
        self._export_btn.clicked.connect(self._on_export)
        action_row.addWidget(self._export_btn)
        layout.addLayout(action_row)

        self._load_filter_defaults()

    # ─────────────────────────────────────
    # 筛选
    # ─────────────────────────────────────

    def _apply_preset(self, index: int) -> None:
        today = QDate.currentDate()
        if index == 0:
            start, end = QDate(today.year(), today.month(), 1), today
        elif index == 1:
            first = QDate(today.year(), today.month(), 1).addMonths(-1)
            start, end = first, first.addMonths(1).addDays(-1)
        elif index == 2:
            start, end = today.addDays(-29), today
        elif index == 3:
            start, end = QDate(today.year(), 1, 1), today
        else:
            first, last = self._db.get_date_bounds()
            start = QDate.fromString(first, "yyyy-MM-dd") if first else today
            last_date = QDate.fromString(last, "yyyy-MM-dd") if last else today
            end = max(today, last_date)
        with QSignalBlocker(self._start_date), QSignalBlocker(self._end_date):
            self._start_date.setDate(start)
            self._end_date.setDate(end)
        self._check_preset(index)
        self._remember_start_date()
        self.refresh()

    def _check_preset(self, index: int) -> None:
        self._active_preset = index
        for button in self._preset_group.buttons():
            button.setChecked(self._preset_group.id(button) == index)

    def _on_custom_range(self) -> None:
        if self._end_date.date() < self._start_date.date():
            with QSignalBlocker(self._end_date):
                self._end_date.setDate(self._start_date.date())
        self._check_preset(-1)
        self._remember_start_date()
        self.refresh()

    def _load_filter_defaults(self) -> None:
        saved_start = self._db.get_config(
            self.START_DATE_CONFIG_KEY,
            QDate.currentDate().addMonths(-1).toString("yyyy-MM-dd"),
        )
        start_date = QDate.fromString(saved_start, "yyyy-MM-dd")
        if not start_date.isValid():
            start_date = QDate.currentDate().addMonths(-1)
        with QSignalBlocker(self._start_date), QSignalBlocker(self._end_date):
            self._start_date.setDate(start_date)
            self._end_date.setDate(QDate.currentDate())

    def _remember_start_date(self) -> None:
        self._db.set_config(self.START_DATE_CONFIG_KEY, self._start_date.date().toString("yyyy-MM-dd"))

    # ─────────────────────────────────────
    # 数据刷新
    # ─────────────────────────────────────

    def refresh(self) -> None:
        start = self._start_date.date().toString("yyyy-MM-dd")
        end = self._end_date.date().toString("yyyy-MM-dd")
        self._records = self._db.get_range(start, end)
        self._render_table()

    def _render_table(self) -> None:
        keyword = self._search.text().strip()
        records = [
            r for r in self._records
            if not keyword or keyword in r.get("note", "") or keyword in r["date"]
        ]
        field_tree = load_field_tree(self._db)
        parents = [item["name"] for item in field_tree]
        headers = ["日期", "星期", "利润（元）"] + [f"{p}（元）" for p in parents] + ["备注"]

        self._table.setSortingEnabled(False)
        self._table.clear()
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setRowCount(len(records))
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        danger = QColor(theme.DANGER)

        for row_idx, rec in enumerate(reversed(records)):
            date_item = SortableItem(rec["date"])
            date_item.setData(Qt.ItemDataRole.UserRole, rec["date"])
            self._table.setItem(row_idx, 0, date_item)
            self._table.setItem(row_idx, 1, QTableWidgetItem(weekday_label(rec["date"])))

            profit = float(rec["total_profit"])
            profit_item = SortableItem(f"{profit:,.2f}")
            profit_item.setData(Qt.ItemDataRole.UserRole, profit)
            profit_item.setTextAlignment(right)
            font = profit_item.font()
            font.setBold(True)
            profit_item.setFont(font)
            if profit < 0:
                profit_item.setForeground(danger)
            self._table.setItem(row_idx, 2, profit_item)

            totals = build_formula_variables(field_tree, rec["raw_data"])
            for offset, parent in enumerate(parents):
                amount = totals.get(parent, 0.0)
                item = SortableItem(f"{amount:,.2f}" if amount else "—")
                item.setData(Qt.ItemDataRole.UserRole, amount)
                item.setTextAlignment(right)
                details = rec["raw_data"].get(parent)
                if isinstance(details, dict):
                    lines = [f"{name}：{value:,.2f}" for name, value in details.items() if value]
                    item.setToolTip(f"{parent}明细\n" + "\n".join(lines) if lines else f"{parent}：无明细")
                if not amount:
                    item.setForeground(QColor(theme.TEXT_3))
                self._table.setItem(row_idx, 3 + offset, item)

            note = rec.get("note", "").replace("\n", " ")
            note_item = QTableWidgetItem(note)
            note_item.setToolTip(rec.get("note", ""))
            self._table.setItem(row_idx, len(headers) - 1, note_item)

        header = self._table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for column in range(2, len(headers) - 1):
            header_item = self._table.horizontalHeaderItem(column)
            if header_item is not None:
                header_item.setTextAlignment(right)
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setMinimumSectionSize(64)
        self._table.setColumnWidth(0, 118)
        self._table.setColumnWidth(1, 64)
        self._table.setColumnWidth(2, 122)
        for offset in range(len(parents)):
            self._table.setColumnWidth(3 + offset, 120)
        self._table.setSortingEnabled(True)
        self._table.sortByColumn(0, Qt.SortOrder.DescendingOrder)

        summary = ReportEngine.range_summary(records)
        self._set_summary("days", f"{summary['days']} 天", 0)
        self._set_summary("total", theme.fmt_money(summary["total"]), summary["total"])
        self._set_summary("avg", theme.fmt_money(summary["avg"]), summary["avg"])
        self._set_summary("max", theme.fmt_money(summary["max"]) if records else "—", summary["max"])
        self._set_summary("min", theme.fmt_money(summary["min"]) if records else "—", summary["min"])
        span = (self._end_date.date().toJulianDay() - self._start_date.date().toJulianDay()) + 1
        self._subtitle.setText(f"所选区间共 {span} 天，其中 {summary['days']} 天有记录 · 双击任意一行可打开编辑")
        self._update_actions()

    def _set_summary(self, key: str, text: str, value: float) -> None:
        widget = self._summary_labels[key]
        widget.setText(text)
        set_tone(widget, "negative" if value < 0 else "")

    # ─────────────────────────────────────
    # 操作
    # ─────────────────────────────────────

    def _selected_dates(self) -> list[str]:
        rows = sorted({index.row() for index in self._table.selectionModel().selectedRows()})
        dates = []
        for row in rows:
            item = self._table.item(row, 0)
            if item is not None:
                dates.append(item.text())
        return dates

    def _update_actions(self) -> None:
        count = len(self._selected_dates())
        self._delete_btn.setEnabled(count > 0)
        self._edit_btn.setEnabled(count == 1)
        self._selection_label.setText(f"已选中 {count} 条" if count else "按住 Ctrl 或 Shift 可多选")

    def _on_double_click(self, row: int, _column: int) -> None:
        item = self._table.item(row, 0)
        if item is not None:
            event_bus.edit_date_requested.emit(item.text())

    def _edit_selected(self) -> None:
        dates = self._selected_dates()
        if len(dates) == 1:
            event_bus.edit_date_requested.emit(dates[0])

    def _on_context_menu(self, pos) -> None:
        if self._table.itemAt(pos) is None:
            return
        menu = QMenu(self)
        edit_action = menu.addAction(icons.icon("edit", theme.TEXT_2, 16), "编辑这一天")
        delete_action = menu.addAction(icons.icon("trash", theme.DANGER, 16), "删除选中的记录")
        edit_action.setEnabled(len(self._selected_dates()) == 1)
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is edit_action:
            self._edit_selected()
        elif chosen is delete_action:
            self._on_delete()

    def _on_delete(self) -> None:
        dates = self._selected_dates()
        if not dates:
            QMessageBox.information(self, "提示", "请先选中要删除的行。")
            return
        preview = "、".join(dates[:5]) + (f" 等 {len(dates)} 天" if len(dates) > 5 else "")
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除 {preview} 的记录吗？\n开启云同步时，其他设备上的这些记录也会被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            deleted = self._db.delete_records(dates)
            event_bus.data_changed.emit()
            event_bus.toast_requested.emit(f"已删除 {deleted} 条记录", "info")

    def _on_export(self) -> None:
        start = self._start_date.date().toString("yyyy-MM-dd")
        end = self._end_date.date().toString("yyyy-MM-dd")
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 Excel",
            str(Path.home() / f"利润报表_{start}_{end}.xlsx"),
            "Excel 文件 (*.xlsx)",
        )
        if not save_path:
            return

        self._export_btn.setEnabled(False)
        self._export_btn.setText("导出中…")
        self._export_thread = ExportWorker(self._db, start, end, save_path)

        def on_finished(path):
            self._export_btn.setEnabled(True)
            self._export_btn.setText("导出 Excel")
            event_bus.toast_requested.emit(f"已导出：{Path(path).name}", "success")

        def on_error(err):
            self._export_btn.setEnabled(True)
            self._export_btn.setText("导出 Excel")
            QMessageBox.critical(self, "导出失败", f"导出过程中发生错误：\n{err}")

        self._export_thread.finished_ok.connect(on_finished)
        self._export_thread.error.connect(on_error)
        self._export_thread.start()

    def sync_to_today(self) -> None:
        """快捷区间随日期变化（例如跨月后的“本月”）；自定义区间至少延伸到今天。"""
        if self._active_preset >= 0:
            self._apply_preset(self._active_preset)
        else:
            with QSignalBlocker(self._end_date):
                self._end_date.setDate(max(self._end_date.date(), QDate.currentDate()))
            self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.sync_to_today()
        super().showEvent(event)

