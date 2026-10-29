"""
tests/test_desktop_sync.py
桌面端同步引擎 ↔ 同步服务端的端到端测试（不依赖 Qt）。

运行：python -m unittest discover -s tests
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

import profit_sync_server as pss  # noqa: E402
from core.database import DatabaseManager  # noqa: E402
from core.sync_client import (  # noqa: E402
    SyncAuthError,
    SyncConfig,
    SyncEngine,
    SyncError,
    make_connection_code,
    parse_connection_code,
)

TOKEN = "PH-ABCD-EFGH-JKMN-PQRS-TVWX-YZ01"


class ServerFixture:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        config = dict(pss.DEFAULT_CONFIG)
        config.update({"data_dir": str(data_dir), "tokens": [{"name": "t", "sha256": pss.hash_token(TOKEN)}]})
        self.store = pss.LedgerStore(data_dir / "ledger.db")
        self.httpd = pss.SyncHTTPServer(("127.0.0.1", 0), self.store, config, None)
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.store.close()


def make_legacy_db(path: Path) -> None:
    """模拟升级前（v0.1）的账本：没有同步表，记录和自定义分类都已存在。"""
    with contextlib.closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE DailyRecords (date TEXT PRIMARY KEY, total_profit REAL DEFAULT 0.0, "
                     "raw_data TEXT DEFAULT '{}', note TEXT DEFAULT '')")
        conn.execute("CREATE TABLE Config (key TEXT PRIMARY KEY, value TEXT DEFAULT '')")
        tree = [{"name": "营业额", "children": ["现金", "微信"]}, {"name": "成本", "children": ["进货"]}]
        conn.executemany("INSERT INTO Config VALUES (?, ?)", [
            ("custom_field_tree", json.dumps(tree, ensure_ascii=False)),
            ("custom_fields", json.dumps(["营业额", "现金", "微信", "成本", "进货"], ensure_ascii=False)),
            ("profit_formula", "现金 + 微信 - 进货"),
            ("history_start_date", "2026-01-01"),
        ])
        for day, cash in [("2026-10-01", 500), ("2026-10-02", 650), ("2026-10-03", 720)]:
            raw = {"营业额": {"现金": cash, "微信": 100}, "成本": {"进货": 200}}
            conn.execute("INSERT INTO DailyRecords VALUES (?, ?, ?, ?)",
                         (day, cash + 100 - 200, json.dumps(raw, ensure_ascii=False), f"{day} 旧备注"))


class DesktopSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="desk-sync-"))
        self.server = ServerFixture(self.tmp / "server")
        legacy = self.tmp / "a" / "family_ledger.db"
        legacy.parent.mkdir()
        make_legacy_db(legacy)
        self.db_a = DatabaseManager(legacy)
        (self.tmp / "b").mkdir()
        self.db_b = DatabaseManager(self.tmp / "b" / "family_ledger.db")
        self.a = self._engine(self.db_a)
        self.b = self._engine(self.db_b)

    def tearDown(self):
        self.db_a.close()
        self.db_b.close()
        self.server.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _engine(self, db: DatabaseManager) -> SyncEngine:
        engine = SyncEngine(db, app_version="test")
        engine.save_config(SyncConfig(server_url=self.server.url, token=TOKEN))
        return engine

    @staticmethod
    def _dates(db: DatabaseManager) -> list[str]:
        return [r["date"] for r in db.get_all_records()]

    def test_legacy_data_uploads_and_fresh_device_receives_everything(self):
        result = self.a.sync()
        self.assertEqual(result.pushed, 3)
        self.assertEqual(self.db_a.count_pending_changes(), 0)

        result = self.b.sync()
        self.assertEqual(result.pulled, 3)
        self.assertTrue(result.settings_changed)
        self.assertEqual(self._dates(self.db_b), ["2026-10-01", "2026-10-02", "2026-10-03"])
        self.assertEqual(self.db_b.get_record("2026-10-02")["note"], "2026-10-02 旧备注")
        self.assertEqual(self.db_b.get_config("profit_formula"), "现金 + 微信 - 进货")
        # 界面偏好不参与同步
        self.assertEqual(self.db_b.get_config("history_start_date"), "")
        # 首次同步前自动备份
        self.assertTrue(list((self.tmp / "a").glob("family_ledger.before-sync-*.db")))

    def test_fresh_device_defaults_do_not_override_legacy_settings(self):
        self.b.sync()  # 新设备先同步：不会上传默认设置
        self.a.sync()
        self.b.sync()
        self.assertEqual(self.db_b.get_config("profit_formula"), "现金 + 微信 - 进货")
        self.assertEqual(self.db_a.get_config("profit_formula"), "现金 + 微信 - 进货")

    def test_edits_flow_both_ways(self):
        self.a.sync()
        self.b.sync()
        raw = {"营业额": {"现金": 900, "微信": 50}, "成本": {"进货": 100}}
        self.db_b.upsert_record("2026-10-04", 850.0, raw, "手机录入")
        self.b.sync()
        self.a.sync()
        self.assertEqual(self.db_a.get_record("2026-10-04")["total_profit"], 850.0)
        self.assertEqual(self.db_a.get_record("2026-10-04")["note"], "手机录入")

    def test_conflict_later_edit_wins_on_both_devices(self):
        self.a.sync()
        self.b.sync()
        raw = {"营业额": {"现金": 1, "微信": 0}, "成本": {"进货": 0}}
        self.db_a.upsert_record("2026-10-02", 1.0, raw, "A 的修改")
        time.sleep(0.01)
        self.db_b.upsert_record("2026-10-02", 2.0, raw, "B 的修改（更晚）")
        self.b.sync()
        self.a.sync()  # A 的修改更早，被拒绝并采用 B 的版本
        self.b.sync()
        for db in (self.db_a, self.db_b):
            record = db.get_record("2026-10-02")
            self.assertEqual(record["total_profit"], 2.0)
            self.assertEqual(record["note"], "B 的修改（更晚）")
        self.assertEqual(self.db_a.count_pending_changes(), 0)

    def test_delete_propagates(self):
        self.a.sync()
        self.b.sync()
        self.db_a.delete_record("2026-10-01")
        self.a.sync()
        self.b.sync()
        self.assertEqual(self._dates(self.db_b), ["2026-10-02", "2026-10-03"])

    def test_settings_change_propagates(self):
        self.a.sync()
        self.b.sync()
        self.db_b.set_configs({"profit_formula": "现金 - 进货"})
        self.b.sync()
        result = self.a.sync()
        self.assertTrue(result.settings_changed)
        self.assertEqual(self.db_a.get_config("profit_formula"), "现金 - 进货")

    def test_server_rebuild_triggers_full_reupload(self):
        self.a.sync()
        self.server.stop()
        shutil.rmtree(self.tmp / "server")
        self.server = ServerFixture(self.tmp / "server")
        self.a.save_config(SyncConfig(server_url=self.server.url, token=TOKEN))
        result = self.a.sync()
        self.assertEqual(result.pushed, 3)
        self.assertEqual(self.server.store.status()["record_count"], 3)

    def test_import_replaces_everywhere(self):
        self.a.sync()
        self.b.sync()
        backup = self.tmp / "only-one.db"
        self.db_b.delete_records(["2026-10-01", "2026-10-02"])
        self.db_b.export_database(backup)
        # B 的删除尚未同步时，A 导入一份只有一天的备份，导入结果应同步到所有设备
        self.db_a.import_database(backup)
        self.a.sync()
        self.b.sync()
        self.assertEqual(self._dates(self.db_a), ["2026-10-03"])
        self.assertEqual(self._dates(self.db_b), ["2026-10-03"])

    def test_export_contains_no_sync_secrets(self):
        self.a.sync()
        out = self.tmp / "export.db"
        self.db_a.export_database(out)
        with contextlib.closing(sqlite3.connect(out)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, {"DailyRecords", "Config", "android_metadata"})
        self.assertNotIn(TOKEN.encode(), out.read_bytes())

    def test_wrong_token_and_unreachable_server(self):
        engine = SyncEngine(self.db_b, app_version="test")
        engine.save_config(SyncConfig(server_url=self.server.url, token="PH-WRONG"))
        with self.assertRaises(SyncAuthError):
            engine.sync()
        engine.save_config(SyncConfig(server_url="http://127.0.0.1:1", token=TOKEN))
        with self.assertRaises(SyncError):
            engine.sync()
        self.assertTrue(self.db_b.get_sync_meta("last_error"))

    def test_builtin_preset_applies_until_user_disconnects(self):
        import core.sync_preset as preset

        preset_file = self.tmp / "利润助手同步配置.txt"
        preset_file.write_text("说明文字\n" + make_connection_code(self.server.url, TOKEN) + "\n", encoding="utf-8")
        original = preset.data_file_path
        preset.data_file_path = lambda name: self.tmp / name
        try:
            fresh = DatabaseManager(self.tmp / "fresh.db")
            engine = SyncEngine(fresh, app_version="test")
            self.assertTrue(preset.apply_preset_if_needed(engine))
            self.assertEqual(engine.load_config().token, TOKEN)
            self.assertEqual(engine.sync().pulled, 0)
            self.assertFalse(preset.apply_preset_if_needed(engine))  # 已设置过，不重复套用
            fresh.forget_sync_server()
            fresh.set_sync_meta("preset_disabled", "1")
            self.assertFalse(preset.apply_preset_if_needed(engine))  # 用户主动断开后不再自动连接
            fresh.close()
        finally:
            preset.data_file_path = original

    def test_connection_code_round_trip(self):
        code = make_connection_code("example.com:28443", TOKEN, "203.0.113.7")
        config = parse_connection_code(code)
        self.assertEqual(config.server_url, "https://example.com:28443")
        self.assertEqual(config.token, TOKEN)
        self.assertEqual(config.fallback_ip, "203.0.113.7")
        with self.assertRaises(SyncError):
            parse_connection_code("PH1-not-base64!!")
        with self.assertRaises(SyncError):
            SyncEngine(self.db_b, "t").save_config(SyncConfig(server_url="http://example.com", token="x"))


if __name__ == "__main__":
    unittest.main()
