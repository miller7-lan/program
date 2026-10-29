"""
tests/test_sync_server.py
同步服务端测试：认证、冲突、删除、分页、时钟校正、重建检测、gzip、TLS、备份与导出。

运行：python -m unittest discover -s tests
"""

from __future__ import annotations

import contextlib
import datetime as dt
import gzip
import http.client
import json
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import profit_sync_server as pss  # noqa: E402

TOKEN = "PH-TEST-0000-1111-2222-3333-4444"


def start_server(data_dir: Path, ssl_context=None, extra_config: dict | None = None):
    config = dict(pss.DEFAULT_CONFIG)
    config.update({
        "listen_host": "127.0.0.1",
        "listen_port": 0,
        "data_dir": str(data_dir),
        "tokens": [{"name": "test", "sha256": pss.hash_token(TOKEN)}],
    })
    config.update(extra_config or {})
    store = pss.LedgerStore(data_dir / "ledger.db")
    httpd = pss.SyncHTTPServer(("127.0.0.1", 0), store, config, ssl_context)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    return httpd, store


class Client:
    def __init__(self, port: int, device_id: str, token: str = TOKEN, ssl_context=None):
        self.port = port
        self.device_id = device_id
        self.token = token
        self.ssl_context = ssl_context

    def _conn(self):
        if self.ssl_context is not None:
            return http.client.HTTPSConnection("localhost", self.port, context=self.ssl_context, timeout=10)
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)

    def request(self, method: str, path: str, body=None, auth: bool = True, gzip_body: bool = False,
                headers: dict | None = None):
        conn = self._conn()
        hdrs = {"Accept-Encoding": "gzip"}
        if auth:
            hdrs["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
            if gzip_body:
                data = gzip.compress(data)
                hdrs["Content-Encoding"] = "gzip"
        hdrs.update(headers or {})
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        if resp.getheader("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        conn.close()
        content_type = resp.getheader("Content-Type", "")
        payload = json.loads(raw) if "json" in content_type else raw.decode("utf-8")
        return resp.status, payload, resp

    def sync(self, since_rev=0, records=None, settings=None, client_time=None, ledger_id="",
             pull_limit=None):
        body = {
            "protocol": 1,
            "device": {"id": self.device_id, "name": "测试设备", "platform": "test", "app_version": "t"},
            "ledger_id": ledger_id,
            "since_rev": since_rev,
            "client_time": client_time if client_time is not None else pss.now_ms(),
            "push": {"records": records or [], "settings": settings},
        }
        if pull_limit is not None:
            body["pull_limit"] = pull_limit
        status, payload, _ = self.request("POST", "/api/v1/sync", body)
        return status, payload


def record(date: str, profit: float, modified_at: int, base_rev: int = 0, note: str = "",
           deleted: bool = False, raw=None):
    return {
        "date": date,
        "total_profit": profit,
        "raw_data": raw if raw is not None else {"营业额": {"现金": profit}, "零钱": 0.0},
        "note": note,
        "deleted": deleted,
        "modified_at": modified_at,
        "base_rev": base_rev,
    }


SETTINGS_DATA = {
    "custom_field_tree": [{"name": "营业额", "children": ["现金"]}, {"name": "零钱", "children": []}],
    "custom_fields": ["营业额", "现金", "零钱"],
    "profit_formula": "现金 + 零钱",
}


class SyncServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pss-test-"))
        self.httpd, self.store = start_server(self.tmp)
        self.port = self.httpd.server_address[1]
        self.a = Client(self.port, "device-aaaaaaaa")
        self.b = Client(self.port, "device-bbbbbbbb")

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── 基础 ────────────────────────────────

    def test_ping_and_status_page_without_auth(self):
        status, payload, _ = self.a.request("GET", "/api/v1/ping", auth=False)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        status, page, _ = self.a.request("GET", "/", auth=False)
        self.assertEqual(status, 200)
        self.assertIn("利润助手同步服务", page)

    def test_auth_required_and_rate_limited(self):
        # 缺少密钥也计入失败次数
        status, payload, _ = self.a.request("GET", "/api/v1/status", auth=False)
        self.assertEqual(status, 401)
        wrong = Client(self.port, "device-cccccccc", token="PH-WRONG")
        for _ in range(pss.AUTH_FAIL_LIMIT - 1):
            self.assertEqual(wrong.request("GET", "/api/v1/status")[0], 401)
        # 达到上限后，即使密钥正确也会被暂时拒绝
        self.assertEqual(self.a.request("GET", "/api/v1/status")[0], 429)

    def test_token_normalization(self):
        loose = Client(self.port, "device-dddddddd", token="  ph-test-0000-1111-2222-3333-4444 ")
        self.assertEqual(loose.request("GET", "/api/v1/status")[0], 200)

    def test_unknown_route(self):
        self.assertEqual(self.a.request("GET", "/api/v1/nothing")[0], 404)

    # ── 推送 / 拉取 ─────────────────────────

    def test_push_then_pull_on_other_device(self):
        status, resp = self.a.sync(records=[record("2026-10-01", 100.0, pss.now_ms(), note="第一天")])
        self.assertEqual(status, 200, resp)
        self.assertEqual(resp["results"]["records"][0]["status"], "accepted")
        rev = resp["results"]["records"][0]["rev"]
        self.assertEqual(rev, 1)

        status, resp = self.b.sync(since_rev=0)
        self.assertEqual(status, 200)
        pulled = resp["pull"]["records"]
        self.assertEqual(len(pulled), 1)
        self.assertEqual(pulled[0]["date"], "2026-10-01")
        self.assertEqual(pulled[0]["note"], "第一天")
        self.assertEqual(pulled[0]["raw_data"], {"营业额": {"现金": 100.0}, "零钱": 0.0})
        self.assertEqual(resp["pull"]["next_since"], 1)
        self.assertFalse(resp["pull"]["has_more"])

    def test_identical_push_does_not_bump_rev(self):
        t = pss.now_ms()
        self.a.sync(records=[record("2026-10-02", 50.0, t)])
        status, resp = self.b.sync(records=[record("2026-10-02", 50.0, t + 5)])
        self.assertEqual(resp["results"]["records"][0]["status"], "accepted")
        self.assertEqual(resp["server_rev"], 1)

    def test_conflict_last_writer_wins_and_history_kept(self):
        t = pss.now_ms() - 60_000
        _, resp = self.a.sync(records=[record("2026-10-03", 10.0, t)])
        rev1 = resp["results"]["records"][0]["rev"]

        # B 基于 rev1 修改：直接接受
        _, resp = self.b.sync(since_rev=rev1, records=[record("2026-10-03", 20.0, t + 20_000, base_rev=rev1)])
        self.assertEqual(resp["results"]["records"][0]["status"], "accepted")
        rev2 = resp["results"]["records"][0]["rev"]

        # A 基于过时的 rev1、且修改时间更早：被拒绝并返回服务器当前版本
        _, resp = self.a.sync(since_rev=rev1, records=[record("2026-10-03", 15.0, t + 10_000, base_rev=rev1)])
        result = resp["results"]["records"][0]
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["current"]["total_profit"], 20.0)
        self.assertEqual(result["current"]["rev"], rev2)

        # A 基于过时的 rev1、但修改时间更晚：后写入者胜
        _, resp = self.a.sync(since_rev=rev2, records=[record("2026-10-03", 30.0, t + 40_000, base_rev=rev1)])
        self.assertEqual(resp["results"]["records"][0]["status"], "accepted")

        with contextlib.closing(sqlite3.connect(self.tmp / "ledger.db")) as conn:
            history = conn.execute("SELECT payload FROM history WHERE item_key = '2026-10-03' ORDER BY id").fetchall()
        self.assertEqual([json.loads(h[0])["total_profit"] for h in history], [10.0, 20.0])

    def test_delete_propagates_as_tombstone(self):
        t = pss.now_ms()
        _, resp = self.a.sync(records=[record("2026-10-04", 10.0, t)])
        rev = resp["results"]["records"][0]["rev"]
        _, resp = self.a.sync(since_rev=rev, records=[record("2026-10-04", 0, t + 1000, base_rev=rev, deleted=True)])
        self.assertEqual(resp["results"]["records"][0]["status"], "accepted")
        _, resp = self.b.sync(since_rev=0)
        self.assertEqual(len(resp["pull"]["records"]), 1)
        self.assertTrue(resp["pull"]["records"][0]["deleted"])

        _, status = None, self.a.request("GET", "/api/v1/status")[1]
        self.assertEqual(status["record_count"], 0)
        self.assertEqual(status["tombstone_count"], 1)

    def test_legacy_records_lose_to_real_edits(self):
        t = pss.now_ms()
        self.a.sync(records=[record("2026-10-05", 99.0, t)])
        # 升级前的旧数据使用 modified_at = 1，遇到已有记录时一律让位
        _, resp = self.b.sync(records=[record("2026-10-05", 1.0, 1), record("2026-10-06", 2.0, 1)])
        results = {r["date"]: r["status"] for r in resp["results"]["records"]}
        self.assertEqual(results, {"2026-10-05": "rejected", "2026-10-06": "accepted"})

    def test_clock_offset_is_corrected(self):
        now = pss.now_ms()
        ahead = now + 3_600_000  # 客户端时钟快一小时
        _, resp = self.a.sync(records=[record("2026-10-07", 1.0, ahead)], client_time=ahead)
        stored = resp["results"]["records"][0]["modified_at"]
        self.assertLess(abs(stored - now), 5_000)

    def test_settings_sync_and_conflict(self):
        t = pss.now_ms()
        _, resp = self.a.sync(settings={"data": SETTINGS_DATA, "modified_at": t, "base_rev": 0})
        self.assertEqual(resp["results"]["settings"]["status"], "accepted")
        rev = resp["results"]["settings"]["rev"]

        _, resp = self.b.sync(since_rev=0)
        self.assertEqual(resp["pull"]["settings"]["data"], SETTINGS_DATA)

        stale = dict(SETTINGS_DATA, profit_formula="现金")
        _, resp = self.b.sync(since_rev=rev, settings={"data": stale, "modified_at": 1, "base_rev": 0})
        self.assertEqual(resp["results"]["settings"]["status"], "rejected")
        self.assertEqual(resp["results"]["settings"]["current"]["data"]["profit_formula"], "现金 + 零钱")

    def test_pagination(self):
        t = pss.now_ms()
        first = dt.date(2024, 1, 1)
        dates = [(first + dt.timedelta(days=i)).isoformat() for i in range(700)]
        for start in range(0, len(dates), 500):
            batch = [record(d, float(i), t) for i, d in enumerate(dates[start:start + 500], start=start)]
            status, resp = self.a.sync(records=batch)
            self.assertEqual(status, 200)

        since, pages, collected = 0, 0, []
        while True:
            _, resp = self.b.sync(since_rev=since, pull_limit=300)
            collected.extend(r["date"] for r in resp["pull"]["records"])
            since = resp["pull"]["next_since"]
            pages += 1
            if not resp["pull"]["has_more"]:
                break
        self.assertEqual(pages, 3)
        self.assertEqual(sorted(collected), sorted(dates))
        self.assertEqual(since, 700)

    def test_resync_required_on_ledger_mismatch_or_future_rev(self):
        _, resp = self.a.sync(ledger_id="some-other-ledger")
        self.assertTrue(resp["resync_required"])
        _, resp = self.a.sync(since_rev=999)
        self.assertTrue(resp["resync_required"])
        _, resp = self.a.sync(ledger_id=self.store.ledger_id)
        self.assertFalse(resp["resync_required"])

    def test_invalid_record_is_reported_individually(self):
        bad = record("2026-10-08", 1.0, pss.now_ms(), raw={"营业额": "很多"})
        good = record("2026-10-09", 2.0, pss.now_ms())
        status, resp = self.a.sync(records=[bad, good])
        self.assertEqual(status, 200)
        statuses = {r["date"]: r["status"] for r in resp["results"]["records"]}
        self.assertEqual(statuses, {"2026-10-08": "invalid", "2026-10-09": "accepted"})

    def test_structural_errors(self):
        status, _ = self.a.sync(records=[record("2026-13-40", 1.0, 5)])
        self.assertEqual(status, 400)
        too_many = [record(f"2024-01-{(i % 28) + 1:02d}", 1.0, 5) for i in range(501)]
        status, _ = self.a.sync(records=too_many)
        self.assertEqual(status, 400)

    def test_body_limit(self):
        httpd, store = start_server(self.tmp / "small", extra_config={"max_body_bytes": 1000})
        try:
            client = Client(httpd.server_address[1], "device-eeeeeeee")
            records = [record(f"2024-02-{d:02d}", 1.0, 5, note="x" * 100) for d in range(1, 20)]
            status, _ = client.sync(records=records)
            self.assertEqual(status, 413)
        finally:
            httpd.shutdown()
            httpd.server_close()
            store.close()

    def test_gzip_request_and_response(self):
        t = pss.now_ms()
        records = [record(f"2024-03-{d:02d}", float(d), t, note="压缩测试" * 20) for d in range(1, 29)]
        body = {
            "protocol": 1,
            "device": {"id": "device-ffffffff", "name": "g", "platform": "test", "app_version": "t"},
            "since_rev": 0,
            "client_time": t,
            "push": {"records": records},
        }
        status, payload, resp = self.a.request("POST", "/api/v1/sync", body, gzip_body=True)
        self.assertEqual(status, 200)
        self.assertEqual(resp.getheader("Content-Encoding"), "gzip")
        self.assertEqual(len(payload["pull"]["records"]), 28)

    def test_keep_alive_after_auth_failure(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/v1/sync", body=b'{"x": 1}', headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        self.assertEqual(resp.status, 401)
        conn.close()

    # ── 备份 / 导出 ─────────────────────────

    def test_backup_rotation(self):
        backup_dir = self.tmp / "backups"
        for _ in range(4):
            self.store.backup(backup_dir, keep=2)
            time.sleep(1.05)
        self.assertEqual(len(list(backup_dir.glob("ledger-*.db"))), 2)

    def test_export_matches_desktop_schema(self):
        t = pss.now_ms()
        self.a.sync(
            records=[record("2026-10-10", 88.0, t, note="导出"), record("2026-10-11", 1.0, t)],
            settings={"data": SETTINGS_DATA, "modified_at": t, "base_rev": 0},
        )
        _, resp = self.a.sync(since_rev=0)
        rev = {r["date"]: r["rev"] for r in resp["pull"]["records"]}["2026-10-11"]
        self.a.sync(records=[record("2026-10-11", 0, t + 1, base_rev=rev, deleted=True)])

        out = self.tmp / "export.db"
        self.store.export_desktop_db(out)
        with contextlib.closing(sqlite3.connect(out)) as conn:
            rows = conn.execute("SELECT date, total_profit, note FROM DailyRecords").fetchall()
            config = dict(conn.execute("SELECT key, value FROM Config").fetchall())
        self.assertEqual(rows, [("2026-10-10", 88.0, "导出")])
        self.assertEqual(config["profit_formula"], "现金 + 零钱")
        self.assertEqual(json.loads(config["custom_field_tree"]), SETTINGS_DATA["custom_field_tree"])


@unittest.skipUnless(shutil.which("openssl"), "需要 openssl 生成测试证书")
class TlsTests(unittest.TestCase):
    def test_https_round_trip(self):
        tmp = Path(tempfile.mkdtemp(prefix="pss-tls-"))
        try:
            cert, key = tmp / "cert.pem", tmp / "key.pem"
            subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                 "-keyout", str(key), "-out", str(cert), "-days", "1", "-nodes", "-subj", "/CN=localhost",
                 "-addext", "subjectAltName=DNS:localhost"],
                check=True, capture_output=True,
            )
            server_ctx = pss.build_ssl_context({"tls_cert": str(cert), "tls_key": str(key)})
            httpd, store = start_server(tmp, ssl_context=server_ctx)
            try:
                client_ctx = ssl.create_default_context(cafile=str(cert))
                client = Client(httpd.server_address[1], "device-tls00000", ssl_context=client_ctx)
                status, payload, _ = client.request("GET", "/api/v1/status")
                self.assertEqual(status, 200)
                self.assertEqual(payload["service"], pss.SERVICE_NAME)

                # 非 TLS 连接会被静默丢弃，不影响后续请求
                plain = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=3)
                with self.assertRaises((http.client.HTTPException, ConnectionError, OSError)):
                    plain.request("GET", "/api/v1/ping")
                    plain.getresponse()
                plain.close()
                self.assertEqual(client.request("GET", "/api/v1/ping", auth=False)[0], 200)
            finally:
                httpd.shutdown()
                httpd.server_close()
                store.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


class ImportTests(unittest.TestCase):
    """导入桌面端账本：覆盖同名日期、整本替换、兼容旧版表结构。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pss-import-"))
        self.store = pss.LedgerStore(self.tmp / "ledger.db")

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _desktop_db(self, name: str, rows: list[tuple], with_note: bool = True, tree=None) -> Path:
        path = self.tmp / name
        with contextlib.closing(sqlite3.connect(path)) as conn, conn:
            note_col = ", note TEXT DEFAULT ''" if with_note else ""
            conn.execute(f"CREATE TABLE DailyRecords (date TEXT PRIMARY KEY, total_profit REAL, raw_data TEXT{note_col})")
            conn.execute("CREATE TABLE Config (key TEXT PRIMARY KEY, value TEXT)")
            for row in rows:
                conn.execute(f"INSERT INTO DailyRecords VALUES ({','.join('?' * len(row))})", row)
            conn.execute("INSERT INTO Config VALUES ('profit_formula', '现金 - 进货')")
            conn.execute("INSERT INTO Config VALUES (?, ?)", (
                "custom_field_tree" if tree is not None else "custom_fields",
                json.dumps(tree if tree is not None else ["现金", "进货"], ensure_ascii=False),
            ))
        return path

    def test_import_overwrites_and_replaces(self):
        client_time = pss.now_ms()
        request = pss.parse_sync_request({
            "device": {"id": "device-old00000"}, "since_rev": 0, "client_time": client_time,
            "push": {"records": [record("2026-01-01", 1.0, client_time - 5000), record("2026-01-09", 9.0, client_time - 5000)]},
        })
        self.store.sync(request)
        tree = [{"name": "营业额", "children": ["现金"]}, {"name": "成本", "children": ["进货"]}]
        source = self._desktop_db("family_ledger.db", [
            ("2026-01-01", 100.0, json.dumps({"营业额": {"现金": 120}, "成本": {"进货": "20"}}), "导入备注"),
            ("2026-01-02", 50.0, json.dumps({"营业额": {"现金": 50}}), ""),
        ], tree=tree)
        summary = pss.import_desktop_db(self.store, source, replace=True)
        self.assertEqual((summary["records"], summary["deleted"], summary["accepted"]), (2, 1, 3))
        self.assertEqual(summary["settings"], "accepted")
        out = self.tmp / "export.db"
        self.store.export_desktop_db(out)
        with contextlib.closing(sqlite3.connect(out)) as conn:
            rows = conn.execute("SELECT date, total_profit, raw_data, note FROM DailyRecords ORDER BY date").fetchall()
            formula = conn.execute("SELECT value FROM Config WHERE key='profit_formula'").fetchone()[0]
        self.assertEqual([r[0] for r in rows], ["2026-01-01", "2026-01-02"])
        self.assertEqual(rows[0][1], 100.0)
        self.assertEqual(json.loads(rows[0][2])["成本"]["进货"], 20.0)
        self.assertEqual(rows[0][3], "导入备注")
        self.assertEqual(formula, "现金 - 进货")

    def test_import_legacy_schema_without_note(self):
        source = self._desktop_db("old.db", [("2026-03-24", 401.0, json.dumps({"营业额": 2000.0, "成本": 1999.0}))],
                                  with_note=False)
        summary = pss.import_desktop_db(self.store, source)
        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(self.store.status()["settings_rev"], 2)

    def test_reimport_same_file_is_idempotent(self):
        source = self._desktop_db("same.db", [("2026-05-05", 5.0, json.dumps({"现金": 5}), "")])
        pss.import_desktop_db(self.store, source)
        rev = self.store.current_rev()
        summary = pss.import_desktop_db(self.store, source)
        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(self.store.current_rev(), rev)
