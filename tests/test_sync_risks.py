"""
tests/test_sync_risks.py
数据同步风险场景：误覆盖、误删除、丢响应、时钟偏差、坏数据卡死同步、密钥泄露面。

运行：python -m unittest discover -s tests
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

import profit_sync_server as pss  # noqa: E402
from core.database import DatabaseManager  # noqa: E402
from core.sync_client import SyncConfig, SyncEngine  # noqa: E402
sys.path.insert(0, str(ROOT / "tests"))
from test_desktop_sync import TOKEN, ServerFixture  # noqa: E402

RAW = {"营业额": {"现金": 100.0}, "成本": {"进货": 40.0}}


def raw(cash: float) -> dict:
    return {"营业额": {"现金": cash}, "成本": {"进货": 40.0}}


class SyncRiskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sync-risk-"))
        self.server = ServerFixture(self.tmp / "server")
        self.dbs: list[DatabaseManager] = []
        self.a, self.db_a = self._device("a")
        self.b, self.db_b = self._device("b")

    def tearDown(self):
        for db in self.dbs:
            db.close()
        self.server.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _device(self, name: str):
        (self.tmp / name).mkdir()
        db = DatabaseManager(self.tmp / name / "family_ledger.db")
        self.dbs.append(db)
        engine = SyncEngine(db, app_version="test")
        engine.save_config(SyncConfig(server_url=self.server.url, token=TOKEN))
        return engine, db

    def _record(self, db, date):
        record = db.get_record(date)
        return None if record is None else (record["total_profit"], record["raw_data"], record["note"])

    # ── 冲突：编辑与删除 ───────────────────

    def test_later_edit_beats_earlier_delete(self):
        self.db_a.upsert_record("2026-10-01", 60.0, raw(100), "原始")
        self.a.sync()
        self.b.sync()
        self.db_a.delete_record("2026-10-01")          # A 先删
        time.sleep(0.01)
        self.db_b.upsert_record("2026-10-01", 160.0, raw(200), "B 后改")  # B 后改
        self.a.sync()
        self.b.sync()
        self.a.sync()
        self.assertEqual(self._record(self.db_a, "2026-10-01"), (160.0, raw(200), "B 后改"))
        self.assertEqual(self._record(self.db_b, "2026-10-01"), (160.0, raw(200), "B 后改"))

    def test_later_delete_beats_earlier_edit(self):
        self.db_a.upsert_record("2026-10-02", 60.0, raw(100), "")
        self.a.sync()
        self.b.sync()
        self.db_a.upsert_record("2026-10-02", 70.0, raw(110), "A 先改")
        time.sleep(0.01)
        self.db_b.delete_record("2026-10-02")
        self.a.sync()
        self.b.sync()
        self.a.sync()
        self.assertIsNone(self._record(self.db_a, "2026-10-02"))
        self.assertIsNone(self._record(self.db_b, "2026-10-02"))

    def test_overwritten_and_deleted_versions_are_recoverable_on_server(self):
        self.db_a.upsert_record("2026-10-03", 60.0, raw(100), "v1")
        self.a.sync()
        self.db_a.upsert_record("2026-10-03", 70.0, raw(110), "v2")
        self.a.sync()
        self.db_a.delete_record("2026-10-03")
        self.a.sync()
        with contextlib.closing(sqlite3.connect(self.tmp / "server" / "ledger.db")) as conn:
            notes = [json.loads(p)["note"] for (p,) in conn.execute(
                "SELECT payload FROM history WHERE item_key = '2026-10-03' ORDER BY id")]
        self.assertEqual(notes, ["v1", "v2"])

    # ── 网络异常 ──────────────────────────

    def test_lost_response_then_retry_does_not_duplicate_or_overwrite(self):
        self.db_a.upsert_record("2026-10-04", 60.0, raw(100), "A")
        change = self.db_a.collect_dirty_records(10)
        # 模拟：服务器已写入，但响应在路上丢失（客户端未标记已同步）
        request = pss.parse_sync_request({
            "device": {"id": self.db_a.get_sync_meta("device_id")}, "since_rev": 0,
            "client_time": pss.now_ms(), "push": {"records": change},
        })
        self.server.store.sync(request)
        self.assertEqual(self.db_a.count_pending_changes(), 1)
        result = self.a.sync()
        self.assertEqual(self.db_a.count_pending_changes(), 0)
        self.assertEqual(self.server.store.status()["record_count"], 1)
        self.assertEqual(self.server.store.current_rev(), 1)
        self.assertFalse(result.records_changed)

    def test_lost_response_while_other_device_edits_keeps_newest(self):
        self.db_a.upsert_record("2026-10-05", 60.0, raw(100), "A")
        change = self.db_a.collect_dirty_records(10)
        self.server.store.sync(pss.parse_sync_request({
            "device": {"id": self.db_a.get_sync_meta("device_id")}, "since_rev": 0,
            "client_time": pss.now_ms(), "push": {"records": change},
        }))
        self.b.sync()
        time.sleep(0.01)
        self.db_b.upsert_record("2026-10-05", 99.0, raw(139), "B 更新")
        self.b.sync()
        self.a.sync()  # A 重发旧内容：应被拒绝并采用 B 的新版本
        self.assertEqual(self._record(self.db_a, "2026-10-05"), (99.0, raw(139), "B 更新"))

    def test_client_clock_one_day_behind_still_orders_correctly(self):
        self.db_a.upsert_record("2026-10-06", 60.0, raw(100), "A")
        self.a.sync()
        self.b.sync()
        time.sleep(0.01)
        self.db_b.upsert_record("2026-10-06", 80.0, raw(120), "B 的时钟慢一天，但修改更晚")
        # 把 B 的本地修改时间和请求时间都往回拨一天
        with self.db_b._lock, self.db_b._conn:
            self.db_b._conn.execute("UPDATE SyncRecordState SET modified_at = modified_at - 86400000")
        import core.sync_client as sc
        original = sc.now_ms
        sc.now_ms = lambda: original() - 86_400_000
        try:
            self.b.sync()
        finally:
            sc.now_ms = original
        self.a.sync()
        self.assertEqual(self._record(self.db_a, "2026-10-06")[2], "B 的时钟慢一天，但修改更晚")

    # ── 坏数据不能卡死整个同步 ─────────────

    def test_malformed_date_does_not_block_other_records(self):
        with self.db_a._lock, self.db_a._conn:
            self.db_a._conn.execute(
                "INSERT INTO DailyRecords (date, total_profit, raw_data, note) VALUES ('2026/10/07', 1, '{}', '')")
            self.db_a._conn.execute(
                "INSERT INTO SyncRecordState (date, modified_at, base_rev, dirty, deleted) VALUES ('2026/10/07', 5, 0, 1, 0)")
        self.db_a.upsert_record("2026-10-08", 60.0, raw(100), "正常记录")
        result = self.a.sync()
        self.assertEqual([d for d, _ in result.invalid], ["2026/10/07"])
        self.b.sync()
        self.assertIsNotNone(self._record(self.db_b, "2026-10-08"))

    def test_import_rejects_malformed_dates(self):
        bad = self.tmp / "bad.db"
        with contextlib.closing(sqlite3.connect(bad)) as conn, conn:
            conn.execute("CREATE TABLE DailyRecords (date TEXT PRIMARY KEY, total_profit REAL, raw_data TEXT, note TEXT)")
            conn.execute("CREATE TABLE Config (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO DailyRecords VALUES ('2026-13-01', 1, '{}', '')")
        with self.assertRaises(ValueError):
            self.db_a.import_database(bad)
        self.assertEqual(self.db_a.get_all_records(), [])

    def test_note_and_name_limits_match_server(self):
        from core.field_config import field_name_problem
        from core.database import NOTE_MAX_CHARS
        self.assertEqual(NOTE_MAX_CHARS, pss.MAX_NOTE_CHARS)
        self.assertTrue(field_name_problem("名" * (pss.MAX_NAME_CHARS + 1), set()))

    # ── 密钥泄露面 ────────────────────────

    def test_pre_sync_backup_does_not_contain_token(self):
        self.db_a.upsert_record("2026-10-09", 60.0, raw(100), "")
        self.a.sync()
        backups = list((self.tmp / "a").glob("family_ledger.before-sync-*.db"))
        self.assertEqual(len(backups), 1)
        self.assertNotIn(TOKEN.encode(), backups[0].read_bytes())


if __name__ == "__main__":
    unittest.main()
