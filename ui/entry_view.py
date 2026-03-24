"""
ui/entry_view.py
录入页 — 用户录入当日各项账目数据并保存。

骨架说明：
  - 顶部：日期选择控件（默认今日）
  - 中部：动态字段区（从 Config 读取 'custom_fields'，动态生成输入框）
  - 底部："保存" 按钮 → 调用公式引擎计算利润 → 写入 DB → 发射 DataChanged
  - 当字段保存成功后，向 event_bus 发射 data_changed，看板自动刷新
"""

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QDateEdit,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from core.event_bus import event_bus


class EntryView(QWidget):
    """录入视图：支持动态字段的每日账目录入界面。"""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._field_inputs: dict[str, QLineEdit] = {}
        self._build_ui()

        # 配置变化时重建字段区
        event_bus.config_changed.connect(self._rebuild_fields)

    # ─────────────────────────────────────
    # UI 构建
    # ─────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(20)

        # 页面标题
        title = QLabel("✏️ 录入今日数据")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        # ── 日期选择 ─────────────────────────
        date_row = QHBoxLayout()
        date_label = QLabel("日期：")
        date_label.setObjectName("FieldLabel")
        self._date_edit = QDateEdit(QDate.currentDate())
        self._date_edit.setDisplayFormat("yyyy-MM-dd")
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setObjectName("DateEdit")
        date_row.addWidget(date_label)
        date_row.addWidget(self._date_edit)
        date_row.addStretch()
        layout.addLayout(date_row)

        # ── 动态字段区 ────────────────────────
        fields_frame = QFrame()
        fields_frame.setObjectName("FieldsFrame")
        self._fields_layout = QFormLayout(fields_frame)
        self._fields_layout.setContentsMargins(20, 20, 20, 20)
        self._fields_layout.setSpacing(14)
        layout.addWidget(fields_frame)

        # ── 底部按钮 ─────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._save_btn = QPushButton("💾  保存记录")
        self._save_btn.setObjectName("PrimaryButton")
        self._save_btn.setMinimumWidth(140)
        self._save_btn.setMinimumHeight(42)
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)
        layout.addLayout(btn_row)
        layout.addStretch()

        # 初始化字段
        self._rebuild_fields()

    def _rebuild_fields(self) -> None:
        """根据 Config 中的 custom_fields 动态重建输入框。"""
        import json

        # 清空旧字段
        while self._fields_layout.rowCount() > 0:
            self._fields_layout.removeRow(0)
        self._field_inputs.clear()

        # 读取配置
        fields_json = self._db.get_config("custom_fields", '["营业额", "成本"]')
        try:
            fields = json.loads(fields_json)
        except json.JSONDecodeError:
            fields = ["营业额", "成本"]

        for field_name in fields:
            line_edit = QLineEdit()
            line_edit.setPlaceholderText(f"请输入 {field_name} 金额（元）")
            line_edit.setObjectName("FieldInput")
            self._fields_layout.addRow(field_name + "：", line_edit)
            self._field_inputs[field_name] = line_edit

    # ─────────────────────────────────────
    # 保存逻辑
    # ─────────────────────────────────────

    def _on_save(self) -> None:
        """读取表单数据 → 调用公式引擎 → 写入数据库 → 发射 data_changed。"""
        from core.formula_engine import FormulaEngine, FormulaError

        # 收集字段值
        raw_data: dict[str, float] = {}
        for field_name, line_edit in self._field_inputs.items():
            text = line_edit.text().strip()
            if not text:
                raw_data[field_name] = 0.0
                continue
            try:
                raw_data[field_name] = float(text)
            except ValueError:
                QMessageBox.warning(
                    self, "输入有误",
                    f"字段「{field_name}」请输入数字，不能包含文字。"
                )
                return

        # 计算利润
        formula = self._db.get_config("profit_formula", "营业额 - 成本")
        engine = FormulaEngine()
        try:
            total_profit = engine.evaluate(formula, raw_data)
        except FormulaError as e:
            QMessageBox.warning(self, "公式计算错误", str(e))
            return

        # 写入数据库
        date_str = self._date_edit.date().toString("yyyy-MM-dd")
        self._db.upsert_record(date_str, total_profit, raw_data)

        # 通知其他视图刷新
        event_bus.data_changed.emit()

        QMessageBox.information(
            self, "保存成功",
            f"{date_str} 记录已保存。\n今日利润：{total_profit:.2f} 元"
        )

    def showEvent(self, event) -> None:
        """每次切换到此页时，加载当日已有数据（如有）。"""
        self._load_today()
        super().showEvent(event)

    def _load_today(self) -> None:
        """若当日已有记录，自动回填表单。"""
        date_str = self._date_edit.date().toString("yyyy-MM-dd")
        record = self._db.get_record(date_str)
        if record:
            for field_name, line_edit in self._field_inputs.items():
                val = record["raw_data"].get(field_name, "")
                line_edit.setText(str(val) if val else "")
