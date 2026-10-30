"""
ui/entry_view.py
录入页 — 用户录入当日各项账目数据并保存。

  - 顶部固定：日期切换（前一天 / 后一天 / 今天）、记录状态
  - 中间滚动：每个一级分类一张卡片，二级分类按窗口宽度自动排成多列，并显示小计；最后是备注
  - 底部固定：按公式实时计算的当日利润、撤销修改、保存记录
  - 金额框支持直接输入算式（如 120+35），回车跳到下一项，Ctrl+S 保存；格式错误时弹窗提醒
"""

from __future__ import annotations

import re

from PySide6.QtCore import QDate, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDateEdit,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.database import NOTE_MAX_CHARS
from core.event_bus import event_bus
from core.field_config import build_formula_variables, load_field_tree_from_json, merge_entry_raw
from core.formula_engine import FormulaEngine, FormulaError, normalize_formula
from ui import theme
from ui.widgets import Badge, Card, label, make_button, page_header, set_tone

_LEADING_ZEROS = re.compile(r"(?<![\d.])0+(?=\d)")
_ENGINE = FormulaEngine()


def evaluate_amount(text: str) -> float:
    """把录入框里的文本（数字或简单算式）转换为金额，非法时抛出 FormulaError。"""
    normalized = normalize_formula(text).replace(",", "").strip()
    if not normalized:
        return 0.0
    return _ENGINE.evaluate(_LEADING_ZEROS.sub("", normalized), {})


class AmountInput(QLineEdit):
    """金额输入框：右对齐，实时校验，失去焦点时把算式换算成结果。"""

    value_changed = Signal()
    submitted = Signal()

    def __init__(self, display_name: str):
        super().__init__()
        self.setObjectName("AmountInput")
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setPlaceholderText("0")
        self.setMinimumWidth(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.display_name = display_name
        self.textChanged.connect(self._on_text_changed)
        self.editingFinished.connect(self._collapse_expression)
        self.returnPressed.connect(self.submitted.emit)

    def value(self) -> float | None:
        try:
            return evaluate_amount(self.text())
        except FormulaError:
            return None

    def _on_text_changed(self) -> None:
        invalid = self.value() is None
        if bool(self.property("invalid")) != invalid:
            self.setProperty("invalid", invalid)
            theme.repolish(self)
        self.setToolTip(f"「{self.display_name}」只能输入数字或算式，例如 120+35" if invalid else "")
        self.value_changed.emit()

    def _collapse_expression(self) -> None:
        text = self.text().strip()
        result = self.value()
        if result is None or not text or re.fullmatch(r"-?\d+(\.\d+)?", text):
            return
        formatted = theme.fmt_amount_input(result) or "0"
        if formatted != text:
            self.setToolTip(f"{text} = {formatted}")
            with QSignalBlocker(self):
                self.setText(formatted)
            self.value_changed.emit()


class EntryView(QWidget):
    """录入视图：支持分级字段的每日账目录入界面。"""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._field_tree: list[dict] = []
        self._field_inputs: dict[str, AmountInput] = {}
        self._input_order: list[AmountInput] = []
        self._category_totals: dict[str, object] = {}
        self._child_grids: list[tuple[QGridLayout, list[QWidget]]] = []
        self._grid_columns = 0
        self._snapshot: tuple | None = None
        self._loaded_record: dict | None = None
        self._loaded_as_today = True
        self._has_record = False
        self._current_date = QDate.currentDate()

        self._build_ui()
        self._rebuild_fields()

        QShortcut(QKeySequence.StandardKey.Save, self, activated=self._on_save)
        QShortcut(QKeySequence("Alt+Left"), self, activated=lambda: self._shift_day(-1))
        QShortcut(QKeySequence("Alt+Right"), self, activated=lambda: self._shift_day(1))
        event_bus.config_changed.connect(self._on_config_changed)
        event_bus.data_changed.connect(self._on_data_changed)

    # ─────────────────────────────────────
    # UI 构建
    # ─────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QWidget()
        outer.addWidget(body, 1)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 0)
        layout.setSpacing(14)

        header, _, _ = page_header("录入", "支持直接输入算式（如 120+35）· Enter 跳到下一项 · Ctrl+S 保存")
        layout.addLayout(header)

        toolbar = Card()
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(12, 10, 12, 10)
        bar.setSpacing(8)

        prev_btn = make_button("", "icon", "chevron-left", tooltip="前一天（Alt+←）")
        prev_btn.clicked.connect(lambda: self._shift_day(-1))
        bar.addWidget(prev_btn)

        self._date_edit = QDateEdit(self._current_date)
        self._date_edit.setDisplayFormat("yyyy-MM-dd  dddd")
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setMinimumWidth(190)
        self._date_edit.dateChanged.connect(self._on_date_edited)
        bar.addWidget(self._date_edit)

        next_btn = make_button("", "icon", "chevron-right", tooltip="后一天（Alt+→）")
        next_btn.clicked.connect(lambda: self._shift_day(1))
        bar.addWidget(next_btn)

        today_btn = make_button("今天", "ghost", "today")
        today_btn.clicked.connect(lambda: self._request_date(QDate.currentDate()))
        bar.addWidget(today_btn)

        bar.addSpacing(6)
        self._record_badge = Badge()
        bar.addWidget(self._record_badge)
        self._dirty_badge = Badge()
        bar.addWidget(self._dirty_badge)
        bar.addStretch()
        layout.addWidget(toolbar)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("PageScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._fields_container = QWidget()
        self._fields_container.setObjectName("PageContent")
        self._fields_layout = QVBoxLayout(self._fields_container)
        self._fields_layout.setContentsMargins(0, 0, 0, 24)
        self._fields_layout.setSpacing(14)
        self._scroll.setWidget(self._fields_container)
        layout.addWidget(self._scroll, 1)

        note_card = Card()
        note_layout = QVBoxLayout(note_card)
        note_layout.setContentsMargins(18, 14, 18, 16)
        note_layout.setSpacing(8)
        note_head = QHBoxLayout()
        note_head.addWidget(label("备注", "CategoryTitle"))
        note_head.addStretch()
        note_head.addWidget(label("会同步到历史表格和 Excel", "CategoryMeta"))
        note_layout.addLayout(note_head)
        self._note_input = QPlainTextEdit()
        self._note_input.setPlaceholderText("例如：租金已支付、活动日营业额偏高")
        self._note_input.setFixedHeight(76)
        self._note_input.textChanged.connect(self._update_state)
        note_layout.addWidget(self._note_input)
        self._note_card = note_card

        # 底部固定操作栏：当日利润 + 撤销 / 保存
        action_bar = Card(object_name="ActionBar")
        actions = QHBoxLayout(action_bar)
        actions.setContentsMargins(28, 10, 28, 10)
        actions.setSpacing(10)
        profit_box = QVBoxLayout()
        profit_box.setSpacing(0)
        profit_box.addWidget(label("当日利润（按公式计算）", "ProfitCaption"))
        self._profit_value = label("¥0.00", "ProfitValue")
        profit_box.addWidget(self._profit_value)
        actions.addLayout(profit_box)
        actions.addSpacing(16)
        self._error_label = label("", "ErrorText", wrap=True)
        actions.addWidget(self._error_label, 1)

        self._revert_btn = make_button("撤销修改", "ghost")
        self._revert_btn.clicked.connect(self._load_current_date)
        actions.addWidget(self._revert_btn)

        self._save_btn = make_button("保存记录", "primary", "save")
        self._save_btn.setMinimumWidth(116)
        self._save_btn.setToolTip("保存（Ctrl+S）")
        self._save_btn.clicked.connect(self._on_save)
        actions.addWidget(self._save_btn)
        outer.addWidget(action_bar)

    def _rebuild_fields(self) -> None:
        while self._fields_layout.count():
            item = self._fields_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self._note_card:
                widget.deleteLater()

        self._field_inputs.clear()
        self._input_order.clear()
        self._category_totals.clear()
        self._child_grids.clear()
        self._grid_columns = 0

        field_tree_json = self._db.get_config("custom_field_tree", "")
        if not field_tree_json:
            field_tree_json = self._db.get_config("custom_fields", '["营业额", "成本"]')
        self._field_tree = load_field_tree_from_json(field_tree_json)

        simple_row: QHBoxLayout | None = None
        for item in self._field_tree:
            parent_name = item["name"]
            children = item["children"]
            card = Card(object_name="CategoryCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(18, 14, 18, 16)
            card_layout.setSpacing(12)

            head = QHBoxLayout()
            head.setSpacing(8)
            dot = label("")
            dot.setFixedSize(8, 8)
            dot.setStyleSheet(f"background: {theme.ACCENT}; border-radius: 4px;")
            head.addWidget(dot)
            head.addWidget(label(parent_name, "CategoryTitle"))
            head.addWidget(label(f"{len(children)} 项" if children else "", "CategoryMeta"))
            head.addStretch()
            total_label = label("", "CategoryTotal")
            self._category_totals[parent_name] = total_label
            head.addWidget(total_label)
            card_layout.addLayout(head)

            if children:
                grid = QGridLayout()
                grid.setHorizontalSpacing(14)
                grid.setVerticalSpacing(10)
                cells = []
                for child_name in children:
                    cell = QWidget()
                    cell_layout = QVBoxLayout(cell)
                    cell_layout.setContentsMargins(0, 0, 0, 0)
                    cell_layout.setSpacing(4)
                    cell_layout.addWidget(label(child_name, "FieldLabel"))
                    line_edit = self._make_input(f"{parent_name}::{child_name}", f"{parent_name} / {child_name}")
                    cell_layout.addWidget(line_edit)
                    cells.append(cell)
                self._child_grids.append((grid, cells))
                card_layout.addLayout(grid)
                self._fields_layout.addWidget(card)
                simple_row = None
            else:
                total_label.hide()
                line_edit = self._make_input(parent_name, parent_name)
                card_layout.addWidget(line_edit)
                card.setMinimumWidth(240)
                card.setMaximumWidth(360)
                if simple_row is None or simple_row.count() >= 3:
                    simple_row = QHBoxLayout()
                    simple_row.setContentsMargins(0, 0, 0, 0)
                    simple_row.setSpacing(14)
                    holder = QWidget()
                    holder.setLayout(simple_row)
                    self._fields_layout.addWidget(holder)
                simple_row.addWidget(card)
                continue

        # 让同一行的简单分类卡片靠左排列
        for index in range(self._fields_layout.count()):
            widget = self._fields_layout.itemAt(index).widget()
            if widget is not None and isinstance(widget.layout(), QHBoxLayout):
                widget.layout().addStretch()

        self._fields_layout.addWidget(self._note_card)
        self._fields_layout.addStretch()
        self._relayout_grids()
        self._load_current_date()

    def _make_input(self, key: str, display_name: str) -> AmountInput:
        line_edit = AmountInput(display_name)
        line_edit.value_changed.connect(self._update_state)
        line_edit.submitted.connect(lambda le=line_edit: self._focus_next(le))
        self._field_inputs[key] = line_edit
        self._input_order.append(line_edit)
        return line_edit

    def _relayout_grids(self) -> None:
        width = self._scroll.viewport().width() - 36
        columns = max(1, min(6, width // 190))
        if columns == self._grid_columns:
            return
        self._grid_columns = columns
        for grid, cells in self._child_grids:
            for cell in cells:
                grid.removeWidget(cell)
            for index, cell in enumerate(cells):
                grid.addWidget(cell, index // columns, index % columns)
            for column in range(6):
                grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._relayout_grids()

    def _focus_next(self, current: AmountInput) -> None:
        index = self._input_order.index(current)
        if index + 1 < len(self._input_order):
            target = self._input_order[index + 1]
        else:
            target = self._note_input
        target.setFocus()
        if isinstance(target, QLineEdit):
            target.selectAll()
        self._scroll.ensureWidgetVisible(target, 40, 60)

    # ─────────────────────────────────────
    # 计算与状态
    # ─────────────────────────────────────

    def _collect(self) -> tuple[dict, AmountInput | None]:
        """读取所有输入框，返回 (raw_data, 第一个非法输入框)。"""
        raw_data: dict[str, float | dict[str, float]] = {}
        first_invalid = None
        values: dict[str, float] = {}
        for key, line_edit in self._field_inputs.items():
            value = line_edit.value()
            if value is None:
                first_invalid = first_invalid or line_edit
                value = 0.0
            values[key] = value
        for item in self._field_tree:
            parent_name = item["name"]
            children = item["children"]
            if children:
                raw_data[parent_name] = {
                    child_name: values.get(f"{parent_name}::{child_name}", 0.0)
                    for child_name in children
                }
            else:
                raw_data[parent_name] = values.get(parent_name, 0.0)
        return raw_data, first_invalid

    def _compute_profit(self, raw_data: dict) -> tuple[float | None, str]:
        formula = self._db.get_config("profit_formula", "营业额 - 成本")
        try:
            return _ENGINE.evaluate(formula, build_formula_variables(self._field_tree, raw_data)), ""
        except FormulaError as exc:
            return None, str(exc)

    def _state_snapshot(self) -> tuple:
        values = []
        for line_edit in self._input_order:
            value = line_edit.value()
            values.append(line_edit.text().strip() if value is None else round(value, 6))
        return tuple(values), self._note_input.toPlainText().strip()

    def has_unsaved_changes(self) -> bool:
        return self._snapshot is not None and self._state_snapshot() != self._snapshot

    def _update_state(self) -> None:
        raw_data, first_invalid = self._collect()
        variables = build_formula_variables(self._field_tree, raw_data)
        for parent_name, total_label in self._category_totals.items():
            total = variables.get(parent_name, 0.0)
            total_label.setText(f"小计 {theme.fmt_money(total)}")
            set_tone(total_label, "negative" if total < 0 else "")

        profit, error = self._compute_profit(raw_data)
        if first_invalid is not None:
            self._error_label.setText(f"「{first_invalid.display_name}」格式不正确，请输入数字或算式")
            self._error_label.setToolTip("")
        elif error:
            self._error_label.setText("利润公式格式错误，请到设置页检查")
            self._error_label.setToolTip(error)
        else:
            self._error_label.setText("")
            self._error_label.setToolTip("")
        if profit is None:
            self._profit_value.setText("公式有误")
            set_tone(self._profit_value, "error")
        else:
            self._profit_value.setText(theme.fmt_money(profit))
            set_tone(self._profit_value, "negative" if profit < 0 else "")

        dirty = self.has_unsaved_changes()
        self._dirty_badge.set("未保存" if dirty else "", "warning")
        self._revert_btn.setVisible(dirty)

    # ─────────────────────────────────────
    # 日期切换
    # ─────────────────────────────────────

    def _shift_day(self, days: int) -> None:
        self._request_date(self._current_date.addDays(days))

    def _on_date_edited(self, date: QDate) -> None:
        if date != self._current_date:
            self._request_date(date)

    def open_date(self, date_text: str) -> None:
        date = QDate.fromString(date_text, "yyyy-MM-dd")
        if date.isValid():
            self._request_date(date)

    def _request_date(self, date: QDate) -> None:
        if date == self._current_date:
            with QSignalBlocker(self._date_edit):
                self._date_edit.setDate(date)
            return
        if not self.confirm_leave():
            with QSignalBlocker(self._date_edit):
                self._date_edit.setDate(self._current_date)
            return
        self._current_date = date
        with QSignalBlocker(self._date_edit):
            self._date_edit.setDate(date)
        self._load_current_date()

    def confirm_leave(self) -> bool:
        """有未保存的修改时询问用户。返回 True 表示可以离开当前日期 / 页面。"""
        if not self.has_unsaved_changes():
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("尚未保存")
        box.setText(f"{self._current_date.toString('yyyy-MM-dd')} 的修改还没有保存。")
        save = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("不保存", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return self._on_save()
        if clicked is discard:
            self._load_current_date()
            return True
        return False

    def _load_current_date(self) -> None:
        date_str = self._current_date.toString("yyyy-MM-dd")
        record = self._db.get_record(date_str)
        self._loaded_record = record
        self._loaded_as_today = self._current_date == QDate.currentDate()
        self._has_record = record is not None
        raw_data = record["raw_data"] if record else {}
        for field_name, line_edit in self._field_inputs.items():
            if "::" in field_name:
                parent_name, child_name = field_name.split("::", 1)
                parent_value = raw_data.get(parent_name, {})
                value = parent_value.get(child_name, "") if isinstance(parent_value, dict) else ""
            else:
                value = raw_data.get(field_name, "")
                if isinstance(value, dict):
                    value = sum(v for v in value.values() if isinstance(v, (int, float)))
            with QSignalBlocker(line_edit):
                line_edit.setText(theme.fmt_amount_input(value))
                line_edit.setProperty("invalid", False)
                theme.repolish(line_edit)
        with QSignalBlocker(self._note_input):
            self._note_input.setPlainText(record.get("note", "") if record else "")

        if self._has_record:
            self._record_badge.set("已录入", "success")
        elif self._current_date > QDate.currentDate():
            self._record_badge.set("未来日期", "warning")
        else:
            self._record_badge.set("新记录", "neutral")
        self._snapshot = self._state_snapshot()
        self._update_state()

    # ─────────────────────────────────────
    # 保存
    # ─────────────────────────────────────

    def _on_save(self) -> bool:
        raw_data, first_invalid = self._collect()
        if first_invalid is not None:
            self._scroll.ensureWidgetVisible(first_invalid, 40, 60)
            QMessageBox.warning(
                self,
                "金额格式错误",
                f"「{first_invalid.display_name}」填写的“{first_invalid.text().strip()}”不是有效的金额。\n\n"
                "请输入数字或算式，例如：120、85.5、120+35、200-12.5",
            )
            first_invalid.setFocus()
            first_invalid.selectAll()
            return False

        total_profit, error = self._compute_profit(raw_data)
        if total_profit is None:
            QMessageBox.warning(self, "公式格式错误", f"利润公式无法计算：{error}\n\n请到“设置 → 利润公式”中修改。")
            return False

        date_str = self._current_date.toString("yyyy-MM-dd")
        note_text = self._note_input.toPlainText().strip()
        if len(note_text) > NOTE_MAX_CHARS:
            QMessageBox.warning(self, "备注太长", f"备注最多 {NOTE_MAX_CHARS} 个字，当前 {len(note_text)} 个字，请精简后再保存。")
            return False
        if not self._confirm_overwrite_if_changed(date_str):
            return False
        # 保留这一天里当前分类以外的数据（例如删掉的分类、其他设备新增的分类），不因保存而丢失
        previous_raw = self._loaded_record["raw_data"] if self._loaded_record else {}
        raw_data = merge_entry_raw(previous_raw, raw_data, self._field_tree)
        self._db.upsert_record(date_str, total_profit, raw_data, note_text, keep_note_if_empty=False)
        self._load_current_date()
        event_bus.data_changed.emit()
        event_bus.toast_requested.emit(
            f"已保存 {self._current_date.month()}月{self._current_date.day()}日 · 利润 {theme.fmt_money(total_profit)}",
            "success",
        )
        return True

    def _confirm_overwrite_if_changed(self, date_str: str) -> bool:
        """打开这一天之后，若它被云同步（其他设备）改过，保存前先问，避免静默覆盖。"""
        if self._db.get_record(date_str) == self._loaded_record:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("这一天刚被其他设备修改过")
        box.setText(f"{date_str} 在你编辑期间已通过云同步更新。\n继续保存会用你现在的输入覆盖它（被覆盖的版本仍保留在服务器上）。")
        overwrite = box.addButton("仍然保存", QMessageBox.ButtonRole.AcceptRole)
        reload_latest = box.addButton("载入最新数据", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(reload_latest)
        box.exec()
        if box.clickedButton() is overwrite:
            return True
        if box.clickedButton() is reload_latest:
            self._load_current_date()
        return False

    # ─────────────────────────────────────
    # 外部变化
    # ─────────────────────────────────────

    def _on_config_changed(self) -> None:
        # 分类被修改（本机设置页或云同步）：重建输入框，同时保留正在输入、尚未保存的内容
        typed = {key: line_edit.text() for key, line_edit in self._field_inputs.items()} \
            if self.has_unsaved_changes() else None
        note = self._note_input.toPlainText()
        loaded = self._loaded_record
        self._rebuild_fields()
        if typed is None:
            return
        self._loaded_record = loaded  # 这一天若同时被同步改过，保存时仍会提示，避免静默覆盖
        for key, text in typed.items():
            line_edit = self._field_inputs.get(key)
            if line_edit is not None:
                line_edit.setText(text)
        with QSignalBlocker(self._note_input):
            self._note_input.setPlainText(note)
        self._update_state()

    def _on_data_changed(self) -> None:
        # 云同步或其他页面修改了数据：没有未保存修改时才刷新，避免覆盖用户正在输入的内容
        if not self.has_unsaved_changes():
            self._load_current_date()

    def roll_over_to_today(self) -> bool:
        """正在看的是“今天”而日期已过零点（例如程序开着过夜）：没有未保存的修改时自动换到新的今天。"""
        today = QDate.currentDate()
        if self._loaded_as_today and self._current_date != today and not self.has_unsaved_changes():
            self._request_date(today)
            return True
        return False

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self.roll_over_to_today() and not self.has_unsaved_changes():
            self._load_current_date()
        super().showEvent(event)
        self._relayout_grids()

