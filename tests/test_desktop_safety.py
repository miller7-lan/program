"""
tests/test_desktop_safety.py
桌面端安全相关逻辑：密钥轮换后自动换用新的内置配置、密钥错误时暂停自动同步、编辑期间被同步更新时不静默覆盖。

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
from core.sync_client import SyncConfig, SyncEngine, make_connection_code  # noqa: E402
from test_desktop_sync import TOKEN, ServerFixture  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:  # pragma: no cover - 没有 PySide6 的环境只跑核心测试
    QApplication = None


class PresetRotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="preset-"))
        self.db = DatabaseManager(self.tmp / "family_ledger.db")
        self.engine = SyncEngine(self.db, app_version="test")
        self.original = preset.data_file_path
        preset.data_file_path = lambda name: self.tmp / name

    def tearDown(self):
        preset.data_file_path = self.original
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_preset(self, url: str, token: str) -> None:
        (self.tmp / "ProfitHelper-sync.txt").write_text(make_connection_code(url, token), encoding="utf-8")

    def test_rotated_key_in_new_preset_is_adopted(self):
        self._write_preset("https://example.com:28443", "PH-OLD")
        self.assertTrue(preset.apply_preset_if_needed(self.engine))
        self.db.set_sync_meta("last_rev", "42")
        self._write_preset("https://example.com:28443", "PH-NEW")
        self.assertTrue(preset.apply_preset_if_needed(self.engine))
        self.assertEqual(self.engine.load_config().token, "PH-NEW")
        self.assertEqual(self.db.get_sync_meta("last_rev"), "42")  # 同一服务器换密钥不重置同步进度

    def test_manual_connection_is_never_replaced(self):
        self.engine.save_config(SyncConfig("https://example.com:28443", "PH-MANUAL"))
        self.db.set_sync_meta("config_source", "manual")
        self._write_preset("https://example.com:28443", "PH-NEW")
        self.assertFalse(preset.apply_preset_if_needed(self.engine))
        self.assertEqual(self.engine.load_config().token, "PH-MANUAL")

    def test_unknown_origin_on_other_server_is_left_alone(self):
        self.engine.save_config(SyncConfig("https://other.example.com", "PH-X"))
        self._write_preset("https://example.com:28443", "PH-NEW")
        self.assertFalse(preset.apply_preset_if_needed(self.engine))

    def test_v11_install_without_source_flag_adopts_new_key_for_same_server(self):
        self.engine.save_config(SyncConfig("https://example.com:28443", "PH-OLD"))  # v1.1 不记录来源
        self._write_preset("https://example.com:28443", "PH-NEW")
        self.assertTrue(preset.apply_preset_if_needed(self.engine))

    def test_disconnect_disables_preset(self):
        self._write_preset("https://example.com:28443", "PH-OLD")
        preset.apply_preset_if_needed(self.engine)
        self.db.forget_sync_server()
        self.db.set_sync_meta("preset_disabled", "1")
        self.assertFalse(preset.apply_preset_if_needed(self.engine))


@unittest.skipIf(QApplication is None, "需要 PySide6")
class QtSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qt-safety-"))
        self.server = ServerFixture(self.tmp / "server")
        self.db = DatabaseManager(self.tmp / "family_ledger.db")

    def tearDown(self):
        self.db.close()
        self.server.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wait(self, condition, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.app.processEvents()
            if condition():
                return True
            time.sleep(0.02)
        return False

    def test_wrong_key_pauses_automatic_sync(self):
        from ui.sync_manager import SyncManager

        manager = SyncManager(self.db, "test")
        manager.save_config(SyncConfig(self.server.url, "PH-WRONG-KEY"))
        manager.sync_now()
        self.assertTrue(self._wait(lambda: manager.state == "error"))
        self.assertTrue(manager._auth_failed)
        self.assertEqual(manager.status_view()[0], "同步密钥不正确")
        self.db.upsert_record("2026-10-10", 1.0, {"现金": 1.0}, "")
        self.assertFalse(manager._debounce.isActive())  # 保存后不再自动重试
        manager._auto_sync()
        self.assertFalse(manager._thread.is_alive() and manager.state == "busy")
        manager.save_config(SyncConfig(self.server.url, TOKEN))  # 换成正确密钥后恢复
        manager.sync_now()
        self.assertTrue(self._wait(lambda: manager.state == "ok"))
        self.assertFalse(manager._auth_failed)
        manager.shutdown(timeout=5)

    def test_save_does_not_silently_overwrite_a_day_updated_by_sync(self):
        from PySide6.QtCore import QDate

        from ui.entry_view import EntryView

        date = QDate.currentDate().toString("yyyy-MM-dd")
        self.db.upsert_record(date, 10.0, {"营业额": {"账户": 10.0}}, "原始")
        pushed = self.db.collect_dirty_records(1)[0]
        self.db.mark_record_synced(date, 1, pushed["modified_at"])  # 这一天已同步过
        view = EntryView(self.db)
        view._load_current_date()
        # 用户正在编辑时，云同步写入了其他设备的新版本
        self.assertTrue(self.db.apply_remote_record({
            "date": date, "total_profit": 99.0, "raw_data": {"营业额": {"账户": 99.0}},
            "note": "手机上改的", "deleted": False, "modified_at": 5, "rev": 7,
        }))
        first_input = next(iter(view._field_inputs.values()))
        first_input.setText("12")
        original_exec = QMessageBox.exec
        prompts = []
        QMessageBox.exec = lambda box: prompts.append(box.text()) or 0  # 不点任何按钮 = 取消
        try:
            saved = view._on_save()
        finally:
            QMessageBox.exec = original_exec
        self.assertFalse(saved)
        self.assertEqual(len(prompts), 1)
        self.assertIn("在你编辑期间已通过云同步更新", prompts[0])
        self.assertEqual(self.db.get_record(date)["note"], "手机上改的")  # 同步来的版本没有被覆盖
        view.deleteLater()


if __name__ == "__main__":
    unittest.main()
