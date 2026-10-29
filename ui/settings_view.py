"""
ui/settings_view.py
设置页 — 分类结构、利润公式、云同步、数据备份。

  - 分类结构：左侧一级分类、右侧所选一级分类的二级分类，支持添加 / 改名 / 删除 / 排序
  - 利润公式：实时校验，点击运算符和分类卡片即可插入
  - 云同步：粘贴连接码或手动填写服务器地址与同步密钥，测试连接、立即同步、复制连接码给手机
  - 数据备份：导入 / 导出 family_ledger.db 兼容格式的完整账本
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
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
from core.field_config import (
    field_name_problem,
    flatten_formula_fields,
    load_field_tree_from_json,
    normalize_field_tree,
)
from core.formula_engine import FormulaEngine, normalize_formula
from core.sync_client import SyncConfig, SyncError, make_connection_code, normalize_server_url, parse_connection_code
from core.version import APP_VERSION
from ui import icons, theme
from ui.widgets import Badge, Card, FlowLayout, IconLabel, label, make_button, page_header, page_scroll, set_tone


class _BackgroundCall(QObject):
    """在后台线程执行函数，并把结果送回界面线程。"""

    done = Signal(object, object)

    def run(self, func) -> None:
        def worker():
            try:
                self.done.emit(func(), None)
            except Exception as exc:  # 结果统一交给界面线程处理
                self.done.emit(None, exc)

        threading.Thread(target=worker, daemon=True).start()


class FormulaEdit(QPlainTextEdit):
    """自动换行的单行公式编辑框：长公式也能完整显示，回车不会插入换行。"""

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
            return
        super().keyPressEvent(event)

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
    """设置视图：自定义分级字段、利润公式、云同步与备份。"""

    def __init__(self, db, sync_manager, parent=None):
        super().__init__(parent)
        self._db = db
        self._sync = sync_manager
        self._engine = FormulaEngine()
        self._tree: list[dict] = []
        self._saved_signature = ""
        self._test_call = _BackgroundCall(self)
        self._test_call.done.connect(self._on_test_done)
        self._build_ui()
        self._load_config()
        self._load_sync_fields()
        sync_manager.status_changed.connect(self._refresh_sync_status)
        event_bus.config_changed.connect(self._on_external_config_change)

    # ─────────────────────────────────────
    # UI 构建
    # ─────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        outer.addWidget(page_scroll(content))
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 24, 28, 32)
        layout.setSpacing(16)

        header, _, actions = page_header("设置", "分类和公式修改后需要点“保存设置”；开启云同步后会自动同步到其他设备")
        self._settings_badge = Badge()
        actions.addWidget(self._settings_badge)
        self._revert_btn = make_button("还原", "ghost")
        self._revert_btn.clicked.connect(self._load_config)
        actions.addWidget(self._revert_btn)
        save_btn = make_button("保存设置", "primary", "save")
        save_btn.clicked.connect(self._on_save)
        actions.addWidget(save_btn)
        layout.addLayout(header)

        layout.addWidget(self._build_category_card())
        layout.addWidget(self._build_formula_card())
        layout.addWidget(self._build_sync_card())
        layout.addWidget(self._build_backup_card())
        layout.addStretch()

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
            "一级分类会自动汇总其下二级分类的金额；一级和二级分类名都可以直接写进利润公式。"
            "改名不会改动已保存的历史数据。",
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
            "支持 + − × ÷ 和括号，例如：营业额 - 成本 + 零钱，或 (现金 + 账户) * 0.9",
        ))

        self._formula_input = FormulaEdit()
        self._formula_input.textChanged.connect(self._on_formula_changed)
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
            button.clicked.connect(lambda _=False, value=insert: self._insert_formula_text(value))
            ops.addWidget(button)
        backspace = make_button("退格", "ghost")
        backspace.clicked.connect(self._on_backspace)
        ops.addWidget(backspace)
        clear = make_button("清空", "ghost")
        clear.clicked.connect(self._formula_input.clear)
        ops.addWidget(clear)
        ops.addStretch()
        layout.addLayout(ops)

        layout.addWidget(label("点击插入分类（深色为一级分类的合计）", "Hint"))
        self._token_holder = QWidget()
        self._token_layout = FlowLayout(self._token_holder, 6, 8)
        layout.addWidget(self._token_holder)
        return card

    def _build_sync_card(self) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        head = QHBoxLayout()
        head.addLayout(section_header(
            "云同步",
            "Win10 电脑与安卓手机共用同一份账本。同一天在两台设备上都改过时，以最后修改的为准，"
            "被覆盖的版本会保留在服务器上。",
        ), 1)
        layout.addLayout(head)

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
        layout.addWidget(status)

        code_row = QHBoxLayout()
        code_row.setSpacing(8)
        self._code_input = QLineEdit()
        self._code_input.setPlaceholderText("粘贴 PH1- 开头的连接码")
        self._code_input.returnPressed.connect(self._apply_connection_code)
        code_row.addWidget(self._code_input, 1)
        apply_code = make_button("识别连接码", "", "link")
        apply_code.clicked.connect(self._apply_connection_code)
        code_row.addWidget(apply_code)
        layout.addLayout(code_row)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        save_sync = make_button("保存并同步", "primary", "cloud-check")
        save_sync.clicked.connect(self._save_and_sync)
        buttons.addWidget(save_sync)
        self._test_btn = make_button("测试连接", "", "link")
        self._test_btn.clicked.connect(self._test_connection)
        buttons.addWidget(self._test_btn)
        copy_code = make_button("复制连接码给手机", "", "copy")
        copy_code.clicked.connect(self._copy_connection_code)
        buttons.addWidget(copy_code)
        buttons.addStretch()
        self._auto_check = QCheckBox("自动同步")
        self._auto_check.setToolTip("启动时、保存后、每 5 分钟自动同步")
        self._auto_check.toggled.connect(self._sync.set_auto_enabled)
        buttons.addWidget(self._auto_check)
        disconnect = make_button("断开", "danger")
        disconnect.clicked.connect(self._disconnect_sync)
        buttons.addWidget(disconnect)
        layout.addLayout(buttons)

        self._test_result = label("", "Hint", wrap=True)
        self._test_result.setVisible(False)
        layout.addWidget(self._test_result)

        self._manual_toggle = make_button("手动填写服务器信息", "ghost", "chevron-right")
        self._manual_toggle.setCheckable(True)
        self._manual_toggle.toggled.connect(self._toggle_manual_fields)
        layout.addWidget(self._manual_toggle, alignment=Qt.AlignmentFlag.AlignLeft)

        self._manual_fields = QWidget()
        form = QFormLayout(self._manual_fields)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._server_input = QLineEdit()
        self._server_input.setPlaceholderText("https://example.com:28443")
        form.addRow("服务器地址", self._server_input)

        token_row = QHBoxLayout()
        token_row.setSpacing(6)
        self._token_input = QLineEdit()
        self._token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._token_input.setPlaceholderText("PH-XXXX-XXXX-…")
        token_row.addWidget(self._token_input, 1)
        self._reveal_btn = make_button("", "icon", "eye", tooltip="显示 / 隐藏密钥")
        self._reveal_btn.setCheckable(True)
        self._reveal_btn.toggled.connect(self._toggle_token_visible)
        token_row.addWidget(self._reveal_btn)
        token_holder = QWidget()
        token_holder.setLayout(token_row)
        token_row.setContentsMargins(0, 0, 0, 0)
        form.addRow("同步密钥", token_holder)

        self._ip_input = QLineEdit()
        self._ip_input.setPlaceholderText("可选：域名解析失败时直接连接此 IP")
        form.addRow("备用 IP", self._ip_input)
        self._device_input = QLineEdit()
        self._device_input.setPlaceholderText("在服务器上显示的设备名称")
        form.addRow("本机名称", self._device_input)
        self._manual_fields.setVisible(False)
        layout.addWidget(self._manual_fields)
        return card

    def _show_result(self, text: str, tone: str = "Hint") -> None:
        self._test_result.setObjectName(tone)
        theme.repolish(self._test_result)
        self._test_result.setText(text)
        self._test_result.setVisible(bool(text))

    def _toggle_manual_fields(self, visible: bool) -> None:
        self._manual_fields.setVisible(visible)
        icons.set_button_icon(self._manual_toggle, "chevron-down" if visible else "chevron-right", theme.TEXT_2, 16)

    def _build_backup_card(self) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addLayout(section_header(
            "数据备份",
            "导出会生成 family_ledger.db 格式的完整账本（不含同步密钥），可在 Win10 和安卓端互相导入；"
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

    def _all_names(self) -> set[str]:
        return set(flatten_formula_fields(normalize_field_tree(self._tree)))

    def _selected_parent_index(self) -> int:
        return self._parent_list.currentRow()

    def _ask_name(self, title: str, prompt: str, current: str = "", exclude: str = "") -> str | None:
        existing = self._all_names() - {exclude}
        text = current
        while True:
            text, ok = QInputDialog.getText(self, title, prompt, QLineEdit.EchoMode.Normal, text)
            if not ok:
                return None
            name = text.strip()
            if name == exclude and exclude:
                return None
            problem = field_name_problem(name, existing)
            if not problem:
                return name
            QMessageBox.warning(self, "名称不可用", problem)

    def _refresh_lists(self, parent_index: int | None = None, child_index: int | None = None) -> None:
        if parent_index is None:
            parent_index = max(0, self._selected_parent_index())
        self._parent_list.blockSignals(True)
        self._parent_list.clear()
        for item in self._tree:
            count = len(item["children"])
            entry = QListWidgetItem(f"{item['name']}    {count} 项" if count else f"{item['name']}    直接录入")
            self._parent_list.addItem(entry)
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
            self._tree.append({"name": name, "children": []})
            self._refresh_lists(len(self._tree) - 1)

    def _rename_parent(self) -> None:
        index = self._selected_parent_index()
        if not (0 <= index < len(self._tree)):
            return
        old = self._tree[index]["name"]
        name = self._ask_name("修改一级分类", "新的名称：", old, exclude=old)
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
            self._refresh_lists(index, len(parent["children"]) - 1)

    def _rename_child(self) -> None:
        index = self._selected_parent_index()
        child_index = self._child_list.currentRow()
        if not (0 <= index < len(self._tree)) or not (0 <= child_index < len(self._tree[index]["children"])):
            return
        old = self._tree[index]["children"][child_index]
        name = self._ask_name("修改二级分类", "新的名称：", old, exclude=old)
        if name:
            self._tree[index]["children"][child_index] = name
            self._rename_in_formula(old, name)
            self._refresh_lists(index, child_index)

    def _delete_child(self) -> None:
        index = self._selected_parent_index()
        child_index = self._child_list.currentRow()
        if not (0 <= index < len(self._tree)) or not (0 <= child_index < len(self._tree[index]["children"])):
            return
        self._tree[index]["children"].pop(child_index)
        self._refresh_lists(index, max(0, child_index - 1))

    def _move_child(self, step: int) -> None:
        index = self._selected_parent_index()
        child_index = self._child_list.currentRow()
        if not (0 <= index < len(self._tree)):
            return
        children = self._tree[index]["children"]
        target = child_index + step
        if 0 <= child_index < len(children) and 0 <= target < len(children):
            children[child_index], children[target] = children[target], children[child_index]
            self._refresh_lists(index, target)

    def _rename_in_formula(self, old: str, new: str) -> None:
        formula = self._formula_input.text()
        updated = re.sub(rf"(?<!\w){re.escape(old)}(?!\w)", new, formula)
        if updated != formula:
            self._formula_input.setText(updated)
            event_bus.toast_requested.emit(f"公式中的「{old}」已同步改为「{new}」", "info")

    # ─────────────────────────────────────
    # 公式
    # ─────────────────────────────────────

    def _rebuild_tokens(self) -> None:
        self._token_layout.clear()
        for item in self._tree:
            parent_btn = make_button(item["name"] + (" 合计" if item["children"] else ""),
                                     "token-total" if item["children"] else "token")
            parent_btn.clicked.connect(lambda _=False, n=item["name"]: self._insert_formula_text(n))
            self._token_layout.addWidget(parent_btn)
            for child in item["children"]:
                child_btn = make_button(child, "token")
                child_btn.clicked.connect(lambda _=False, n=child: self._insert_formula_text(n))
                self._token_layout.addWidget(child_btn)
        self._token_holder.updateGeometry()

    def _insert_formula_text(self, text: str) -> None:
        self._formula_input.insert(text)

    def _on_backspace(self) -> None:
        self._formula_input.backspace()

    def _formula_problem(self) -> tuple[str, str]:
        """返回 (语气, 说明)，语气为 ok / warn / error。"""
        formula = self._formula_input.text().strip()
        if not formula:
            return "error", "公式不能为空"
        ok, message = self._engine.validate(formula)
        if not ok:
            return "error", message
        used = list(dict.fromkeys(self._engine.extract_variables(formula)))
        undefined = [name for name in used if name not in self._all_names()]
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

    # ─────────────────────────────────────
    # 读取 / 保存
    # ─────────────────────────────────────

    def _signature(self) -> str:
        return json.dumps([normalize_field_tree(self._tree), self._formula_input.text().strip()], ensure_ascii=False)

    def _update_dirty_badge(self) -> None:
        dirty = self._signature() != self._saved_signature
        self._settings_badge.set("有未保存的修改" if dirty else "", "warning")
        self._revert_btn.setVisible(dirty)

    def _load_config(self) -> None:
        field_tree_json = self._db.get_config("custom_field_tree", "")
        if not field_tree_json:
            field_tree_json = self._db.get_config("custom_fields", '["营业额", "成本"]')
        self._tree = load_field_tree_from_json(field_tree_json)
        self._formula_input.blockSignals(True)
        self._formula_input.setText(self._db.get_config("profit_formula", "营业额 - 成本"))
        self._formula_input.blockSignals(False)
        self._saved_signature = self._signature()
        self._refresh_lists(0)

    def _on_external_config_change(self) -> None:
        if self._signature() == self._saved_signature:
            self._load_config()

    def _on_save(self) -> None:
        field_tree = normalize_field_tree(self._tree)
        fields = flatten_formula_fields(field_tree)
        formula = " ".join(normalize_formula(self._formula_input.text()).split())

        if not fields:
            QMessageBox.warning(self, "配置错误", "请至少添加一个一级分类。")
            return
        tone, message = self._formula_problem()
        if tone != "ok":
            QMessageBox.warning(self, "公式需要修改", message)
            return

        self._db.set_configs({
            "custom_field_tree": json.dumps(field_tree, ensure_ascii=False),
            "custom_fields": json.dumps(fields, ensure_ascii=False),
            "profit_formula": formula,
        })
        self._tree = field_tree
        self._formula_input.blockSignals(True)
        self._formula_input.setText(formula)
        self._formula_input.blockSignals(False)
        self._saved_signature = self._signature()
        self._update_dirty_badge()
        event_bus.config_changed.emit()
        event_bus.toast_requested.emit("设置已保存，录入页和导出报表会按新分类展示", "success")

    # ─────────────────────────────────────
    # 云同步
    # ─────────────────────────────────────

    def _load_sync_fields(self) -> None:
        config = self._sync.engine.load_config()
        self._server_input.setText(config.server_url)
        self._token_input.setText(config.token)
        self._ip_input.setText(config.fallback_ip)
        self._device_input.setText(self._sync.engine.device_name())
        self._auto_check.blockSignals(True)
        self._auto_check.setChecked(self._sync.auto_enabled)
        self._auto_check.blockSignals(False)
        self._refresh_sync_status()

    def _refresh_sync_status(self) -> None:
        title, detail, tone = self._sync.status_view()
        if tone == "off":
            detail = "粘贴连接码后点“保存并同步”即可开启"
        self._sync_title.setText(title)
        set_tone(self._sync_title, tone)
        self._sync_detail.setText(detail)
        icon_name, color = {"ok": ("cloud-check", theme.SUCCESS), "error": ("alert", theme.DANGER),
                            "busy": ("sync", theme.ACCENT)}.get(tone, ("cloud-off", theme.TEXT_3))
        self._sync_icon.set_icon(icon_name, color)
        self._sync_now_btn.setEnabled(self._sync.configured and tone != "busy")

    def _toggle_token_visible(self, visible: bool) -> None:
        self._token_input.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        icons.set_button_icon(self._reveal_btn, "eye-off" if visible else "eye", theme.TEXT_2, 16)

    def _apply_connection_code(self) -> None:
        try:
            config = parse_connection_code(self._code_input.text())
        except SyncError as exc:
            QMessageBox.warning(self, "无法识别", str(exc))
            return
        self._server_input.setText(config.server_url)
        self._token_input.setText(config.token)
        self._ip_input.setText(config.fallback_ip)
        self._code_input.clear()
        self._show_result(f"已识别：{config.server_url}。点击“保存并同步”即可开始。", "OkText")

    def _form_config(self) -> SyncConfig | None:
        try:
            url = normalize_server_url(self._server_input.text())
        except SyncError as exc:
            QMessageBox.warning(self, "服务器地址", str(exc))
            return None
        token = self._token_input.text().strip()
        if not url or not token:
            QMessageBox.warning(self, "信息不完整", "请先粘贴连接码并点“识别连接码”，或在“手动填写服务器信息”中填写。")
            return None
        return SyncConfig(server_url=url, token=token, fallback_ip=self._ip_input.text().strip())

    def _test_connection(self) -> None:
        config = self._form_config()
        if config is None:
            return
        self._test_btn.setEnabled(False)
        self._show_result("正在连接服务器…")
        self._test_call.run(lambda: self._sync.engine.test_connection(config))

    def _on_test_done(self, status, error) -> None:
        self._test_btn.setEnabled(True)
        if error is not None:
            self._show_result(f"连接失败：{error}", "ErrorText")
        else:
            devices = "、".join(d["name"] for d in status.get("devices", [])[:4] if d.get("name")) or "暂无"
            self._show_result(
                f"连接成功：服务器上有 {status.get('record_count', 0)} 天记录，最近同步过的设备：{devices}", "OkText"
            )

    def _save_and_sync(self) -> None:
        config = self._form_config()
        if config is None:
            return
        first_time = not self._db.get_sync_meta("last_sync_at")
        local_count = len(self._db.get_all_records())
        if first_time and local_count:
            reply = QMessageBox.question(
                self,
                "首次同步",
                f"本机有 {local_count} 天记录，将与服务器上的账本合并：\n"
                "· 服务器上没有的日期会上传；\n"
                "· 同一天两边都有数据时，以最后修改的为准；\n"
                "· 同步前会自动在数据文件旁备份一份（family_ledger.before-sync-*.db）。\n\n确定开始同步吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._sync.save_config(config, self._device_input.text())
        self._sync.sync_now()
        self._show_result("已保存，正在同步…")

    def _copy_connection_code(self) -> None:
        config = self._form_config()
        if config is None:
            return
        QGuiApplication.clipboard().setText(make_connection_code(config.server_url, config.token, config.fallback_ip))
        QMessageBox.information(
            self,
            "连接码已复制",
            "连接码已复制到剪贴板。\n\n可以通过微信“文件传输助手”发到自己的手机，"
            "在安卓端“设置 → 云同步”中粘贴即可。\n连接码包含同步密钥，请不要发给其他人。",
        )

    def _disconnect_sync(self) -> None:
        if not self._sync.configured:
            return
        reply = QMessageBox.question(
            self,
            "断开云同步",
            "断开后本机不再与服务器同步，本机数据会保留。\n以后重新连接时会再次合并数据。确定断开吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._sync.disconnect()
            self._load_sync_fields()
            self._show_result("")

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

        sync_note = "\n开启了云同步，导入结果也会同步到其他设备。" if self._sync.configured else ""
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
            QMessageBox.critical(self, "导入失败", f"导入文件不符合账本格式，或读取失败：\n{exc}")
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
        if self._signature() == self._saved_signature:
            self._load_config()
        self._refresh_sync_status()
        super().showEvent(event)
