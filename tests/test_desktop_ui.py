"""
tests/test_desktop_ui.py
桌面端交互：新增分类后插入按钮立即出现、公式自动整理格式、格式错误弹窗、改名同步改写历史记录、
设置被同步修改时不静默覆盖、离开设置页前确认、录入保存保留看不到的数据、可以清空备注、内置配置“重新检测”。

需要 PySide6（CI 与本地 .venv 中均已安装）；缺少时自动跳过。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import core.sync_preset as preset  # noqa: E402
from core.database import DatabaseManager  # noqa: E402
from core.event_bus import event_bus  # noqa: E402
from core.sync_client import make_connection_code  # noqa: E402

try:
    from PySide6.QtCore import QDate, QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication, QDateEdit, QInputDialog, QMessageBox, QPushButton, QScrollArea
except ImportError:  # pragma: no cover - 没有 PySide6 的环境只跑核心测试
    QApplication = None


class FakeDialogs:
    """替换会阻塞测试的对话框：按预设答案返回，并记录弹出的内容。"""

    def __init__(self, names=(), question=None, choice=None):
        self.names = list(names)          # QInputDialog.getText 依次返回的名称
        self.question = question          # QMessageBox.question 的返回值
        self.choice = choice              # 自定义 QMessageBox 上点击的按钮文字（None = 关闭对话框）
        self.warnings: list[tuple[str, str]] = []
        self.boxes: list[tuple[str, str]] = []

    def __enter__(self):
        self._saved = (QInputDialog.getText, QMessageBox.question, QMessageBox.warning,
                       QMessageBox.information, QMessageBox.exec)
        yes = QMessageBox.StandardButton.Yes

        def get_text(*_args, **_kwargs):
            return (self.names.pop(0), True) if self.names else ("", False)

        def warning(_parent, title, text, *_args, **_kwargs):
            self.warnings.append((title, text))
            return QMessageBox.StandardButton.Ok

        def exec_box(box):
            self.boxes.append((box.windowTitle(), box.text()))
            for button in box.buttons():
                if button.text() == self.choice:
                    button.click()
            return 0

        QInputDialog.getText = get_text
        QMessageBox.question = lambda *_a, **_k: self.question or yes
        QMessageBox.warning = warning
        QMessageBox.information = warning
        QMessageBox.exec = exec_box
        return self

    def __exit__(self, *_exc):
        (QInputDialog.getText, QMessageBox.question, QMessageBox.warning,
         QMessageBox.information, QMessageBox.exec) = self._saved


@unittest.skipIf(QApplication is None, "需要 PySide6")
class DesktopUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="desk-ui-"))
        self.original_path = preset.data_file_path
        preset.data_file_path = lambda name: self.tmp / name  # 不读取开发目录里可能存在的真实配置
        self.db = DatabaseManager(self.tmp / "family_ledger.db")
        self.widgets = []
        self.server = None

    def tearDown(self):
        for widget in self.widgets:
            widget.hide()  # 不触发 closeEvent：主窗口关闭时会询问未保存的修改并关闭数据库
            widget.deleteLater()
        # 立即销毁，断开它们与全局 event_bus 的连接，避免影响后面的测试
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if self.manager_created():
            self.manager.shutdown(timeout=5)
        self.db.close()
        if self.server is not None:
            self.server.stop()
        preset.data_file_path = self.original_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── 辅助 ──────────────────────────────────

    def manager_created(self) -> bool:
        return hasattr(self, "manager")

    def settings_view(self):
        from ui.settings_view import SettingsView
        from ui.sync_manager import SyncManager

        self.manager = SyncManager(self.db, "test")
        view = SettingsView(self.db, self.manager)
        view.resize(1100, 900)
        view.show()
        self.widgets.append(view)
        self.pump()
        return view

    def entry_view(self):
        from ui.entry_view import EntryView

        view = EntryView(self.db)
        view.resize(1100, 800)
        view.show()
        self.widgets.append(view)
        self.pump()
        return view

    def pump(self, rounds: int = 5) -> None:
        for _ in range(rounds):
            self.app.processEvents()

    def wait(self, condition, timeout: float = 10.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.app.processEvents()
            if condition():
                return True
            time.sleep(0.02)
        return False

    @staticmethod
    def token_buttons(view) -> dict[str, QPushButton]:
        return {button.text(): button for button in view._token_holder.findChildren(QPushButton)
                if not button.isHidden()}

    # ── 设置页 ────────────────────────────────

    def test_every_category_has_an_insert_button_and_new_ones_appear_immediately(self):
        view = self.settings_view()
        buttons = self.token_buttons(view)
        for name in ["营业额 合计", "账户", "邮政账户", "成本 合计", "龙虾", "零钱", "营业额备注"]:
            self.assertIn(name, buttons)
        height_before = view._token_holder.height()

        with FakeDialogs(names=["新分类甲"]):
            view._add_parent()
        with FakeDialogs(names=[f"新子项{i}" for i in range(12)]):
            for _ in range(12):
                view._add_child()
        self.pump()

        buttons = self.token_buttons(view)
        self.assertIn("新分类甲 合计", buttons)
        holder = view._token_holder.rect()
        for i in range(12):
            button = buttons[f"新子项{i}"]
            self.assertTrue(button.isVisible())
            self.assertTrue(holder.contains(button.geometry()), f"新子项{i} 没有排进按钮区域")
        self.assertGreater(view._token_holder.height(), height_before)  # 换行后区域立即变高

        view._formula_input.clear()
        buttons["新子项3"].click()
        self.assertEqual(view._formula_input.text(), "新子项3")

    def test_category_buttons_are_laid_out_on_first_show_even_in_a_narrow_window(self):
        from ui.settings_view import SettingsView
        from ui.sync_manager import SyncManager

        self.manager = SyncManager(self.db, "test")
        view = SettingsView(self.db, self.manager)
        view.resize(700, 600)  # 相当于最小窗口宽度、125% 缩放时设置页的可用宽度
        view.show()
        self.widgets.append(view)
        self.pump()
        names = ["添加一级", "添加二级", "改名", "删除"]
        buttons = [b for b in view.findChildren(QPushButton) if b.text() in names]
        self.assertEqual(len(buttons), 6)
        for button in buttons:
            holder = button.parentWidget()
            self.assertTrue(holder.rect().contains(button.geometry()), f"「{button.text()}」没有排进按钮区域")
            self.assertLess(button.width(), 300)
        content = view.findChild(QScrollArea).widget()
        self.assertLessEqual(content.minimumSizeHint().width(), view.findChild(QScrollArea).viewport().width())

    def test_formula_is_tidied_on_focus_out_and_tidying_alone_is_not_a_change(self):
        self.db.set_config("profit_formula", "账户+现金  -（小肠×2）")
        view = self.settings_view()
        self.assertEqual(view._formula_input.text(), "账户 + 现金 - (小肠 * 2)")
        self.assertFalse(view.is_dirty())

        view._formula_input.setText("营业额－成本÷2")
        view._formula_input.editing_finished.emit()
        self.assertEqual(view._formula_input.text(), "营业额 - 成本 / 2")

        view._formula_input.setText("营业额 - (成本")  # 有误的公式保持原样，等待修改
        view._formula_input.editing_finished.emit()
        self.assertEqual(view._formula_input.text(), "营业额 - (成本")

    def test_invalid_formula_pops_up_format_error_and_is_not_saved(self):
        view = self.settings_view()
        saved = self.db.get_config("profit_formula")
        for formula, hint in [("营业额 - (成本", "括号"), ("营业额 - 不存在的分类", "不存在的分类"), ("", "不能为空")]:
            view._formula_input.setText(formula)
            with FakeDialogs() as dialogs:
                self.assertFalse(view._on_save())
            self.assertEqual(dialogs.warnings[0][0], "公式格式错误")
            self.assertIn(hint, dialogs.warnings[0][1])
            self.assertEqual(self.db.get_config("profit_formula"), saved)

    def test_saving_tidies_formula_and_renames_rewrite_history(self):
        self.db.upsert_record("2026-10-01", 6.0, {"营业额": {"账户": 5.0, "邮政账户": 2.0}, "成本": {"小肠": 1.0}}, "")
        self.db.mark_record_synced("2026-10-01", 1, self.db.collect_dirty_records(5)[0]["modified_at"])
        view = self.settings_view()

        view._parent_list.setCurrentRow(0)
        view._child_list.setCurrentRow(0)
        with FakeDialogs(names=["银行账户"]):
            view._rename_child()
        view._parent_list.setCurrentRow(1)
        with FakeDialogs(names=["进货成本"]):
            view._rename_parent()
        formula = view._formula_input.text()
        self.assertTrue(formula.startswith("银行账户 + 邮政账户"), formula)  # 只替换完整的名称
        self.assertTrue(view.is_dirty())

        with FakeDialogs():
            self.assertTrue(view._on_save())
        record = self.db.get_record("2026-10-01")
        self.assertEqual(record["raw_data"], {"营业额": {"银行账户": 5.0, "邮政账户": 2.0}, "进货成本": {"小肠": 1.0}})
        self.assertEqual([r["date"] for r in self.db.collect_dirty_records(5)], ["2026-10-01"])  # 改名会同步到手机
        self.assertEqual(self.db.get_sync_meta("settings_dirty"), "1")
        self.assertFalse(view.is_dirty())

    def test_settings_changed_by_sync_while_editing_are_not_silently_overwritten(self):
        view = self.settings_view()
        with FakeDialogs(names=["本机新增"]):
            view._add_parent()
        self.assertTrue(self.db.apply_remote_settings({
            "data": {"custom_field_tree": [{"name": "手机分类", "children": []}], "custom_fields": ["手机分类"],
                     "profit_formula": "手机分类"},
            "rev": 3, "modified_at": 5,
        }))
        event_bus.config_changed.emit()
        self.assertIn("本机新增", [n["name"] for n in view._tree])  # 编辑中的内容保留

        with FakeDialogs(choice="取消") as dialogs:
            self.assertFalse(view._on_save())
        self.assertIn("已通过云同步更新", dialogs.boxes[0][1])  # macOS 上 QMessageBox 不显示标题，按正文判断
        self.assertEqual(self.db.get_config("profit_formula"), "手机分类")

        with FakeDialogs(choice="载入最新设置"):
            self.assertFalse(view._on_save())
        self.assertEqual([n["name"] for n in view._tree], ["手机分类"])
        self.assertFalse(view.is_dirty())

    def test_leaving_settings_with_unsaved_changes_asks_first(self):
        from ui.main_window import MainWindow
        from ui.sync_manager import SyncManager

        self.manager = SyncManager(self.db, "test")
        window = MainWindow(self.db, self.manager)
        self.widgets.append(window)
        window._switch_page(3)
        settings = window._settings_view
        with FakeDialogs(names=["临时分类"]):
            settings._add_parent()

        with FakeDialogs(choice="取消") as dialogs:
            window._switch_page(0)
        self.assertIn("修改还没有保存", dialogs.boxes[0][1])
        self.assertEqual(window._stack.currentIndex(), 3)
        self.assertTrue(window._nav_buttons[3].isChecked())

        with FakeDialogs(choice="不保存"):
            window._switch_page(0)
        self.assertEqual(window._stack.currentIndex(), 0)
        self.assertFalse(settings.is_dirty())
        self.assertNotIn("临时分类", self.db.get_config("custom_field_tree"))

    def test_redetect_reads_the_preset_file_and_syncs(self):
        from test_desktop_sync import TOKEN, ServerFixture

        self.server = ServerFixture(self.tmp / "server")
        view = self.settings_view()
        self.assertFalse(self.manager.configured)
        self.assertTrue(view._redetect_btn.isVisibleTo(view))
        self.assertFalse(view._sync_now_btn.isVisibleTo(view))
        with FakeDialogs() as dialogs:
            view._redetect_preset()
        self.assertEqual(dialogs.warnings[0][0], "没有找到同步配置")

        (self.tmp / "ProfitHelper-sync.txt").write_text(make_connection_code(self.server.url, TOKEN), encoding="utf-8")
        view._redetect_preset()
        self.assertTrue(self.wait(lambda: self.manager.state == "ok"))
        self.assertTrue(view._sync_now_btn.isVisibleTo(view))
        self.assertFalse(view._redetect_btn.isVisibleTo(view))

    def test_full_width_category_names_are_stored_half_width(self):
        view = self.settings_view()
        with FakeDialogs(names=["ｘ１号"]):
            view._add_parent()
        self.assertIn("x1号", [n["name"] for n in view._tree])
        view._formula_input.setText("营业额 - 成本 + ｘ１号")  # 公式里写全角也能识别
        self.assertEqual(view._formula_problem()[0], "ok")

    # ── 录入页 ────────────────────────────────

    def test_entry_rolls_over_to_the_new_day_unless_editing(self):
        view = self.entry_view()
        yesterday = QDate.currentDate().addDays(-1)

        def opened_yesterday_as_today():  # 模拟程序开着过了零点
            view._current_date = yesterday
            view._load_current_date()
            view._loaded_as_today = True

        opened_yesterday_as_today()
        self.assertTrue(view.roll_over_to_today())
        self.assertEqual(view._current_date, QDate.currentDate())

        opened_yesterday_as_today()
        view._field_inputs["营业额::现金"].setText("5")  # 有未保存的修改时不换日期
        self.assertFalse(view.roll_over_to_today())
        self.assertEqual(view._current_date, yesterday)

    def test_invalid_amount_pops_up_format_error(self):
        view = self.entry_view()
        view._field_inputs["营业额::现金"].setText("12元")
        with FakeDialogs() as dialogs:
            self.assertFalse(view._on_save())
        self.assertEqual(dialogs.warnings[0][0], "金额格式错误")
        self.assertIn("营业额 / 现金", dialogs.warnings[0][1])
        self.assertIsNone(self.db.get_record(QDate.currentDate().toString("yyyy-MM-dd")))

    def test_saving_keeps_data_of_removed_categories_and_can_clear_the_note(self):
        date = QDate.currentDate().toString("yyyy-MM-dd")
        self.db.upsert_record(date, 1.0, {"营业额": {"现金": 1.0, "旧子项": 9.0}, "旧分类": 3.0}, "原备注")
        view = self.entry_view()
        view._field_inputs["营业额::现金"].setText("2")
        view._note_input.setPlainText("")
        with FakeDialogs():
            self.assertTrue(view._on_save())
        record = self.db.get_record(date)
        self.assertEqual(record["raw_data"]["营业额"]["现金"], 2.0)
        self.assertEqual(record["raw_data"]["营业额"]["旧子项"], 9.0)
        self.assertEqual(record["raw_data"]["旧分类"], 3.0)
        self.assertEqual(record["note"], "")

    def test_category_change_from_sync_keeps_unsaved_input(self):
        view = self.entry_view()
        view._field_inputs["营业额::现金"].setText("88")
        view._note_input.setPlainText("还没保存")
        tree = [{"name": "营业额", "children": ["账户", "现金", "新子项"]}, {"name": "成本", "children": ["小肠"]}]
        self.assertTrue(self.db.apply_remote_settings({
            "data": {"custom_field_tree": tree, "custom_fields": ["营业额", "账户", "现金", "新子项", "成本", "小肠"],
                     "profit_formula": "营业额 - 成本"},
            "rev": 2, "modified_at": 5,
        }))
        event_bus.config_changed.emit()
        self.assertIn("营业额::新子项", view._field_inputs)
        self.assertEqual(view._field_inputs["营业额::现金"].text(), "88")
        self.assertEqual(view._note_input.toPlainText(), "还没保存")
        self.assertTrue(view.has_unsaved_changes())


    # ── 历史利润与日期防误操作 ────────────────

    def test_formula_change_asks_whether_to_recompute_saved_profits(self):
        date = "2026-10-01"
        self.db.upsert_record(date, 6.0, {"营业额": {"账户": 5.0, "邮政账户": 2.0}, "成本": {"小肠": 1.0}, "零钱": 4.0}, "")
        view = self.settings_view()
        formula = view._formula_input.text()
        view._formula_input.setText(formula + " + 零钱")

        with FakeDialogs(choice=None) as dialogs:  # 关掉对话框 = 取消保存
            self.assertFalse(view._on_save())
        self.assertIn("1 天利润会变化", dialogs.boxes[0][1])
        self.assertIn("¥6.00 → ¥10.00", dialogs.boxes[0][1])
        self.assertTrue(view.is_dirty())

        with FakeDialogs(choice="保留原利润"):
            self.assertTrue(view._on_save())
        self.assertEqual(self.db.get_record(date)["total_profit"], 6.0)

        with FakeDialogs(names=["备用金"]):  # 之后的修改不影响利润：不再询问
            view._add_parent()
        with FakeDialogs() as dialogs:
            self.assertTrue(view._on_save())
        self.assertEqual(dialogs.boxes, [])

        view._formula_input.setText(formula + " + 零钱 * 2")
        with FakeDialogs(choice="重算历史"):
            self.assertTrue(view._on_save())
        self.assertEqual(self.db.get_record(date)["total_profit"], 14.0)
        self.assertIn(date, [r["date"] for r in self.db.collect_dirty_records(5)])  # 会同步到手机

    def test_saving_a_future_date_asks_first(self):
        view = self.entry_view()
        future = QDate.currentDate().addYears(7)
        view._request_date(future)
        view._field_inputs["营业额::现金"].setText("5")
        with FakeDialogs(choice="取消") as dialogs:
            self.assertFalse(view._on_save())
        self.assertIn("请确认没有选错年份或日期", dialogs.boxes[0][1])
        self.assertIsNone(self.db.get_record(future.toString("yyyy-MM-dd")))
        with FakeDialogs(choice="仍然保存"):
            self.assertTrue(view._on_save())
        self.assertIsNotNone(self.db.get_record(future.toString("yyyy-MM-dd")))

        today = QDate.currentDate()
        view._request_date(today)
        view._field_inputs["营业额::现金"].setText("6")
        with FakeDialogs() as dialogs:  # 今天和以前的日期直接保存
            self.assertTrue(view._on_save())
        self.assertEqual(dialogs.boxes, [])

    def test_mouse_wheel_cannot_change_the_entry_date(self):
        def wheel(widget, notches: int) -> None:
            local = QPointF(widget.width() / 2, widget.height() / 2)
            event = QWheelEvent(local, QPointF(widget.mapToGlobal(local.toPoint())), QPoint(0, 0), QPoint(0, 120 * notches),
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(widget, event)
            self.pump()

        plain = QDateEdit(QDate(2026, 9, 6))  # 对照：普通日期框滚 7 格就从 2026 变成 2033
        plain.setDisplayFormat("yyyy-MM-dd")
        plain.show()
        self.widgets.append(plain)
        self.pump()
        wheel(plain, 7)
        self.assertEqual(plain.date().year(), 2033)

        view = self.entry_view()
        before = view._date_edit.date()
        wheel(view._date_edit, 7)
        self.assertEqual(view._date_edit.date(), before)
        self.assertEqual(view._current_date, before)


if __name__ == "__main__":
    unittest.main()
