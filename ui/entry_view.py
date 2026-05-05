"""
ui/entry_view.py
录入页 — 用户录入当日各项账目数据并保存。
"""

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from core.event_bus import event_bus
from core.field_config import build_formula_variables, load_field_tree_from_json


class EntryView(QWidget):
    """录入视图：支持分级字段的每日账目录入界面。"""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._field_inputs: dict[str, QLineEdit] = {}
        self._field_tree: list[dict] = []
        self._build_ui()
        event_bus.config_changed.connect(self._rebuild_fields)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 30)
        layout.setSpacing(20)

        title = QLabel("✏️ 录入今日数据")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        hint = QLabel("按分类分支录入金额。带二级分类的一级节点会自动汇总，直接用于利润计算和 Excel 导出。")
        hint.setObjectName("HintLabel")
        layout.addWidget(hint)

        date_frame = QFrame()
        date_frame.setObjectName("FieldsFrame")
        date_layout = QHBoxLayout(date_frame)
        date_layout.setContentsMargins(18, 14, 18, 14)
        date_layout.setSpacing(14)
        date_layout.addWidget(QLabel("日期："))

        self._date_edit = QDateEdit(QDate.currentDate())
        self._date_edit.setDisplayFormat("yyyy-MM-dd")
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setObjectName("DateEdit")
        self._date_edit.dateChanged.connect(self._load_today)
        date_layout.addWidget(self._date_edit)
        date_layout.addStretch()
        layout.addWidget(date_frame)

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setObjectName("EntryScrollArea")
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._fields_container = QWidget()
        self._fields_layout = QVBoxLayout(self._fields_container)
        self._fields_layout.setContentsMargins(6, 6, 6, 6)
        self._fields_layout.setSpacing(18)
        self._scroll_area.setWidget(self._fields_container)
        layout.addWidget(self._scroll_area, stretch=1)

        note_frame = QFrame()
        note_frame.setObjectName("FieldsFrame")
        note_layout = QVBoxLayout(note_frame)
        note_layout.setContentsMargins(18, 16, 18, 16)
        note_layout.setSpacing(10)

        note_label = QLabel("备注")
        note_label.setObjectName("SectionLabel")
        note_layout.addWidget(note_label)

        note_hint = QLabel("备注会同步到历史表格和 Excel 导出。留空时不会覆盖这一天已保存的旧备注。")
        note_hint.setObjectName("HintLabel")
        note_layout.addWidget(note_hint)

        self._note_input = QTextEdit()
        self._note_input.setObjectName("FieldInput")
        self._note_input.setPlaceholderText("输入当天备注，例如：租金已支付、活动日营业额偏高")
        self._note_input.setMinimumHeight(96)
        note_layout.addWidget(self._note_input)
        layout.addWidget(note_frame)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._save_btn = QPushButton("💾  保存记录")
        self._save_btn.setObjectName("PrimaryButton")
        self._save_btn.setMinimumWidth(170)
        self._save_btn.setMinimumHeight(46)
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)
        layout.addLayout(btn_row)

        self._rebuild_fields()

    def _rebuild_fields(self) -> None:
        while self._fields_layout.count():
            item = self._fields_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

        self._field_inputs.clear()

        field_tree_json = self._db.get_config("custom_field_tree", "")
        if not field_tree_json:
            field_tree_json = self._db.get_config("custom_fields", '["营业额", "成本"]')
        self._field_tree = load_field_tree_from_json(field_tree_json)

        for item in self._field_tree:
            parent_name = item["name"]
            children = item["children"]

            card = QFrame()
            card.setObjectName("EntryCategoryCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(20, 18, 20, 18)
            card_layout.setSpacing(12)

            header_row = QHBoxLayout()
            header_row.setSpacing(10)
            dot_label = QLabel("●")
            dot_label.setObjectName("EntryNodeDot")
            header_row.addWidget(dot_label)

            title_label = QLabel(parent_name)
            title_label.setObjectName("EntryCategoryTitle")
            header_row.addWidget(title_label)

            meta = QLabel("按二级分类汇总" if children else "直接作为一级分类录入")
            meta.setObjectName("BranchMetaLabel")
            header_row.addWidget(meta)
            header_row.addStretch()
            card_layout.addLayout(header_row)

            if children:
                for index, child_name in enumerate(children):
                    row = QHBoxLayout()
                    row.setContentsMargins(26, 0, 0, 0)
                    row.setSpacing(12)

                    branch_label = QLabel("└─" if index == len(children) - 1 else "├─")
                    branch_label.setObjectName("EntryBranchLabel")
                    row.addWidget(branch_label)

                    child_label = QLabel(child_name)
                    child_label.setObjectName("EntryFieldLabel")
                    child_label.setMinimumWidth(140)
                    row.addWidget(child_label)

                    row.addStretch()

                    key = f"{parent_name}::{child_name}"
                    line_edit = QLineEdit()
                    line_edit.setPlaceholderText(f"输入 {child_name} 金额（元）")
                    line_edit.setObjectName("FieldInput")
                    line_edit.setMinimumWidth(240)
                    row.addWidget(line_edit)
                    self._field_inputs[key] = line_edit
                    card_layout.addLayout(row)
            else:
                row = QHBoxLayout()
                row.setSpacing(12)

                child_label = QLabel("金额")
                child_label.setObjectName("EntryFieldLabel")
                child_label.setMinimumWidth(140)
                row.addWidget(child_label)
                row.addStretch()

                line_edit = QLineEdit()
                line_edit.setPlaceholderText(f"输入 {parent_name} 金额（元）")
                line_edit.setObjectName("FieldInput")
                line_edit.setMinimumWidth(240)
                row.addWidget(line_edit)
                self._field_inputs[parent_name] = line_edit
                card_layout.addLayout(row)

            self._fields_layout.addWidget(card)

        self._fields_layout.addStretch()
        self._load_today()

    def _on_save(self) -> None:
        from core.formula_engine import FormulaEngine, FormulaError

        raw_data: dict[str, float | dict[str, float]] = {}
        parsed_values: dict[str, float] = {}

        for field_name, line_edit in self._field_inputs.items():
            text = line_edit.text().strip()
            if not text:
                parsed_values[field_name] = 0.0
                continue
            try:
                parsed_values[field_name] = float(text)
            except ValueError:
                display_name = field_name.replace("::", " / ")
                QMessageBox.warning(self, "输入有误", f"字段「{display_name}」请输入数字，不能包含文字。")
                return

        for item in self._field_tree:
            parent_name = item["name"]
            children = item["children"]
            if children:
                raw_data[parent_name] = {
                    child_name: parsed_values.get(f"{parent_name}::{child_name}", 0.0)
                    for child_name in children
                }
            else:
                raw_data[parent_name] = parsed_values.get(parent_name, 0.0)

        formula = self._db.get_config("profit_formula", "营业额 - 成本")
        engine = FormulaEngine()
        try:
            total_profit = engine.evaluate(formula, build_formula_variables(self._field_tree, raw_data))
        except FormulaError as e:
            QMessageBox.warning(self, "公式计算错误", str(e))
            return

        date_str = self._date_edit.date().toString("yyyy-MM-dd")
        note_text = self._note_input.toPlainText().strip()
        self._db.upsert_record(date_str, total_profit, raw_data, note_text)
        event_bus.data_changed.emit()

        QMessageBox.information(
            self,
            "保存成功",
            f"{date_str} 记录已保存。\n今日利润：{total_profit:.2f} 元",
        )

    def showEvent(self, event) -> None:
        self._load_today()
        super().showEvent(event)

    def _load_today(self) -> None:
        date_str = self._date_edit.date().toString("yyyy-MM-dd")
        record = self._db.get_record(date_str)
        if not record:
            for line_edit in self._field_inputs.values():
                line_edit.clear()
            self._note_input.clear()
            return

        raw_data = record["raw_data"]
        for field_name, line_edit in self._field_inputs.items():
            if "::" in field_name:
                parent_name, child_name = field_name.split("::", 1)
                parent_value = raw_data.get(parent_name, {})
                value = parent_value.get(child_name, "") if isinstance(parent_value, dict) else ""
            else:
                value = raw_data.get(field_name, "")
            line_edit.setText(str(value) if value else "")
        self._note_input.setPlainText(record.get("note", ""))

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())
