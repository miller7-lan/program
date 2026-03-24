"""
ui/settings_view.py
设置页 — 用户自定义"基数项"列表和"利润计算公式"。

骨架说明：
  - 字段管理区：增加/删除自定义字段（如"营业额"、"成本"、"零钱"等）
  - 公式设置区：输入利润公式字符串（如 "营业额 - 成本"）
  - 保存后写入 Config 表，发射 config_changed 信号通知录入页重建字段
  - 使用 FormulaEngine.validate() 在保存前校验公式合法性
"""

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from core.event_bus import event_bus


class SettingsView(QWidget):
    """设置视图：自定义字段和利润公式配置。"""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._build_ui()

    # ─────────────────────────────────────
    # UI 构建
    # ─────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(24)

        # 页面标题
        title = QLabel("⚙️ 设置")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        # ── 自定义字段管理 ────────────────────
        field_section_label = QLabel("自定义账目字段")
        field_section_label.setObjectName("SectionLabel")
        layout.addWidget(field_section_label)

        field_hint = QLabel("这些字段将出现在每日录入页中，在公式里用字段名来引用它们。")
        field_hint.setObjectName("HintLabel")
        layout.addWidget(field_hint)

        # 字段列表
        self._field_list = QListWidget()
        self._field_list.setObjectName("FieldList")
        self._field_list.setMaximumHeight(160)
        layout.addWidget(self._field_list)

        # 添加字段行
        add_row = QHBoxLayout()
        self._new_field_input = QLineEdit()
        self._new_field_input.setPlaceholderText("新字段名称，例如：零钱")
        self._new_field_input.setObjectName("FieldInput")
        self._new_field_input.returnPressed.connect(self._on_add_field)
        add_row.addWidget(self._new_field_input)

        add_btn = QPushButton("＋ 添加")
        add_btn.setObjectName("SecondaryButton")
        add_btn.clicked.connect(self._on_add_field)
        add_row.addWidget(add_btn)

        del_btn = QPushButton("－ 删除选中")
        del_btn.setObjectName("DangerButton")
        del_btn.clicked.connect(self._on_remove_field)
        add_row.addWidget(del_btn)
        layout.addLayout(add_row)

        # ── 利润公式设置 ──────────────────────
        formula_section_label = QLabel("利润计算公式")
        formula_section_label.setObjectName("SectionLabel")
        layout.addWidget(formula_section_label)

        formula_hint = QLabel(
            "使用上方定义的字段名写公式，支持 ＋ − × ÷ 和括号。\n"
            "例如：营业额 - 成本   或   (营业额 - 成本) * 0.9"
        )
        formula_hint.setObjectName("HintLabel")
        layout.addWidget(formula_hint)

        self._formula_input = QLineEdit()
        self._formula_input.setPlaceholderText("营业额 - 成本")
        self._formula_input.setObjectName("FieldInput")
        layout.addWidget(self._formula_input)

        # ── 公式助手（计算器面板） ──────────────
        self._build_calculator(layout)

        # ── 保存按钮 ─────────────────────────
        save_row = QHBoxLayout()
        save_row.addStretch()
        save_btn = QPushButton("💾  保存设置")
        save_btn.setObjectName("PrimaryButton")
        save_btn.setMinimumWidth(140)
        save_btn.setMinimumHeight(42)
        save_btn.clicked.connect(self._on_save)
        save_row.addWidget(save_btn)
        layout.addLayout(save_row)
        layout.addStretch()

        # 初始加载配置
        self._load_config()
        self._refresh_field_buttons()

    def _build_calculator(self, parent_layout: QVBoxLayout) -> None:
        """构建公式计算器按钮面板。"""
        calc_frame = QFrame()
        calc_frame.setObjectName("CalcFrame")
        calc_layout = QVBoxLayout(calc_frame)
        calc_layout.setContentsMargins(12, 12, 12, 12)
        calc_layout.setSpacing(10)

        # 第一行：运算符
        ops_row = QHBoxLayout()
        ops_row.setSpacing(8)
        operators = ["+", "-", "*", "/", "(", ")"]
        for op in operators:
            btn = QPushButton(op)
            btn.setObjectName("CalcButton")
            btn.setFixedSize(40, 36)
            btn.clicked.connect(lambda _, char=op: self._insert_formula_text(f" {char} "))
            ops_row.addWidget(btn)

        ops_row.addSpacing(10)
        # 退格和清空
        back_btn = QPushButton("⌫")
        back_btn.setObjectName("SecondaryButton")
        back_btn.setFixedSize(40, 36)
        back_btn.clicked.connect(self._on_backspace)
        ops_row.addWidget(back_btn)

        clear_btn = QPushButton("AC")
        clear_btn.setObjectName("DangerButton")
        clear_btn.setFixedSize(40, 36)
        clear_btn.clicked.connect(self._formula_input.clear)
        ops_row.addWidget(clear_btn)

        ops_row.addStretch()
        calc_layout.addLayout(ops_row)

        # 第二行：基数名称（动态生成）
        self._field_buttons_layout = QHBoxLayout()
        self._field_buttons_layout.setSpacing(8)
        calc_layout.addLayout(self._field_buttons_layout)

        parent_layout.addWidget(calc_frame)

    def _refresh_field_buttons(self) -> None:
        """根据当前字段列表动态生成按钮。"""
        # 先清空旧按钮
        while self._field_buttons_layout.count():
            item = self._field_buttons_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 生成新按钮
        fields = self._get_field_names()
        for name in fields:
            btn = QPushButton(name)
            btn.setObjectName("FieldCalcButton")
            btn.setMinimumHeight(32)
            # 自动调整宽度
            btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _, n=name: self._insert_formula_text(n))
            self._field_buttons_layout.addWidget(btn)
        
        self._field_buttons_layout.addStretch()

    def _insert_formula_text(self, text: str) -> None:
        """在光标位置插入文本。"""
        current_pos = self._formula_input.cursorPosition()
        original_text = self._formula_input.text()
        
        # 拼接
        new_text = original_text[:current_pos] + text + original_text[current_pos:]
        self._formula_input.setText(new_text)
        self._formula_input.setFocus()
        self._formula_input.setCursorPosition(current_pos + len(text))

    def _on_backspace(self) -> None:
        """处理退格键。"""
        self._formula_input.backspace()
        self._formula_input.setFocus()

    # ─────────────────────────────────────
    # 字段管理
    # ─────────────────────────────────────

    def _on_add_field(self) -> None:
        """将新字段名添加到列表。"""
        name = self._new_field_input.text().strip()
        if not name:
            return
        # 检查重复
        existing = self._get_field_names()
        if name in existing:
            QMessageBox.warning(self, "字段重复", f"字段「{name}」已存在。")
            return
        self._field_list.addItem(QListWidgetItem(name))
        self._new_field_input.clear()
        self._refresh_field_buttons()

    def _on_remove_field(self) -> None:
        """删除列表中选中的字段。"""
        row = self._field_list.currentRow()
        if row >= 0:
            self._field_list.takeItem(row)
            self._refresh_field_buttons()

    def _get_field_names(self) -> list[str]:
        return [
            self._field_list.item(i).text()
            for i in range(self._field_list.count())
        ]

    # ─────────────────────────────────────
    # 配置读写
    # ─────────────────────────────────────

    def _load_config(self) -> None:
        """从数据库加载当前配置，填充 UI。"""
        fields_json = self._db.get_config("custom_fields", '["营业额", "成本"]')
        try:
            fields = json.loads(fields_json)
        except json.JSONDecodeError:
            fields = ["营业额", "成本"]
        self._field_list.clear()
        for f in fields:
            self._field_list.addItem(QListWidgetItem(f))

        formula = self._db.get_config("profit_formula", "营业额 - 成本")
        self._formula_input.setText(formula)
        self._refresh_field_buttons()

    def _on_save(self) -> None:
        """验证公式并将配置写入数据库，发射 config_changed 信号。"""
        from core.formula_engine import FormulaEngine

        fields = self._get_field_names()
        formula = self._formula_input.text().strip()

        if not fields:
            QMessageBox.warning(self, "配置错误", "请至少添加一个字段。")
            return
        if not formula:
            QMessageBox.warning(self, "配置错误", "公式不能为空。")
            return

        # 验证公式语法
        engine = FormulaEngine()
        ok, msg = engine.validate(formula)
        if not ok:
            QMessageBox.warning(self, "公式语法错误", msg)
            return

        # 检查公式中的变量是否都在字段列表中
        used_vars = engine.extract_variables(formula)
        undefined = [v for v in used_vars if v not in fields]
        if undefined:
            QMessageBox.warning(
                self, "公式变量未定义",
                f"公式引用了未定义的字段：{', '.join(undefined)}\n"
                "请先在字段列表中添加这些字段，或修改公式。"
            )
            return

        # 写入数据库
        self._db.set_config("custom_fields", json.dumps(fields, ensure_ascii=False))
        self._db.set_config("profit_formula", formula)

        # 通知录入页重建字段
        event_bus.config_changed.emit()

        QMessageBox.information(self, "保存成功", "设置已保存，录入页字段已更新。")

    def showEvent(self, event) -> None:
        self._load_config()
        super().showEvent(event)
