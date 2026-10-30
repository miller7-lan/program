"""
ui/settings_view.py
设置页 — 分类结构、利润公式、云同步、数据备份。

  - 分类结构：左侧一级分类、右侧所选一级分类的二级分类，支持添加 / 改名 / 删除 / 排序；
    改名会同步改写历史记录里的名称，删除分类不会删除已保存的数据
  - 利润公式：实时校验；下方为每个分类提供插入按钮（新增的分类立即出现）；离开输入框和保存时自动整理格式
  - 云同步：服务器已内置（读取程序旁的 ProfitHelper-sync.txt），这里只显示状态，不需要填写
  - 数据备份：导入 / 导出 family_ledger.db 兼容格式的完整账本
  - 底部固定操作栏：还原 / 保存设置；有未保存的修改时切换页面或关闭程序会先询问
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.event_bus import event_bus
from core.field_config import field_name_problem, flatten_formula_fields, load_field_tree_from_json, normalize_field_tree
from core.formula_engine import FormulaEngine, format_formula
from core.version import APP_VERSION
from ui import theme
from ui.widgets import Badge, Card, FlowLayout, IconLabel, label, make_button, page_header, page_scroll, set_tone


class FormulaEdit(QPlainTextEdit):
    """自动换行的单行公式编辑框：长公式也能完整显示，回车不会插入换行。"""

    editing_finished = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("FormulaInput")
        self.setTabChangesFocus(True)
        self.setFixedHeight(78)
        self.setPlaceholderText("营业额 - 成本")

    def text(self) -> str:
        return self.toPlainText().replace("\n", " ")

    def setText(self, text: str) -> None:  # noqa: N802 - 与 QLineEdit 保持一致
        self.setPlainText(text)

    def insert(self, text: str) -> None:
        self.textCursor().insertText(text)
        self.setFocus()

    def backspace(self) -> None:
        self.textCursor().deletePreviousChar()
        self.setFocus()

    def keyPressEvent(self, event):  # noqa: N802 - Qt API
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            self.editing_finished.emit()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):  # noqa: N802 - Qt API
        super().focusOutEvent(event)
        self.editing_finished.emit()

    def insertFromMimeData(self, source):  # noqa: N802 - Qt API
        self.insertPlainText(source.text().replace("\r", " ").replace("\n", " "))


def section_header(title: str, hint: str = "") -> QVBoxLayout:
    box = QVBoxLayout()
    box.setSpacing(2)
    box.addWidget(label(title, "SectionTitle"))
    if hint:
        box.addWidget(label(hint, "SectionHint", wrap=True))
    return box


class SettingsView(QWidget):
    """设置视图：自定义分级字段、利润公式、云同步状态与备份。"""

    def __init__(self, db, sync_manager, parent=None):
        super().__init__(parent)
        self._db = db
        self._sync = sync_manager
        self._engine = FormulaEngine()
        # 编辑中的分类树；orig / child_orig 记录载入时的原名，用于保存时把改名同步到历史记录
        self._tree: list[dict] = []
        self._saved_signature = ""
        self._build_ui()
        self._load_config()
        self._refresh_sync_status()
        QShortcut(QKeySequence.StandardKey.Save, self, activated=self._on_save)
        sync_manager.status_changed.connect(self._refresh_sync_status)
        event_bus.config_changed.connect(self._on_external_config_change)

    # ─────────────────────────────────────
    # UI 构建
    # ─────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        content = QWidget()
        outer.addWidget(page_scroll(content), 1)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        header, _, _ = page_header("设置", "分类和公式修改后点底部的“保存设置”（Ctrl+S），保存后会自动同步到手机")
        layout.addLayout(header)
        layout.addWidget(self._build_category_card())
        layout.addWidget(self._build_formula_card())
        layout.addWidget(self._build_sync_card())
        layout.addWidget(self._build_backup_card())
        layout.addStretch()

        bar = Card(object_name="ActionBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(28, 10, 28, 10)
        bar_layout.setSpacing(8)
        self._settings_badge = Badge()
        bar_layout.addWidget(self._settings_badge)
        self._bar_hint = label("分类和公式都已保存", "Hint")
        bar_layout.addWidget(self._bar_hint)
        bar_layout.addStretch()
        self._revert_btn = make_button("还原", "ghost")
        self._revert_btn.clicked.connect(self._load_config)
        bar_layout.addWidget(self._revert_btn)
        self._save_btn = make_button("保存设置", "primary", "save")
        self._save_btn.setMinimumWidth(116)
        self._save_btn.clicked.connect(self._on_save)
        bar_layout.addWidget(self._save_btn)
        outer.addWidget(bar)

    def _list_buttons(self, on_add, on_rename, on_delete, on_up, on_down, add_text: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        for text, variant, icon_name, handler, tip in [
            (add_text, "soft", "plus", on_add, ""),
            ("改名", "", "edit", on_rename, ""),
            ("删除", "danger", "trash", on_delete, ""),
            ("", "icon", "arrow-up", on_up, "上移"),
            ("", "icon", "arrow-down", on_down, "下移"),
        ]:
            button = make_button(text, variant, icon_name, tooltip=tip)
            button.clicked.connect(handler)
            row.addWidget(button)
        row.addStretch()
        return row

    def _build_category_card(self) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)
        layout.addLayout(section_header(
            "分类结构",
            "一级分类会自动汇总其下二级分类的金额；分类名都可以直接写进利润公式。"
            "改名会同步改写历史记录里的名称；删除分类不会删除已保存的数据。",
        ))

        columns = QHBoxLayout()
        columns.setSpacing(18)

        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(label("一级分类", "ListCaption"))
        self._parent_list = QListWidget()
        self._parent_list.setMinimumHeight(230)
        self._parent_list.currentRowChanged.connect(self._on_parent_selected)
        left.addWidget(self._parent_list, 1)
        left.addLayout(self._list_buttons(self._add_parent, self._rename_parent, self._delete_parent,
                                          lambda: self._move_parent(-1), lambda: self._move_parent(1), "添加一级"))
        columns.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(8)
        self._child_caption = label("二级分类", "ListCaption")
        right.addWidget(self._child_caption)
        self._child_list = QListWidget()
        self._child_list.setMinimumHeight(230)
        right.addWidget(self._child_list, 1)
        right.addLayout(self._list_buttons(self._add_child, self._rename_child, self._delete_child,
                                           lambda: self._move_child(-1), lambda: self._move_child(1), "添加二级"))
        columns.addLayout(right, 1)
        layout.addLayout(columns)
        return card

    def _build_formula_card(self) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addLayout(section_header(
            "利润公式",
            "支持 + − × ÷ 和括号，例如：营业额 - 成本 + 零钱，或 (现金 + 账户) * 0.9；离开输入框时会自动整理格式",
        ))

        self._formula_input = FormulaEdit()
        self._formula_input.textChanged.connect(self._on_formula_changed)
        self._formula_input.editing_finished.connect(self._tidy_formula)
        layout.addWidget(self._formula_input)

        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        self._formula_icon = IconLabel("check", theme.SUCCESS, 14)
        status_row.addWidget(self._formula_icon)
        self._formula_status = label("", "OkText", wrap=True)
        status_row.addWidget(self._formula_status, 1)
        layout.addLayout(status_row)

        ops = QHBoxLayout()
        ops.setSpacing(6)
        for text, insert in [("+", " + "), ("−", " - "), ("×", " * "), ("÷", " / "), ("(", "("), (")", ")")]:
            button = make_button(text, "op")
            button.clicked.connect(lambda _=False, value=insert: self._formula_input.insert(value))
            ops.addWidget(button)
        backspace = make_button("退格", "ghost")
        backspace.clicked.connect(self._formula_input.backspace)
        ops.addWidget(backspace)
        clear = make_button("清空", "ghost")
        clear.clicked.connect(self._formula_input.clear)
        ops.addWidget(clear)
        ops.addStretch()
        layout.addLayout(ops)

        layout.addWidget(label("点击插入分类（每个分类都有按钮，深色为一级分类的合计；新增分类后立即出现）", "Hint"))
        self._token_holder = QWidget()
        self._token_layout = FlowLayout(self._token_holder, 6, 8)
        layout.addWidget(self._token_holder)
        return card

    def _build_sync_card(self) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addLayout(section_header(
            "云同步",
            "同步服务器已内置，不需要填写。电脑与手机自动共用同一份账本；同一天两边都改过时以最后修改的为准，"
            "被覆盖的版本会保留在服务器上。",
        ))

        status = Card(object_name="SyncPanel")
        status_row = QHBoxLayout(status)
        status_row.setContentsMargins(14, 10, 10, 10)
        status_row.setSpacing(10)
        self._sync_icon = IconLabel("cloud-off", theme.TEXT_3, 20)
        status_row.addWidget(self._sync_icon)
        status_text = QVBoxLayout()
        status_text.setSpacing(0)
        self._sync_title = label("", "SyncTitle")
        status_text.addWidget(self._sync_title)
        self._sync_detail = label("", "SyncDetail", wrap=True)
        status_text.addWidget(self._sync_detail)
        status_row.addLayout(status_text, 1)
        self._sync_now_btn = make_button("立即同步", "soft", "sync")
        self._sync_now_btn.clicked.connect(self._sync.sync_now)
        status_row.addWidget(self._sync_now_btn)
        self._redetect_btn = make_button("重新检测", "soft", "sync")
        self._redetect_btn.setToolTip("把 ProfitHelper-sync.txt 放到程序所在文件夹后点这里")
        self._redetect_btn.clicked.connect(self._redetect_preset)
        status_row.addWidget(self._redetect_btn)
        layout.addWidget(status)

        self._auto_check = QCheckBox("自动同步（启动时、保存后、每分钟）")
        self._auto_check.setChecked(self._sync.auto_enabled)
        self._auto_check.toggled.connect(self._sync.set_auto_enabled)
        layout.addWidget(self._auto_check)
        return card

    def _build_backup_card(self) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addLayout(section_header(
            "数据备份",
            "导出会生成 family_ledger.db 格式的完整账本（不含同步密钥），可在电脑和手机之间互相导入；"
            "导入会替换当前所有历史记录和设置。",
        ))
        row = QHBoxLayout()
        row.setSpacing(8)
        import_btn = make_button("导入账本", "", "upload")
        import_btn.clicked.connect(self._on_import_database)
        row.addWidget(import_btn)
        export_btn = make_button("导出账本", "", "download")
        export_btn.clicked.connect(self._on_export_database)
        row.addWidget(export_btn)
        folder_btn = make_button("打开数据文件夹", "ghost", "folder")
        folder_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._db.data_directory())))
        )
        row.addWidget(folder_btn)
        row.addStretch()
        layout.addLayout(row)
        about = label(f"利润助手 v{APP_VERSION} · 数据文件：{self._db.db_path.resolve()}", "MonoValue", wrap=True)
        about.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(about)
        return card

    # ─────────────────────────────────────
    # 分类结构
    # ─────────────────────────────────────

    def _plain_tree(self) -> list[dict]:
        return normalize_field_tree([{"name": n["name"], "children": list(n["children"])} for n in self._tree])

    def _all_names(self) -> set[str]:
        return set(flatten_formula_fields(self._plain_tree()))

    def _selected_parent_index(self) -> int:
        return self._parent_list.currentRow()

    def _ask_name(self, title: str, prompt: str, current: str = "", exclude: str = "") -> str | None:
        existing = self._all_names() - {exclude}
        text = current
        while True:
            text, ok = QInputDialog.getText(self, title, prompt, QLineEdit.EchoMode.Normal, text)
            if not ok:
                return None
            name = unicodedata.normalize("NFKC", text).strip()  # 全角字母、数字转半角，和公式里的写法一致
            if name == exclude and exclude:
                return None
            problem = field_name_problem(name, existing)
            if not problem:
                return name
            QMessageBox.warning(self, "名称格式错误", problem)

    def _refresh_lists(self, parent_index: int | None = None, child_index: int | None = None) -> None:
        if parent_index is None:
            parent_index = max(0, self._selected_parent_index())
        self._parent_list.blockSignals(True)
        self._parent_list.clear()
        for item in self._tree:
            count = len(item["children"])
            self._parent_list.addItem(QListWidgetItem(
                f"{item['name']}    {count} 项" if count else f"{item['name']}    直接录入"
            ))
        self._parent_list.blockSignals(False)
        if self._tree:
            self._parent_list.setCurrentRow(min(parent_index, len(self._tree) - 1))
        self._on_parent_selected(self._parent_list.currentRow(), child_index)
        self._rebuild_tokens()
        self._on_formula_changed()

    def _on_parent_selected(self, index: int, child_index: int | None = None) -> None:
        self._child_list.clear()
        if not (0 <= index < len(self._tree)):
            self._child_caption.setText("二级分类")
            return
        parent = self._tree[index]
        self._child_caption.setText(f"「{parent['name']}」的二级分类")
        for child in parent["children"]:
            self._child_list.addItem(child)
        if parent["children"]:
            self._child_list.setCurrentRow(min(child_index or 0, len(parent["children"]) - 1))

    def _add_parent(self) -> None:
        name = self._ask_name("添加一级分类", "一级分类名称（例如：成本）：")
        if name:
            self._tree.append({"name": name, "children": [], "orig": None, "child_orig": []})
            self._refresh_lists(len(self._tree) - 1)

    def _rename_parent(self) -> None:
        index = self._selected_parent_index()
        if not (0 <= index < len(self._tree)):
            return
        old = self._tree[index]["name"]
        name = self._ask_name("修改一级分类", "新的名称（历史记录和公式里的这一项会一起改名）：", old, exclude=old)
        if name:
            self._tree[index]["name"] = name
            self._rename_in_formula(old, name)
            self._refresh_lists(index)

    def _delete_parent(self) -> None:
        index = self._selected_parent_index()
        if not (0 <= index < len(self._tree)):
            return
        item = self._tree[index]
        extra = f"\n它下面的 {len(item['children'])} 个二级分类也会一起删除。" if item["children"] else ""
        if QMessageBox.question(self, "删除一级分类", f"确定删除「{item['name']}」吗？{extra}\n已保存的历史数据不会被删除。") \
                != QMessageBox.StandardButton.Yes:
            return
        self._tree.pop(index)
        self._refresh_lists(max(0, index - 1))

    def _move_parent(self, step: int) -> None:
        index = self._selected_parent_index()
        target = index + step
        if 0 <= index < len(self._tree) and 0 <= target < len(self._tree):
            self._tree[index], self._tree[target] = self._tree[target], self._tree[index]
            self._refresh_lists(target)

    def _add_child(self) -> None:
        index = self._selected_parent_index()
        if not (0 <= index < len(self._tree)):
            QMessageBox.information(self, "请选择分类", "请先在左侧选中一个一级分类。")
            return
        parent = self._tree[index]
        name = self._ask_name("添加二级分类", f"添加到「{parent['name']}」下的二级分类名称：")
        if name:
            parent["children"].append(name)
            parent["child_orig"].append(None)
            self._refresh_lists(index, len(parent["children"]) - 1)

    def _selected_child(self) -> tuple[int, int] | None:
        index = self._selected_parent_index()
        child_index = self._child_list.currentRow()
        if 0 <= index < len(self._tree) and 0 <= child_index < len(self._tree[index]["children"]):
            return index, child_index
        return None

    def _rename_child(self) -> None:
        selected = self._selected_child()
        if selected is None:
            return
        index, child_index = selected
        old = self._tree[index]["children"][child_index]
        name = self._ask_name("修改二级分类", "新的名称（历史记录和公式里的这一项会一起改名）：", old, exclude=old)
        if name:
            self._tree[index]["children"][child_index] = name
            self._rename_in_formula(old, name)
            self._refresh_lists(index, child_index)

    def _delete_child(self) -> None:
        selected = self._selected_child()
        if selected is None:
            return
        index, child_index = selected
        name = self._tree[index]["children"][child_index]
        if QMessageBox.question(self, "删除二级分类", f"确定删除「{name}」吗？\n已保存的历史数据不会被删除。") \
                != QMessageBox.StandardButton.Yes:
            return
        self._tree[index]["children"].pop(child_index)
        self._tree[index]["child_orig"].pop(child_index)
        self._refresh_lists(index, max(0, child_index - 1))

    def _move_child(self, step: int) -> None:
        selected = self._selected_child()
        if selected is None:
            return
        index, child_index = selected
        target = child_index + step
        node = self._tree[index]
        if 0 <= target < len(node["children"]):
            for key in ("children", "child_orig"):
                node[key][child_index], node[key][target] = node[key][target], node[key][child_index]
            self._refresh_lists(index, target)

    def _rename_in_formula(self, old: str, new: str) -> None:
        formula = self._formula_input.text()
        updated = re.sub(rf"(?<!\w){re.escape(old)}(?!\w)", new, formula)
        if updated != formula:
            self._formula_input.setText(updated)
            event_bus.toast_requested.emit(f"公式中的「{old}」已同步改为「{new}」", "info")

    def _renames(self) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
        parent_renames = {n["orig"]: n["name"] for n in self._tree if n["orig"] and n["orig"] != n["name"]}
        child_renames: dict[str, dict[str, str]] = {}
        for node in self._tree:
            mapping = {old: new for old, new in zip(node["child_orig"], node["children"]) if old and old != new}
            if mapping:
                child_renames[node["name"]] = mapping
        return parent_renames, child_renames

    # ─────────────────────────────────────
    # 公式
    # ─────────────────────────────────────

    def _rebuild_tokens(self) -> None:
        self._token_layout.clear()
        for item in self._tree:
            parent_btn = make_button(item["name"] + (" 合计" if item["children"] else ""),
                                     "token-total" if item["children"] else "token")
            parent_btn.clicked.connect(lambda _=False, n=item["name"]: self._formula_input.insert(n))
            self._token_layout.addWidget(parent_btn)
            for child in item["children"]:
                child_btn = make_button(child, "token")
                child_btn.clicked.connect(lambda _=False, n=child: self._formula_input.insert(n))
                self._token_layout.addWidget(child_btn)
        self._token_holder.updateGeometry()

    def _formula_problem(self, formula: str | None = None, tree: list[dict] | None = None) -> tuple[str, str]:
        """返回 (语气, 说明)，语气为 ok / warn / error。"""
        formula = (self._formula_input.text() if formula is None else formula).strip()
        names = {unicodedata.normalize("NFKC", n) for n in flatten_formula_fields(self._plain_tree() if tree is None else tree)}
        if not formula:
            return "error", "公式不能为空"
        ok, message = self._engine.validate(formula)
        if not ok:
            return "error", message
        used = list(dict.fromkeys(self._engine.extract_variables(formula)))
        undefined = [name for name in used if name not in names]
        if undefined:
            return "warn", f"引用了不存在的分类：{'、'.join(undefined)}，请先添加分类或修改公式"
        return "ok", f"公式有效，引用了 {len(used)} 个分类"

    def _on_formula_changed(self) -> None:
        tone, message = self._formula_problem()
        self._formula_status.setText(message)
        self._formula_status.setObjectName({"ok": "OkText", "warn": "WarnText", "error": "ErrorText"}[tone])
        theme.repolish(self._formula_status)
        icon_name, color = {"ok": ("check", theme.SUCCESS), "warn": ("alert", theme.WARNING),
                            "error": ("alert", theme.DANGER)}[tone]
        self._formula_icon.set_icon(icon_name, color)
        self._formula_input.setProperty("invalid", tone == "error" and bool(self._formula_input.text().strip()))
        theme.repolish(self._formula_input)
        self._update_dirty_badge()

    def _tidy_formula(self) -> None:
        """离开公式框时自动整理格式（全角符号、空格），公式有误时保持原样等待修改。"""
        text = self._formula_input.text()
        tidy = format_formula(text)
        if tidy != text and self._engine.validate(tidy)[0]:
            self._formula_input.setText(tidy)

    # ─────────────────────────────────────
    # 读取 / 保存
    # ─────────────────────────────────────

    def _signature(self) -> str:
        # 与 DatabaseManager.settings_signature 相同的写法：只整理了公式格式不算修改
        return json.dumps([self._plain_tree(), format_formula(self._formula_input.text())], ensure_ascii=False)

    def is_dirty(self) -> bool:
        return self._signature() != self._saved_signature

    def _update_dirty_badge(self) -> None:
        dirty = self.is_dirty()
        self._settings_badge.set("有未保存的修改" if dirty else "", "warning")
        self._bar_hint.setVisible(not dirty)
        self._revert_btn.setEnabled(dirty)

    def _load_config(self) -> None:
        field_tree_json = self._db.get_config("custom_field_tree", "") or self._db.get_config("custom_fields", "")
        self._tree = [
            {"name": n["name"], "children": list(n["children"]), "orig": n["name"], "child_orig": list(n["children"])}
            for n in load_field_tree_from_json(field_tree_json)
        ]
        self._formula_input.blockSignals(True)
        self._formula_input.setText(format_formula(self._db.get_config("profit_formula", "")))
        self._formula_input.blockSignals(False)
        self._saved_signature = self._db.settings_signature()
        self._refresh_lists(0)

    def _on_external_config_change(self) -> None:
        if not self.is_dirty():
            self._load_config()

    def _on_save(self) -> bool:
        tree = self._plain_tree()
        if not tree:
            QMessageBox.warning(self, "分类不能为空", "请至少添加一个一级分类。")
            return False
        formula = format_formula(self._formula_input.text())
        tone, message = self._formula_problem(formula, tree)
        if tone != "ok":
            QMessageBox.warning(self, "公式格式错误", f"{message}\n\n请修改后再保存。")
            self._formula_input.setFocus()
            return False
        if self._db.settings_signature() != self._saved_signature and not self._confirm_overwrite_remote():
            return False

        parent_renames, child_renames = self._renames()
        migrated = self._db.save_settings(tree, formula, parent_renames, child_renames)
        self._load_config()
        event_bus.config_changed.emit()
        if migrated:
            event_bus.data_changed.emit()
        extra = f"，{migrated} 天的历史记录已同步改名" if migrated else ""
        event_bus.toast_requested.emit(f"设置已保存{extra}，会自动同步到手机", "success")
        return True

    def _confirm_overwrite_remote(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("设置刚被其他设备修改过")
        box.setText("在你编辑期间，分类或公式已通过云同步更新。\n继续保存会用你现在的设置覆盖它（被覆盖的版本仍保留在服务器上）。")
        overwrite = box.addButton("仍然保存", QMessageBox.ButtonRole.AcceptRole)
        reload_latest = box.addButton("载入最新设置", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(reload_latest)
        box.exec()
        if box.clickedButton() is reload_latest:
            self._load_config()
        return box.clickedButton() is overwrite

    def confirm_leave(self) -> bool:
        """有未保存的设置时询问。返回 True 表示可以离开设置页。"""
        if not self.is_dirty():
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("设置尚未保存")
        box.setText("分类或公式的修改还没有保存。")
        save = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("不保存", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.exec()
        if box.clickedButton() is save:
            return self._on_save()
        if box.clickedButton() is discard:
            self._load_config()
            return True
        return False

    # ─────────────────────────────────────
    # 云同步状态
    # ─────────────────────────────────────

    def _refresh_sync_status(self) -> None:
        title, detail, tone = self._sync.status_view()
        self._sync_title.setText(title)
        set_tone(self._sync_title, tone)
        self._sync_detail.setText(detail)
        icon_name, color = {"ok": ("cloud-check", theme.SUCCESS), "error": ("alert", theme.DANGER),
                            "busy": ("sync", theme.ACCENT)}.get(tone, ("cloud-off", theme.TEXT_3))
        self._sync_icon.set_icon(icon_name, color)
        configured = self._sync.configured
        self._sync_now_btn.setVisible(configured)
        self._sync_now_btn.setEnabled(tone != "busy")
        self._redetect_btn.setVisible(self._sync.needs_preset)  # 缺少配置文件或密钥已失效
        self._auto_check.setEnabled(configured)

    def _redetect_preset(self) -> None:
        if self._sync.reload_preset():
            event_bus.toast_requested.emit("已读取内置同步配置，正在同步…", "success")
        else:
            QMessageBox.information(
                self,
                "没有找到同步配置",
                f"请把 ProfitHelper-sync.txt 放到程序所在的文件夹：\n{self._db.data_directory()}\n\n然后再点“重新检测”。",
            )

    # ─────────────────────────────────────
    # 导入 / 导出
    # ─────────────────────────────────────

    def _on_export_database(self) -> None:
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出账本",
            str(Path.home() / "family_ledger.db"),
            "SQLite 数据库 (*.db)",
        )
        if not save_path:
            return

        try:
            exported_path = self._db.export_database(save_path)
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", f"导出过程中发生错误：\n{exc}")
            return

        event_bus.toast_requested.emit(f"账本已导出：{Path(exported_path).name}", "success")

    def _on_import_database(self) -> None:
        import_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入账本",
            str(Path.home()),
            "SQLite 数据库 (*.db)",
        )
        if not import_path:
            return

        sync_note = "\n开启了云同步，导入结果也会同步到手机。" if self._sync.configured else ""
        reply = QMessageBox.question(
            self,
            "确认导入",
            f"导入后会替换当前所有历史记录和设置。{sync_note}\n确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            result = self._db.import_database(import_path)
        except Exception as exc:
            QMessageBox.critical(self, "导入文件格式错误", f"导入文件不符合账本格式，或读取失败：\n{exc}")
            return

        self._load_config()
        event_bus.config_changed.emit()
        event_bus.data_changed.emit()
        QMessageBox.information(
            self,
            "导入成功",
            f"已导入 {result['records']} 条历史记录、{result['configs']} 项设置。",
        )

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self.is_dirty():
            self._load_config()
        self._refresh_sync_status()
        super().showEvent(event)
