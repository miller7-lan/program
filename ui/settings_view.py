"""
ui/settings_view.py
设置页 — 用户自定义一级/二级账目分类和利润计算公式。
"""

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from core.event_bus import event_bus
from core.field_config import flatten_formula_fields, load_field_tree_from_json, normalize_field_tree


class BranchNodeButton(QPushButton):
    """用于树状分支展示的可选节点按钮。"""

    def __init__(self, text: str, level: str, path: tuple[str, str | None], parent=None):
        super().__init__(text, parent)
        self.path = path
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setObjectName("BranchNodeButton" if level == "parent" else "BranchChildButton")


class SettingsView(QWidget):
    """设置视图：自定义分级字段和利润公式配置。"""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._field_tree_state: list[dict[str, list[str]]] = []
        self._selected_path: tuple[str, str | None] | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        page_scroll = QScrollArea()
        page_scroll.setWidgetResizable(True)
        page_scroll.setObjectName("PageScrollArea")
        page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        root_layout.addWidget(page_scroll)

        page = QWidget()
        page_scroll.setWidget(page)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(44, 36, 44, 36)
        layout.setSpacing(26)

        title = QLabel("⚙️ 设置")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        field_section_label = QLabel("分类思维导图")
        field_section_label.setObjectName("SectionLabel")
        layout.addWidget(field_section_label)

        field_hint = QLabel(
            "一级分类会保留汇总变量；二级分类也能直接作为公式变量。点击下方节点后，可以继续添加、改名或删除。"
        )
        field_hint.setObjectName("HintLabel")
        layout.addWidget(field_hint)

        self._selected_hint = QLabel("当前未选中节点")
        self._selected_hint.setObjectName("SelectedHint")
        layout.addWidget(self._selected_hint)

        self._branch_scroll = QScrollArea()
        self._branch_scroll.setWidgetResizable(True)
        self._branch_scroll.setObjectName("MindMapBoard")
        self._branch_scroll.setMinimumHeight(300)
        self._branch_scroll.setMaximumHeight(360)
        self._branch_container = QWidget()
        self._branch_layout = QVBoxLayout(self._branch_container)
        self._branch_layout.setContentsMargins(18, 18, 18, 18)
        self._branch_layout.setSpacing(16)
        self._branch_scroll.setWidget(self._branch_container)
        layout.addWidget(self._branch_scroll)

        editor_frame = QFrame()
        editor_frame.setObjectName("FieldsFrame")
        editor_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        editor_layout = QVBoxLayout(editor_frame)
        editor_layout.setContentsMargins(18, 18, 18, 18)
        editor_layout.setSpacing(14)

        self._top_level_input = QLineEdit()
        self._top_level_input.setPlaceholderText("新增一级分类，例如：成本")
        self._top_level_input.setObjectName("FieldInput")
        self._top_level_input.returnPressed.connect(self._on_add_top_level)

        top_row = QHBoxLayout()
        top_row.setSpacing(12)
        top_row.addWidget(self._top_level_input, stretch=1)
        add_top_btn = QPushButton("＋ 添加一级")
        add_top_btn.setObjectName("SecondaryButton")
        add_top_btn.clicked.connect(self._on_add_top_level)
        top_row.addWidget(add_top_btn)
        editor_layout.addLayout(top_row)

        self._child_input = QLineEdit()
        self._child_input.setPlaceholderText("给选中的一级分类添加二级，例如：租金")
        self._child_input.setObjectName("FieldInput")
        self._child_input.returnPressed.connect(self._on_add_child)

        child_row = QHBoxLayout()
        child_row.setSpacing(12)
        child_row.addWidget(self._child_input, stretch=1)
        add_child_btn = QPushButton("＋ 添加二级")
        add_child_btn.setObjectName("SecondaryButton")
        add_child_btn.clicked.connect(self._on_add_child)
        child_row.addWidget(add_child_btn)
        editor_layout.addLayout(child_row)

        self._rename_input = QLineEdit()
        self._rename_input.setPlaceholderText("重命名当前选中节点")
        self._rename_input.setObjectName("FieldInput")
        self._rename_input.returnPressed.connect(self._on_rename_selected)

        rename_row = QHBoxLayout()
        rename_row.setSpacing(12)
        rename_row.addWidget(self._rename_input, stretch=1)
        rename_btn = QPushButton("✎ 修改名称")
        rename_btn.setObjectName("SecondaryButton")
        rename_btn.clicked.connect(self._on_rename_selected)
        rename_row.addWidget(rename_btn)

        del_btn = QPushButton("－ 删除选中")
        del_btn.setObjectName("DangerButton")
        del_btn.clicked.connect(self._on_remove_field)
        rename_row.addWidget(del_btn)
        editor_layout.addLayout(rename_row)

        layout.addWidget(editor_frame)

        formula_section_label = QLabel("利润计算公式")
        formula_section_label.setObjectName("SectionLabel")
        layout.addWidget(formula_section_label)

        formula_hint = QLabel(
            "公式支持直接引用二级分类名，同时也保留一级分类汇总变量。\n"
            "例如：零钱 + 微信 - 租金 - 水电费   或   营业额 - 成本"
        )
        formula_hint.setObjectName("HintLabel")
        layout.addWidget(formula_hint)

        self._formula_input = QLineEdit()
        self._formula_input.setPlaceholderText("营业额 - 成本")
        self._formula_input.setObjectName("FieldInput")
        layout.addWidget(self._formula_input)

        self._build_calculator(layout)

        save_row = QHBoxLayout()
        save_row.addStretch()
        save_btn = QPushButton("💾  保存设置")
        save_btn.setObjectName("PrimaryButton")
        save_btn.setMinimumWidth(160)
        save_btn.setMinimumHeight(44)
        save_btn.clicked.connect(self._on_save)
        save_row.addWidget(save_btn)
        layout.addLayout(save_row)

        self._load_config()

    def _build_calculator(self, parent_layout: QVBoxLayout) -> None:
        calc_frame = QFrame()
        calc_frame.setObjectName("CalcFrame")
        calc_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        calc_layout = QVBoxLayout(calc_frame)
        calc_layout.setContentsMargins(14, 14, 14, 14)
        calc_layout.setSpacing(12)

        ops_row = QHBoxLayout()
        ops_row.setSpacing(8)
        for op in ["+", "-", "*", "/", "(", ")"]:
            btn = QPushButton(op)
            btn.setObjectName("CalcButton")
            btn.setFixedSize(42, 38)
            btn.clicked.connect(lambda _, char=op: self._insert_formula_text(f" {char} "))
            ops_row.addWidget(btn)

        back_btn = QPushButton("⌫")
        back_btn.setObjectName("SecondaryButton")
        back_btn.setFixedSize(42, 38)
        back_btn.clicked.connect(self._on_backspace)
        ops_row.addWidget(back_btn)

        clear_btn = QPushButton("AC")
        clear_btn.setObjectName("DangerButton")
        clear_btn.setFixedSize(52, 38)
        clear_btn.clicked.connect(self._formula_input.clear)
        ops_row.addWidget(clear_btn)
        ops_row.addStretch()
        calc_layout.addLayout(ops_row)

        reference_label = QLabel("公式引用参考")
        reference_label.setObjectName("SectionLabel")
        calc_layout.addWidget(reference_label)

        reference_hint = QLabel(
            "下面优先显示二级分类卡片。点击二级分类卡片会直接插入它自己的名称；没有二级分类时，才显示一级分类卡片。"
        )
        reference_hint.setObjectName("HintLabel")
        calc_layout.addWidget(reference_hint)

        self._field_cards_layout = QVBoxLayout()
        self._field_cards_layout.setSpacing(10)
        calc_layout.addLayout(self._field_cards_layout)

        parent_layout.addWidget(calc_frame)

    def _rebuild_branch_board(self) -> None:
        while self._branch_layout.count():
            item = self._branch_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

        for item in self._field_tree_state:
            parent_name = item["name"]
            children = item["children"]

            card = QFrame()
            card.setObjectName("BranchCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(18, 16, 18, 16)
            card_layout.setSpacing(10)

            top_row = QHBoxLayout()
            top_row.setSpacing(12)
            parent_btn = BranchNodeButton(parent_name, "parent", (parent_name, None))
            parent_btn.clicked.connect(lambda checked=False, path=(parent_name, None): self._select_path(path))
            parent_btn.setChecked(self._selected_path == (parent_name, None))
            top_row.addWidget(parent_btn)

            badge = QLabel(f"{len(children)} 个二级分类" if children else "直接录入")
            badge.setObjectName("BranchMetaLabel")
            top_row.addWidget(badge)
            top_row.addStretch()
            card_layout.addLayout(top_row)

            if children:
                for index, child_name in enumerate(children):
                    child_row = QHBoxLayout()
                    child_row.setContentsMargins(26, 0, 0, 0)
                    child_row.setSpacing(10)

                    branch_label = QLabel("└─" if index == len(children) - 1 else "├─")
                    branch_label.setObjectName("BranchLineLabel")
                    child_row.addWidget(branch_label)

                    child_btn = BranchNodeButton(child_name, "child", (parent_name, child_name))
                    child_btn.clicked.connect(
                        lambda checked=False, path=(parent_name, child_name): self._select_path(path)
                    )
                    child_btn.setChecked(self._selected_path == (parent_name, child_name))
                    child_row.addWidget(child_btn)
                    child_row.addStretch()
                    card_layout.addLayout(child_row)
            else:
                empty_hint = QLabel("这个一级分类下还没有二级分类，录入页会直接显示一个金额输入框。")
                empty_hint.setObjectName("HintLabel")
                card_layout.addWidget(empty_hint)

            self._branch_layout.addWidget(card)

        self._branch_layout.addStretch()
        self._update_selected_hint()

    def _refresh_field_buttons(self) -> None:
        while self._field_cards_layout.count():
            item = self._field_cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.spacerItem():
                continue

        for item in self._field_tree_state:
            parent_name = item["name"]
            children = item["children"]

            card = QFrame()
            card.setObjectName("FormulaFieldCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.setSpacing(8)

            top_row = QHBoxLayout()
            top_row.setSpacing(10)

            parent_label = QLabel(parent_name)
            parent_label.setObjectName("FormulaGroupTitle")
            top_row.addWidget(parent_label)

            state_label = QLabel("点击二级卡片直接插入二级变量" if children else "当前无二级分类，显示一级分类卡片")
            state_label.setObjectName("FormulaStateLabel")
            top_row.addWidget(state_label)
            top_row.addStretch()
            card_layout.addLayout(top_row)

            if children:
                chip_hint = QLabel(f"一级分类「{parent_name}」下的二级公式变量：")
                chip_hint.setObjectName("FormulaChildSummary")
                chip_hint.setWordWrap(True)
                card_layout.addWidget(chip_hint)

                chip_row = QHBoxLayout()
                chip_row.setSpacing(8)
                chip_row.setContentsMargins(0, 0, 0, 0)
                for child_name in children:
                    child_btn = QPushButton(child_name)
                    child_btn.setObjectName("FormulaChildButton")
                    child_btn.setMinimumHeight(42)
                    child_btn.setToolTip(f"点击后插入二级分类：{child_name}")
                    child_btn.clicked.connect(lambda _, n=child_name: self._insert_formula_text(n))
                    chip_row.addWidget(child_btn)

                chip_row.addStretch()
                card_layout.addLayout(chip_row)
            else:
                child_label = QLabel("这个一级分类下面暂无二级分类，所以这里直接提供一级分类卡片。")
                child_label.setObjectName("FormulaChildSummary")
                child_label.setWordWrap(True)
                card_layout.addWidget(child_label)

                direct_card = QPushButton(parent_name)
                direct_card.setObjectName("FormulaDirectButton")
                direct_card.setMinimumHeight(44)
                direct_card.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
                direct_card.clicked.connect(lambda _, n=parent_name: self._insert_formula_text(n))
                card_layout.addWidget(direct_card, alignment=Qt.AlignmentFlag.AlignLeft)

            self._field_cards_layout.addWidget(card)

        self._field_cards_layout.addItem(
            QSpacerItem(20, 10, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        )

    def _insert_formula_text(self, text: str) -> None:
        current_pos = self._formula_input.cursorPosition()
        original_text = self._formula_input.text()
        new_text = original_text[:current_pos] + text + original_text[current_pos:]
        self._formula_input.setText(new_text)
        self._formula_input.setFocus()
        self._formula_input.setCursorPosition(current_pos + len(text))

    def _on_backspace(self) -> None:
        self._formula_input.backspace()
        self._formula_input.setFocus()

    def _select_path(self, path: tuple[str, str | None]) -> None:
        self._selected_path = path
        self._rename_input.setText(path[1] or path[0])
        self._rebuild_branch_board()

    def _on_add_top_level(self) -> None:
        name = self._top_level_input.text().strip()
        if not name:
            return
        if name in self._get_formula_field_names():
            QMessageBox.warning(self, "字段重复", f"分类变量「{name}」已存在，请避免一级和二级重名。")
            return

        self._field_tree_state.append({"name": name, "children": []})
        self._field_tree_state = normalize_field_tree(self._field_tree_state)
        self._top_level_input.clear()
        self._select_path((name, None))
        self._refresh_field_buttons()

    def _on_add_child(self) -> None:
        name = self._child_input.text().strip()
        if not name:
            return
        if self._selected_path is None:
            QMessageBox.information(self, "请选择分类", "请先点选一个一级分类，再添加二级分类。")
            return

        parent_name = self._selected_path[0]
        for item in self._field_tree_state:
            if item["name"] != parent_name:
                continue
            if name in self._get_formula_field_names():
                QMessageBox.warning(self, "字段重复", f"分类变量「{name}」已存在，请避免一级和二级重名。")
                return
            item["children"].append(name)
            item["children"] = [child for child in item["children"] if child.strip()]
            self._child_input.clear()
            self._select_path((parent_name, name))
            return

    def _on_rename_selected(self) -> None:
        if self._selected_path is None:
            QMessageBox.information(self, "请选择节点", "请先在树状图里选中一个节点。")
            return

        new_name = self._rename_input.text().strip()
        if not new_name:
            QMessageBox.warning(self, "名称为空", "请输入新的分类名称。")
            return

        parent_name, child_name = self._selected_path
        if child_name is None:
            existing = set(self._get_formula_field_names()) - {parent_name}
            if new_name in existing:
                QMessageBox.warning(self, "字段重复", f"分类变量「{new_name}」已存在，请使用其他名称。")
                return
            for item in self._field_tree_state:
                if item["name"] == parent_name:
                    item["name"] = new_name
                    self._selected_path = (new_name, None)
                    break
        else:
            existing = set(self._get_formula_field_names()) - {child_name}
            if new_name in existing:
                QMessageBox.warning(self, "字段重复", f"分类变量「{new_name}」已存在，请使用其他名称。")
                return
            for item in self._field_tree_state:
                if item["name"] != parent_name:
                    continue
                index = item["children"].index(child_name)
                item["children"][index] = new_name
                self._selected_path = (parent_name, new_name)
                break

        self._field_tree_state = normalize_field_tree(self._field_tree_state)
        self._rebuild_branch_board()
        self._refresh_field_buttons()

    def _on_remove_field(self) -> None:
        if self._selected_path is None:
            QMessageBox.information(self, "请选择节点", "请先在树状图里选中要删除的节点。")
            return

        parent_name, child_name = self._selected_path
        if child_name is None:
            self._field_tree_state = [item for item in self._field_tree_state if item["name"] != parent_name]
        else:
            for item in self._field_tree_state:
                if item["name"] == parent_name:
                    item["children"] = [child for child in item["children"] if child != child_name]
                    break

        self._selected_path = None
        self._rename_input.clear()
        self._field_tree_state = normalize_field_tree(self._field_tree_state)
        self._rebuild_branch_board()
        self._refresh_field_buttons()

    def _get_field_tree(self) -> list[dict[str, list[str]]]:
        return normalize_field_tree(self._field_tree_state)

    def _get_formula_field_names(self) -> list[str]:
        return flatten_formula_fields(self._get_field_tree())

    def _load_config(self) -> None:
        field_tree_json = self._db.get_config("custom_field_tree", "")
        if not field_tree_json:
            field_tree_json = self._db.get_config("custom_fields", '["营业额", "成本"]')

        self._field_tree_state = load_field_tree_from_json(field_tree_json)
        self._selected_path = (self._field_tree_state[0]["name"], None) if self._field_tree_state else None

        formula = self._db.get_config("profit_formula", "营业额 - 成本")
        self._formula_input.setText(formula)
        if self._selected_path:
            self._rename_input.setText(self._selected_path[0])
        self._rebuild_branch_board()
        self._refresh_field_buttons()

    def _on_save(self) -> None:
        from core.formula_engine import FormulaEngine

        field_tree = self._get_field_tree()
        fields = flatten_formula_fields(field_tree)
        formula = self._formula_input.text().strip()

        if not fields:
            QMessageBox.warning(self, "配置错误", "请至少添加一个一级分类。")
            return
        if not formula:
            QMessageBox.warning(self, "配置错误", "公式不能为空。")
            return

        engine = FormulaEngine()
        ok, msg = engine.validate(formula)
        if not ok:
            QMessageBox.warning(self, "公式语法错误", msg)
            return

        used_vars = engine.extract_variables(formula)
        undefined = [v for v in used_vars if v not in fields]
        if undefined:
            QMessageBox.warning(
                self,
                "公式变量未定义",
                f"公式引用了未定义的分类变量：{', '.join(undefined)}\n请先添加这些一级或二级分类，或修改公式。",
            )
            return

        self._db.set_config("custom_field_tree", json.dumps(field_tree, ensure_ascii=False))
        self._db.set_config("custom_fields", json.dumps(fields, ensure_ascii=False))
        self._db.set_config("profit_formula", formula)

        event_bus.config_changed.emit()
        QMessageBox.information(self, "保存成功", "设置已保存，录入页和导出报表都会按新层级展示。")

    def _update_selected_hint(self) -> None:
        if self._selected_path is None:
            self._selected_hint.setText("当前未选中节点")
            return

        parent_name, child_name = self._selected_path
        if child_name is None:
            self._selected_hint.setText(f"当前选中：一级分类「{parent_name}」")
        else:
            self._selected_hint.setText(f"当前选中：二级分类「{parent_name} / {child_name}」")

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def showEvent(self, event) -> None:
        self._load_config()
        super().showEvent(event)
