#!/usr/bin/env python3
"""
server/profit_sync_server.py
利润助手同步服务 — 只依赖 Python 标准库（3.10+），部署时无需 pip 安装任何包。

职责：
  - 通过 HTTPS + Bearer 令牌，为 Win10 桌面端和安卓端提供同一份账本的双向同步
  - SQLite 存储：每日记录按日期为主键，三项设置（分类树 / 字段列表 / 利润公式）作为一个文档
  - 全局递增修订号 rev：每次被接受的修改都会分配新的 rev，客户端凭 since_rev 增量拉取
  - 冲突处理：客户端提交的 base_rev 与服务器一致时直接接受；不一致时按“校正后的修改时间”
    后写入者胜，被覆盖的旧版本写入 history 表，任何一次覆盖都可以人工找回
  - 每日自动备份数据库，保留最近 N 份

用法：
  python3 profit_sync_server.py serve --config /etc/profit-helper/config.json
  python3 profit_sync_server.py gen-token
  python3 profit_sync_server.py export --config /etc/profit-helper/config.json --out ledger.db
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import gzip
import hashlib
import hmac
import html
import io
import json
import logging
import math
import os
import re
import secrets
import signal
import sqlite3
import ssl
import sys
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

SERVICE_NAME = "profit-helper-sync"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = 1

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
DOWNLOAD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")

# 小于等于该值的 modified_at 是“旧数据 / 默认值”标记（例如升级前的历史记录），不做时钟校正，
# 任何真实的修改时间都会比它新。
LEGACY_TIME_MAX = 10_000

MAX_PUSH_RECORDS = 500
DEFAULT_PULL_LIMIT = 500
MAX_PULL_LIMIT = 1000
MAX_RECORD_JSON_BYTES = 64 * 1024
MAX_SETTINGS_JSON_BYTES = 128 * 1024
MAX_NOTE_CHARS = 4000
MAX_NAME_CHARS = 100
MAX_FORMULA_CHARS = 4000

# 认证失败限流：同一 IP 在窗口期内失败次数达到上限后，暂时拒绝该 IP 的认证请求
AUTH_FAIL_WINDOW_SECONDS = 600
AUTH_FAIL_LIMIT = 20

TOKEN_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford Base32，去掉易混淆的 I L O U

DEFAULT_CONFIG: dict = {
    "listen_host": "0.0.0.0",
    "listen_port": 28443,
    # 支持写成 "credential:<名称>"，从 systemd LoadCredential 提供的目录读取
    "tls_cert": "",
    "tls_key": "",
    "data_dir": "/var/lib/profit-helper",
    # [{"name": "family", "sha256": "<令牌的 SHA-256 十六进制>"}]
    "tokens": [],
    "max_body_bytes": 8 * 1024 * 1024,
    "backup_keep": 30,
    "backup_interval_hours": 24,
    "max_connections": 24,
    # 可选：公开下载目录（放 APK / EXE），留空则不提供 /download
    "downloads_dir": "",
}

log = logging.getLogger(SERVICE_NAME)


# ─────────────────────────────────────────────
# 通用工具
# ─────────────────────────────────────────────

def now_ms() -> int:
    return int(time.time() * 1000)


def normalize_token(token: str) -> str:
    return "".join(str(token).split()).upper()


def hash_token(token: str) -> str:
    return hashlib.sha256(normalize_token(token).encode("utf-8")).hexdigest()


def generate_token() -> str:
    groups = ["".join(secrets.choice(TOKEN_ALPHABET) for _ in range(4)) for _ in range(6)]
    return "PH-" + "-".join(groups)


def dumps_compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class ApiError(Exception):
    """可直接返回给客户端的错误。"""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _bad_request(message: str) -> ApiError:
    return ApiError(400, "invalid_request", message)


def _as_int(value, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _bad_request(f"{name} 必须是整数")
    if value < minimum:
        raise _bad_request(f"{name} 不能小于 {minimum}")
    return value


def _as_number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _bad_request(f"{name} 必须是数字")
    number = float(value)
    if not math.isfinite(number):
        raise _bad_request(f"{name} 必须是有限数字")
    return number


def _as_name(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_NAME_CHARS:
        raise _bad_request(f"{name} 必须是 1~{MAX_NAME_CHARS} 个字符的名称")
    return value


def _valid_date(value) -> str:
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise _bad_request("date 必须是 YYYY-MM-DD 格式")
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise _bad_request(f"无效日期：{value}") from None
    return value


def _clean_raw_data(raw) -> dict:
    if not isinstance(raw, dict):
        raise _bad_request("raw_data 必须是对象")
    cleaned: dict = {}
    for key, value in raw.items():
        key = _as_name(key, "raw_data 的分类名")
        if isinstance(value, dict):
            cleaned[key] = {
                _as_name(child, "raw_data 的二级分类名"): _as_number(amount, f"{key}/{child}")
                for child, amount in value.items()
            }
        else:
            cleaned[key] = _as_number(value, key)
    if len(dumps_compact(cleaned).encode("utf-8")) > MAX_RECORD_JSON_BYTES:
        raise _bad_request("单条记录数据过大")
    return cleaned


def _clean_settings(data) -> dict:
    if not isinstance(data, dict):
        raise _bad_request("settings.data 必须是对象")

    tree = data.get("custom_field_tree")
    if not isinstance(tree, list):
        raise _bad_request("custom_field_tree 必须是数组")
    cleaned_tree = []
    for item in tree:
        if not isinstance(item, dict):
            raise _bad_request("custom_field_tree 的每一项必须是对象")
        children = item.get("children", [])
        if not isinstance(children, list):
            raise _bad_request("children 必须是数组")
        cleaned_tree.append({
            "name": _as_name(item.get("name"), "一级分类名"),
            "children": [_as_name(child, "二级分类名") for child in children],
        })

    fields = data.get("custom_fields", [])
    if not isinstance(fields, list):
        raise _bad_request("custom_fields 必须是数组")
    cleaned_fields = [_as_name(field, "字段名") for field in fields]

    formula = data.get("profit_formula")
    if not isinstance(formula, str) or len(formula) > MAX_FORMULA_CHARS:
        raise _bad_request("profit_formula 必须是字符串")

    cleaned = {
        "custom_field_tree": cleaned_tree,
        "custom_fields": cleaned_fields,
        "profit_formula": formula,
    }
    if len(dumps_compact(cleaned).encode("utf-8")) > MAX_SETTINGS_JSON_BYTES:
        raise _bad_request("设置数据过大")
    return cleaned


# ─────────────────────────────────────────────
# 存储层
# ─────────────────────────────────────────────

class LedgerStore:
    """账本存储。所有读写都在一把锁内完成，写入量很小，单连接足够。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    # ── 初始化 ──────────────────────────────

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS records (
                    date         TEXT PRIMARY KEY,
                    total_profit REAL    NOT NULL DEFAULT 0,
                    raw_data     TEXT    NOT NULL DEFAULT '{}',
                    note         TEXT    NOT NULL DEFAULT '',
                    deleted      INTEGER NOT NULL DEFAULT 0,
                    modified_at  INTEGER NOT NULL DEFAULT 0,
                    rev          INTEGER NOT NULL,
                    device_id    TEXT    NOT NULL DEFAULT '',
                    server_time  INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_records_rev ON records(rev);
                CREATE TABLE IF NOT EXISTS settings (
                    id          INTEGER PRIMARY KEY CHECK (id = 1),
                    data        TEXT    NOT NULL,
                    modified_at INTEGER NOT NULL,
                    rev         INTEGER NOT NULL,
                    device_id   TEXT    NOT NULL DEFAULT '',
                    server_time INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS history (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind         TEXT    NOT NULL,
                    item_key     TEXT    NOT NULL,
                    payload      TEXT    NOT NULL,
                    replaced_rev INTEGER NOT NULL,
                    replaced_at  INTEGER NOT NULL,
                    by_device    TEXT    NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_history_item ON history(kind, item_key);
                CREATE TABLE IF NOT EXISTS devices (
                    device_id   TEXT PRIMARY KEY,
                    name        TEXT    NOT NULL DEFAULT '',
                    platform    TEXT    NOT NULL DEFAULT '',
                    app_version TEXT    NOT NULL DEFAULT '',
                    first_seen  INTEGER NOT NULL,
                    last_seen   INTEGER NOT NULL
                );
            """)
            self._conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '1')")
            self._conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('rev', '0')")
            self._conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('ledger_id', ?)",
                (uuid.uuid4().hex,),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── 元数据 ──────────────────────────────

    def _meta(self, key: str) -> str:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else ""

    def _current_rev(self) -> int:
        return int(self._meta("rev") or 0)

    def _next_rev(self) -> int:
        rev = self._current_rev() + 1
        self._conn.execute("UPDATE meta SET value = ? WHERE key = 'rev'", (str(rev),))
        return rev

    @property
    def ledger_id(self) -> str:
        with self._lock:
            return self._meta("ledger_id")

    def current_rev(self) -> int:
        with self._lock:
            return self._current_rev()

    def live_dates(self) -> list[str]:
        with self._lock:
            return [row["date"] for row in self._conn.execute("SELECT date FROM records WHERE deleted = 0")]

    # ── 同步 ────────────────────────────────

    def sync(self, request: dict) -> dict:
        """处理一次“先推送、后拉取”的同步请求。request 已经过结构校验。"""
        server_now = now_ms()
        client_time = request["client_time"]
        offset = server_now - client_time if client_time > LEGACY_TIME_MAX else 0
        device = request["device"]

        with self._lock:
            ledger_id = self._meta("ledger_id")
            server_rev = self._current_rev()
            known_ledger = request["ledger_id"]
            if (known_ledger and known_ledger != ledger_id) or request["since_rev"] > server_rev:
                # 服务器被重建或从旧备份恢复：客户端需要重置同步状态后全量重新同步
                return {
                    "ok": True,
                    "resync_required": True,
                    "ledger_id": ledger_id,
                    "server_rev": server_rev,
                    "server_time": server_now,
                }

            self._conn.execute("BEGIN IMMEDIATE")
            try:
                record_results = [
                    self._apply_record(item, device["id"], offset, server_now)
                    for item in request["records"]
                ]
                settings_result = None
                if request["settings"] is not None:
                    settings_result = self._apply_settings(request["settings"], device["id"], offset, server_now)
                self._touch_device(device, server_now)
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

            pull = self._pull(request["since_rev"], request["pull_limit"])
            return {
                "ok": True,
                "resync_required": False,
                "ledger_id": ledger_id,
                "server_rev": self._current_rev(),
                "server_time": server_now,
                "results": {"records": record_results, "settings": settings_result},
                "pull": pull,
            }

    @staticmethod
    def _normalize_time(value: int, offset: int, server_now: int) -> int:
        if value <= LEGACY_TIME_MAX:
            return value
        return max(LEGACY_TIME_MAX + 1, min(value + offset, server_now))

    def _apply_record(self, item: dict, device_id: str, offset: int, server_now: int) -> dict:
        date = item["date"]
        if "error" in item:
            return {"date": date, "status": "invalid", "message": item["error"]}

        modified_at = self._normalize_time(item["modified_at"], offset, server_now)
        row = self._conn.execute("SELECT * FROM records WHERE date = ?", (date,)).fetchone()

        if row is not None:
            if self._same_record(row, item):
                return {"date": date, "status": "accepted", "rev": row["rev"], "modified_at": row["modified_at"]}
            if item["base_rev"] != row["rev"] and modified_at <= row["modified_at"]:
                return {"date": date, "status": "rejected", "current": self._record_out(row)}
            self._archive("record", date, self._record_out(row), row["rev"], device_id, server_now)
        # 服务器上不存在的日期即使是删除也写入墓碑，保证其他设备上未同步过的旧副本同样被删除

        rev = self._next_rev()
        self._conn.execute(
            """
            INSERT INTO records (date, total_profit, raw_data, note, deleted, modified_at, rev, device_id, server_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                total_profit = excluded.total_profit,
                raw_data     = excluded.raw_data,
                note         = excluded.note,
                deleted      = excluded.deleted,
                modified_at  = excluded.modified_at,
                rev          = excluded.rev,
                device_id    = excluded.device_id,
                server_time  = excluded.server_time
            """,
            (
                date,
                0.0 if item["deleted"] else item["total_profit"],
                "{}" if item["deleted"] else dumps_compact(item["raw_data"]),
                "" if item["deleted"] else item["note"],
                1 if item["deleted"] else 0,
                modified_at,
                rev,
                device_id,
                server_now,
            ),
        )
        return {"date": date, "status": "accepted", "rev": rev, "modified_at": modified_at}

    def _apply_settings(self, item: dict, device_id: str, offset: int, server_now: int) -> dict:
        modified_at = self._normalize_time(item["modified_at"], offset, server_now)
        row = self._conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()

        if row is not None:
            if canonical_json(json.loads(row["data"])) == canonical_json(item["data"]):
                return {"status": "accepted", "rev": row["rev"], "modified_at": row["modified_at"]}
            if item["base_rev"] != row["rev"] and modified_at <= row["modified_at"]:
                return {"status": "rejected", "current": self._settings_out(row)}
            self._archive("settings", "settings", self._settings_out(row), row["rev"], device_id, server_now)

        rev = self._next_rev()
        self._conn.execute(
            """
            INSERT INTO settings (id, data, modified_at, rev, device_id, server_time)
            VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                data        = excluded.data,
                modified_at = excluded.modified_at,
                rev         = excluded.rev,
                device_id   = excluded.device_id,
                server_time = excluded.server_time
            """,
            (dumps_compact(item["data"]), modified_at, rev, device_id, server_now),
        )
        return {"status": "accepted", "rev": rev, "modified_at": modified_at}

    def _pull(self, since_rev: int, limit: int) -> dict:
        rows = self._conn.execute(
            "SELECT * FROM records WHERE rev > ? ORDER BY rev ASC LIMIT ?",
            (since_rev, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_since = rows[-1]["rev"] if has_more else self._current_rev()

        settings = None
        settings_row = self._conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        if settings_row is not None and settings_row["rev"] > since_rev:
            settings = self._settings_out(settings_row)

        return {
            "records": [self._record_out(row) for row in rows],
            "settings": settings,
            "next_since": next_since,
            "has_more": has_more,
        }

    @staticmethod
    def _same_record(row: sqlite3.Row, item: dict) -> bool:
        if bool(row["deleted"]) != item["deleted"]:
            return False
        if item["deleted"]:
            return True
        return (
            abs(float(row["total_profit"]) - item["total_profit"]) < 1e-9
            and row["note"] == item["note"]
            and canonical_json(json.loads(row["raw_data"])) == canonical_json(item["raw_data"])
        )

    @staticmethod
    def _record_out(row: sqlite3.Row) -> dict:
        return {
            "date": row["date"],
            "total_profit": float(row["total_profit"]),
            "raw_data": json.loads(row["raw_data"]),
            "note": row["note"],
            "deleted": bool(row["deleted"]),
            "modified_at": int(row["modified_at"]),
            "rev": int(row["rev"]),
            "device_id": row["device_id"],
        }

    @staticmethod
    def _settings_out(row: sqlite3.Row) -> dict:
        return {
            "data": json.loads(row["data"]),
            "modified_at": int(row["modified_at"]),
            "rev": int(row["rev"]),
            "device_id": row["device_id"],
        }

    def _archive(self, kind: str, key: str, payload: dict, rev: int, device_id: str, server_now: int) -> None:
        self._conn.execute(
            """
            INSERT INTO history (kind, item_key, payload, replaced_rev, replaced_at, by_device)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (kind, key, dumps_compact(payload), rev, server_now, device_id),
        )

    def _touch_device(self, device: dict, server_now: int) -> None:
        self._conn.execute(
            """
            INSERT INTO devices (device_id, name, platform, app_version, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                name        = excluded.name,
                platform    = excluded.platform,
                app_version = excluded.app_version,
                last_seen   = excluded.last_seen
            """,
            (device["id"], device["name"], device["platform"], device["app_version"], server_now, server_now),
        )

    # ── 状态 / 备份 / 导出 ──────────────────

    def status(self) -> dict:
        with self._lock:
            counts = self._conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN deleted = 0 THEN 1 ELSE 0 END), 0) AS live,
                    COALESCE(SUM(CASE WHEN deleted = 1 THEN 1 ELSE 0 END), 0) AS tombstones,
                    MIN(CASE WHEN deleted = 0 THEN date END) AS first_date,
                    MAX(CASE WHEN deleted = 0 THEN date END) AS last_date
                FROM records
                """
            ).fetchone()
            settings_row = self._conn.execute("SELECT rev, modified_at FROM settings WHERE id = 1").fetchone()
            devices = [
                {
                    "name": row["name"],
                    "platform": row["platform"],
                    "app_version": row["app_version"],
                    "last_seen": row["last_seen"],
                }
                for row in self._conn.execute(
                    "SELECT name, platform, app_version, last_seen FROM devices ORDER BY last_seen DESC LIMIT 20"
                )
            ]
            return {
                "ok": True,
                "service": SERVICE_NAME,
                "version": SERVER_VERSION,
                "ledger_id": self._meta("ledger_id"),
                "server_rev": self._current_rev(),
                "server_time": now_ms(),
                "record_count": int(counts["live"]),
                "tombstone_count": int(counts["tombstones"]),
                "first_date": counts["first_date"],
                "last_date": counts["last_date"],
                "settings_rev": int(settings_row["rev"]) if settings_row else 0,
                "devices": devices,
            }

    def backup(self, backup_dir: str | Path, keep: int) -> Path:
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        target = backup_dir / f"ledger-{stamp}.db"
        temp = target.with_suffix(".db.part")
        with self._lock, contextlib.closing(sqlite3.connect(str(temp))) as dest:
            self._conn.backup(dest)
        os.replace(temp, target)
        backups = sorted(backup_dir.glob("ledger-*.db"))
        for old in backups[:-keep] if keep > 0 else []:
            old.unlink(missing_ok=True)
        return target

    def export_desktop_db(self, out_path: str | Path) -> Path:
        """导出为桌面端 family_ledger.db 兼容格式（DailyRecords / Config / android_metadata）。"""
        out_path = Path(out_path)
        if out_path.exists():
            raise FileExistsError(f"目标文件已存在：{out_path}")
        with self._lock:
            records = self._conn.execute(
                "SELECT date, total_profit, raw_data, note FROM records WHERE deleted = 0 ORDER BY date"
            ).fetchall()
            settings_row = self._conn.execute("SELECT data FROM settings WHERE id = 1").fetchone()
        settings = json.loads(settings_row["data"]) if settings_row else {}

        with contextlib.closing(sqlite3.connect(str(out_path))) as conn, conn:
            conn.execute("""
                CREATE TABLE DailyRecords (
                    date          TEXT PRIMARY KEY,
                    total_profit  REAL DEFAULT 0.0,
                    raw_data      TEXT DEFAULT '{}',
                    note          TEXT DEFAULT ''
                )
            """)
            conn.execute("CREATE TABLE Config (key TEXT PRIMARY KEY, value TEXT DEFAULT '')")
            conn.execute("CREATE TABLE android_metadata (locale TEXT)")
            conn.execute("INSERT INTO android_metadata (locale) VALUES ('zh_CN')")
            conn.executemany(
                "INSERT INTO DailyRecords (date, total_profit, raw_data, note) VALUES (?, ?, ?, ?)",
                [(r["date"], r["total_profit"], r["raw_data"], r["note"]) for r in records],
            )
            if settings:
                conn.executemany(
                    "INSERT INTO Config (key, value) VALUES (?, ?)",
                    [
                        ("custom_field_tree", dumps_compact(settings.get("custom_field_tree", []))),
                        ("custom_fields", dumps_compact(settings.get("custom_fields", []))),
                        ("profit_formula", settings.get("profit_formula", "")),
                    ],
                )
        return out_path


# ─────────────────────────────────────────────
# 请求解析
# ─────────────────────────────────────────────

def parse_sync_request(body) -> dict:
    """校验同步请求的整体结构。单条记录的内容错误不会让整个请求失败，而是标记为 invalid。"""
    if not isinstance(body, dict):
        raise _bad_request("请求体必须是 JSON 对象")

    protocol = body.get("protocol", PROTOCOL_VERSION)
    if protocol != PROTOCOL_VERSION:
        raise ApiError(400, "unsupported_protocol", f"不支持的同步协议版本：{protocol}，请升级应用")

    device = body.get("device")
    if not isinstance(device, dict):
        raise _bad_request("缺少 device 信息")
    device_id = device.get("id")
    if not isinstance(device_id, str) or not DEVICE_ID_RE.match(device_id):
        raise _bad_request("device.id 格式不正确")
    parsed_device = {
        "id": device_id,
        "name": str(device.get("name", ""))[:60],
        "platform": str(device.get("platform", ""))[:20],
        "app_version": str(device.get("app_version", ""))[:20],
    }

    ledger_id = body.get("ledger_id") or ""
    if not isinstance(ledger_id, str) or len(ledger_id) > 64:
        raise _bad_request("ledger_id 格式不正确")

    push = body.get("push") or {}
    if not isinstance(push, dict):
        raise _bad_request("push 必须是对象")
    raw_records = push.get("records") or []
    if not isinstance(raw_records, list):
        raise _bad_request("push.records 必须是数组")
    if len(raw_records) > MAX_PUSH_RECORDS:
        raise _bad_request(f"单次最多推送 {MAX_PUSH_RECORDS} 条记录")

    records = []
    seen_dates: set[str] = set()
    for raw in raw_records:
        if not isinstance(raw, dict):
            raise _bad_request("push.records 的每一项必须是对象")
        date = _valid_date(raw.get("date"))
        if date in seen_dates:
            raise _bad_request(f"同一请求中重复的日期：{date}")
        seen_dates.add(date)
        modified_at = _as_int(raw.get("modified_at"), "modified_at")
        base_rev = _as_int(raw.get("base_rev", 0), "base_rev")
        deleted = raw.get("deleted", False)
        if not isinstance(deleted, bool):
            raise _bad_request("deleted 必须是布尔值")
        item = {"date": date, "modified_at": modified_at, "base_rev": base_rev, "deleted": deleted}
        try:
            if deleted:
                item.update({"total_profit": 0.0, "raw_data": {}, "note": ""})
            else:
                note = raw.get("note", "")
                if not isinstance(note, str) or len(note) > MAX_NOTE_CHARS:
                    raise _bad_request(f"备注必须是不超过 {MAX_NOTE_CHARS} 字的文本")
                item.update({
                    "total_profit": _as_number(raw.get("total_profit"), "total_profit"),
                    "raw_data": _clean_raw_data(raw.get("raw_data", {})),
                    "note": note,
                })
        except ApiError as exc:
            item["error"] = exc.message
        records.append(item)

    settings = None
    raw_settings = push.get("settings")
    if raw_settings is not None:
        if not isinstance(raw_settings, dict):
            raise _bad_request("push.settings 必须是对象")
        settings = {
            "data": _clean_settings(raw_settings.get("data")),
            "modified_at": _as_int(raw_settings.get("modified_at"), "settings.modified_at"),
            "base_rev": _as_int(raw_settings.get("base_rev", 0), "settings.base_rev"),
        }

    client_time = body.get("client_time", 0)
    if isinstance(client_time, bool) or not isinstance(client_time, int) or client_time < 0:
        client_time = 0

    pull_limit = body.get("pull_limit", DEFAULT_PULL_LIMIT)
    if isinstance(pull_limit, bool) or not isinstance(pull_limit, int):
        pull_limit = DEFAULT_PULL_LIMIT
    pull_limit = max(1, min(pull_limit, MAX_PULL_LIMIT))

    return {
        "device": parsed_device,
        "ledger_id": ledger_id,
        "since_rev": _as_int(body.get("since_rev", 0), "since_rev"),
        "client_time": client_time,
        "records": records,
        "settings": settings,
        "pull_limit": pull_limit,
    }


# ─────────────────────────────────────────────
# 导入桌面端账本
# ─────────────────────────────────────────────

def _sanitize_number(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _sanitize_raw(raw) -> dict:
    """与桌面端 sanitize_raw_data 相同：非数字金额按 0 处理。"""
    if not isinstance(raw, dict):
        return {}
    cleaned: dict = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name:
            continue
        if isinstance(value, dict):
            cleaned[name] = {str(c).strip(): _sanitize_number(v) for c, v in value.items() if str(c).strip()}
        else:
            cleaned[name] = _sanitize_number(value)
    return cleaned


def _settings_from_config(config: dict) -> dict | None:
    """从桌面端 Config 表组装同步设置（兼容旧版只有 custom_fields 字符串列表的账本）。"""
    formula = (config.get("profit_formula") or "").strip()
    tree_text = config.get("custom_field_tree") or config.get("custom_fields") or ""
    if not formula or not tree_text:
        return None
    try:
        items = json.loads(tree_text)
    except json.JSONDecodeError:
        return None
    tree: list[dict] = []
    seen: set[str] = set()
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str):
            name, children = item.strip(), []
        elif isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            raw_children = item.get("children", [])
            children = [str(c).strip() for c in raw_children if str(c).strip()] if isinstance(raw_children, list) else []
        else:
            continue
        if not name or name in seen:
            continue
        seen.add(name)
        tree.append({"name": name, "children": list(dict.fromkeys(children))})
    if not tree:
        return None
    fields = list(dict.fromkeys(n for item in tree for n in [item["name"], *item["children"]]))
    return {"custom_field_tree": tree, "custom_fields": fields, "profit_formula": formula}


def read_desktop_db(path: str | Path) -> tuple[list[dict], dict | None]:
    """只读打开桌面端 family_ledger.db，返回 (记录列表, 同步设置)。"""
    path = Path(path)
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not {"DailyRecords", "Config"} <= tables:
            raise ValueError("这不是利润助手账本：缺少 DailyRecords 或 Config 表")
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(DailyRecords)")}
        note_column = "note" if "note" in columns else "''"
        rows = conn.execute(
            f"SELECT date, total_profit, raw_data, {note_column} AS note FROM DailyRecords ORDER BY date"
        ).fetchall()
        config = {row["key"]: row["value"] or "" for row in conn.execute("SELECT key, value FROM Config")}
    records = []
    for row in rows:
        try:
            raw = json.loads(row["raw_data"] or "{}")
        except json.JSONDecodeError:
            raise ValueError(f"{row['date']} 的 raw_data 不是合法 JSON") from None
        records.append({
            "date": str(row["date"]),
            "total_profit": _sanitize_number(row["total_profit"]),
            "raw_data": _sanitize_raw(raw),
            "note": row["note"] or "",
        })
    return records, _settings_from_config(config)


def import_desktop_db(store: "LedgerStore", path: str | Path, replace: bool = False) -> dict:
    """
    把桌面端账本导入服务器。修改时间记为“现在”，因此会覆盖服务器和各设备上较旧的同名日期；
    replace=True 时，服务器上有而文件里没有的日期会被标记删除（整本替换）。
    """
    records, settings = read_desktop_db(path)
    now = now_ms()
    device = {
        "id": "import-" + hashlib.sha256(str(Path(path).name).encode("utf-8")).hexdigest()[:16],
        "name": f"导入：{Path(path).name}"[:60],
        "platform": "import",
        "app_version": SERVER_VERSION,
    }
    changes = [dict(r, deleted=False, modified_at=now, base_rev=0) for r in records]
    if replace:
        imported = {r["date"] for r in records}
        changes += [
            {"date": d, "deleted": True, "modified_at": now, "base_rev": 0}
            for d in store.live_dates() if d not in imported
        ]

    summary = {"records": len(records), "deleted": len(changes) - len(records), "accepted": 0,
               "rejected": 0, "invalid": [], "settings": "skipped"}
    batches = [changes[i:i + MAX_PUSH_RECORDS] for i in range(0, len(changes), MAX_PUSH_RECORDS)] or [[]]
    for index, batch in enumerate(batches):
        push_settings = None
        if index == len(batches) - 1 and settings is not None:
            push_settings = {"data": settings, "modified_at": now, "base_rev": 0}
        request = parse_sync_request({
            "protocol": PROTOCOL_VERSION,
            "device": device,
            "since_rev": store.current_rev(),
            "client_time": now,
            "push": {"records": batch, "settings": push_settings},
            "pull_limit": 1,
        })
        response = store.sync(request)
        for result in response["results"]["records"]:
            if result["status"] == "accepted":
                summary["accepted"] += 1
            elif result["status"] == "rejected":
                summary["rejected"] += 1
            else:
                summary["invalid"].append((result["date"], result.get("message", "")))
        if response["results"]["settings"] is not None:
            summary["settings"] = response["results"]["settings"]["status"]
    summary["server_rev"] = store.current_rev()
    return summary


# ─────────────────────────────────────────────
# HTTP 层
# ─────────────────────────────────────────────

class AuthLimiter:
    """记录每个 IP 的认证失败次数，防止暴力尝试。"""

    def __init__(self, window: int = AUTH_FAIL_WINDOW_SECONDS, limit: int = AUTH_FAIL_LIMIT):
        self._window = window
        self._limit = limit
        self._failures: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _prune(self, ip: str, now: float) -> deque:
        bucket = self._failures.setdefault(ip, deque())
        while bucket and now - bucket[0] > self._window:
            bucket.popleft()
        return bucket

    def blocked(self, ip: str) -> bool:
        with self._lock:
            return len(self._prune(ip, time.monotonic())) >= self._limit

    def fail(self, ip: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._prune(ip, now).append(now)
            if len(self._failures) > 10_000:  # 防止被大量伪造来源撑大
                for key in [k for k, v in self._failures.items() if not v][:5_000]:
                    self._failures.pop(key, None)


class SyncHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(self, address, store: LedgerStore, config: dict, ssl_context: ssl.SSLContext | None):
        self.store = store
        self.config = config
        self.ssl_context = ssl_context
        self.token_hashes = [str(t.get("sha256", "")).lower() for t in config.get("tokens", []) if t.get("sha256")]
        self.auth_limiter = AuthLimiter()
        self._slots = threading.BoundedSemaphore(int(config.get("max_connections", 24)))
        super().__init__(address, SyncRequestHandler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def finish_request(self, request, client_address):
        request.settimeout(20)
        if self.ssl_context is None:
            self.RequestHandlerClass(request, client_address, self)
            return
        try:
            tls_sock = self.ssl_context.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):
            return  # 握手失败（扫描器、协议不符），静默丢弃
        try:
            self.RequestHandlerClass(tls_sock, client_address, self)
        finally:
            try:
                tls_sock.close()
            except OSError:
                pass

    def check_token(self, header_value: str | None) -> bool:
        if not header_value or not header_value.startswith("Bearer "):
            return False
        digest = hash_token(header_value[7:])
        matched = False
        for expected in self.token_hashes:
            matched |= hmac.compare_digest(digest, expected)
        return matched


STATUS_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>利润助手同步服务</title>
<style>
body{{margin:0;font-family:-apple-system,"Microsoft YaHei UI","PingFang SC",sans-serif;background:#f4f5fa;color:#1f2333}}
main{{max-width:520px;margin:12vh auto;padding:32px;background:#fff;border:1px solid #e6e8f0;border-radius:16px}}
h1{{font-size:22px;margin:0 0 8px}} p{{color:#6b7085;line-height:1.7;margin:6px 0}}
.dot{{display:inline-block;width:9px;height:9px;border-radius:50%;background:#12a150;margin-right:8px}}
a{{color:#5b4fe0}}
</style></head><body><main>
<h1><span class="dot"></span>利润助手同步服务运行中</h1>
<p>版本 {version}。在 Win10 桌面端或安卓端的“设置 → 云同步”中填写服务器地址和同步密钥即可使用。</p>
{downloads}
</main></body></html>
"""


class SyncRequestHandler(BaseHTTPRequestHandler):
    server: SyncHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "ProfitHelperSync/" + SERVER_VERSION
    sys_version = ""

    def log_message(self, format, *args):  # noqa: A002 - 覆盖父类签名
        pass  # 统一在 _log_access 中输出，不记录请求体与令牌

    # ── 路由 ────────────────────────────────

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method: str) -> None:
        started = time.monotonic()
        self._status_sent = 0
        self._bytes_sent = 0
        self._body_read = method != "POST"
        path = urlsplit(self.path).path
        try:
            if method == "GET" and path in ("/", "/index.html"):
                self._send_html(200, self._status_page())
            elif method == "GET" and path == "/api/v1/ping":
                self._send_json(200, {
                    "ok": True,
                    "service": SERVICE_NAME,
                    "version": SERVER_VERSION,
                    "protocol": PROTOCOL_VERSION,
                    "server_time": now_ms(),
                })
            elif method == "GET" and path == "/api/v1/status":
                self._require_auth()
                self._send_json(200, self.server.store.status())
            elif method == "POST" and path == "/api/v1/sync":
                self._require_auth()
                request = parse_sync_request(self._read_json())
                self._send_json(200, self.server.store.sync(request))
            elif method == "GET" and (path == "/download" or path.startswith("/download/")):
                self._serve_download(path)
            else:
                raise ApiError(404, "not_found", "接口不存在")
        except ApiError as exc:
            if not self._body_read:
                self.close_connection = True  # 未读取的请求体会污染长连接上的下一个请求
            self._send_json(exc.status, {"ok": False, "error": exc.code, "message": exc.message})
        except (ConnectionError, TimeoutError, ssl.SSLError):
            self.close_connection = True
        except Exception:
            log.exception("处理请求出错：%s %s", method, path)
            try:
                self._send_json(500, {"ok": False, "error": "server_error", "message": "服务器内部错误"})
            except OSError:
                self.close_connection = True
        finally:
            elapsed = (time.monotonic() - started) * 1000
            log.info("%s %s %s %s %dB %.0fms", self.client_address[0], method, path,
                     self._status_sent, self._bytes_sent, elapsed)

    # ── 认证与请求体 ────────────────────────

    def _require_auth(self) -> None:
        ip = self.client_address[0]
        if self.server.auth_limiter.blocked(ip):
            raise ApiError(429, "too_many_attempts", "认证失败次数过多，请 10 分钟后再试")
        if not self.server.check_token(self.headers.get("Authorization")):
            self.server.auth_limiter.fail(ip)
            raise ApiError(401, "unauthorized", "同步密钥不正确")

    def _read_json(self):
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(411, "length_required", "请求必须带 Content-Length")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise ApiError(411, "length_required", "请求必须带 Content-Length") from None
        max_body = int(self.server.config.get("max_body_bytes", DEFAULT_CONFIG["max_body_bytes"]))
        if length < 0 or length > max_body:
            self.close_connection = True
            raise ApiError(413, "too_large", "请求体过大")
        data = self.rfile.read(length)
        if len(data) != length:
            raise ConnectionError("请求体不完整")
        self._body_read = True

        if self.headers.get("Content-Encoding", "").lower() == "gzip":
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as zipped:
                    data = zipped.read(max_body + 1)
            except (OSError, EOFError):
                raise _bad_request("gzip 请求体无法解压") from None
            if len(data) > max_body:
                raise ApiError(413, "too_large", "请求体过大")
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise _bad_request("请求体不是合法的 JSON") from None

    # ── 响应 ────────────────────────────────

    def _send_bytes(self, status: int, body: bytes, content_type: str, compressible: bool = True,
                    extra_headers: dict | None = None) -> None:
        gzip_ok = compressible and len(body) > 1024 and "gzip" in self.headers.get("Accept-Encoding", "").lower()
        if gzip_ok:
            body = gzip.compress(body, compresslevel=6)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if gzip_ok:
            self.send_header("Content-Encoding", "gzip")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self._status_sent = status
        self._bytes_sent = len(body)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send_bytes(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                         "application/json; charset=utf-8")

    def _send_html(self, status: int, text: str) -> None:
        self._send_bytes(status, text.encode("utf-8"), "text/html; charset=utf-8",
                         extra_headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

    def _status_page(self) -> str:
        files = self._download_files()
        downloads = ""
        if files:
            items = "".join(
                f'<li><a href="/download/{html.escape(f.name)}">{html.escape(f.name)}</a>'
                f'（{f.stat().st_size / 1024 / 1024:.1f} MB）</li>'
                for f in files
            )
            downloads = f"<p>安装包下载：</p><ul>{items}</ul>"
        return STATUS_PAGE.format(version=SERVER_VERSION, downloads=downloads)

    def _downloads_dir(self) -> Path | None:
        configured = self.server.config.get("downloads_dir") or ""
        if not configured:
            return None
        path = Path(configured)
        return path if path.is_dir() else None

    def _download_files(self) -> list[Path]:
        folder = self._downloads_dir()
        if folder is None:
            return []
        return sorted(
            (p for p in folder.iterdir() if p.is_file() and DOWNLOAD_NAME_RE.match(p.name)),
            key=lambda p: p.name,
        )

    def _serve_download(self, path: str) -> None:
        folder = self._downloads_dir()
        if folder is None:
            raise ApiError(404, "not_found", "接口不存在")
        name = unquote(path[len("/download"):].lstrip("/"))
        if not name:
            self._send_html(200, self._status_page())
            return
        if not DOWNLOAD_NAME_RE.match(name):
            raise ApiError(404, "not_found", "文件不存在")
        target = folder / name
        if not target.is_file():
            raise ApiError(404, "not_found", "文件不存在")

        content_type = {
            ".apk": "application/vnd.android.package-archive",
            ".zip": "application/zip",
        }.get(target.suffix.lower(), "application/octet-stream")
        size = target.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        with target.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                self.wfile.write(chunk)
        self._status_sent = 200
        self._bytes_sent = size


# ─────────────────────────────────────────────
# 配置与启动
# ─────────────────────────────────────────────

def load_config(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        loaded = json.load(handle)
    config = dict(DEFAULT_CONFIG)
    config.update(loaded)
    return config


def resolve_path(value: str) -> str:
    if value.startswith("credential:"):
        cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
        if not cred_dir:
            raise RuntimeError(f"配置使用了 {value}，但当前不是由 systemd LoadCredential 启动")
        return str(Path(cred_dir) / value.split(":", 1)[1])
    return value


def build_ssl_context(config: dict) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(resolve_path(config["tls_cert"]), resolve_path(config["tls_key"]))
    context.set_alpn_protocols(["http/1.1"])
    return context


def _backup_loop(store: LedgerStore, config: dict, stop: threading.Event) -> None:
    backup_dir = Path(config["data_dir"]) / "backups"
    interval = max(1, int(config.get("backup_interval_hours", 24))) * 3600
    keep = int(config.get("backup_keep", 30))
    while not stop.is_set():
        try:
            existing = sorted(backup_dir.glob("ledger-*.db"))
            newest = existing[-1].stat().st_mtime if existing else 0
            if time.time() - newest >= interval:
                target = store.backup(backup_dir, keep)
                log.info("已完成每日备份：%s", target.name)
        except Exception:
            log.exception("自动备份失败")
        stop.wait(3600)


def serve(config: dict, insecure_http: bool = False) -> None:
    if not config.get("tokens"):
        raise SystemExit("配置中没有任何同步令牌（tokens），拒绝启动")
    if insecure_http and config["listen_host"] not in ("127.0.0.1", "::1", "localhost"):
        raise SystemExit("--insecure-http 只允许监听本机地址")

    store = LedgerStore(Path(config["data_dir"]) / "ledger.db")
    ssl_context = None if insecure_http else build_ssl_context(config)
    httpd = SyncHTTPServer((config["listen_host"], int(config["listen_port"])), store, config, ssl_context)

    stop = threading.Event()
    threading.Thread(target=_backup_loop, args=(store, config, stop), daemon=True, name="backup").start()

    def _shutdown(signum, _frame):
        log.info("收到信号 %s，正在停止服务", signum)
        stop.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    scheme = "http" if insecure_http else "https"
    log.info("利润助手同步服务 %s 已启动：%s://%s:%s，账本 %s",
             SERVER_VERSION, scheme, config["listen_host"], config["listen_port"], store.ledger_id[:8])
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        httpd.server_close()
        store.close()
        log.info("服务已停止")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="利润助手同步服务")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_cmd = sub.add_parser("serve", help="启动同步服务")
    serve_cmd.add_argument("--config", required=True)
    serve_cmd.add_argument("--insecure-http", action="store_true", help="仅用于本机测试：不启用 TLS")

    sub.add_parser("gen-token", help="生成一个新的同步密钥及其 SHA-256")

    sub.add_parser("hash-token", help="从标准输入读取密钥并输出 SHA-256")

    export_cmd = sub.add_parser("export", help="导出为桌面端兼容的 family_ledger.db")
    export_cmd.add_argument("--config", required=True)
    export_cmd.add_argument("--out", required=True)

    backup_cmd = sub.add_parser("backup", help="立即备份一次数据库")
    backup_cmd.add_argument("--config", required=True)

    import_cmd = sub.add_parser("import", help="导入桌面端 family_ledger.db（覆盖同名日期）")
    import_cmd.add_argument("--config", required=True)
    import_cmd.add_argument("--file", required=True)
    import_cmd.add_argument("--replace", action="store_true", help="整本替换：删除文件里没有的日期")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)

    if args.command == "serve":
        serve(load_config(args.config), insecure_http=args.insecure_http)
    elif args.command == "gen-token":
        token = generate_token()
        print(json.dumps({"token": token, "sha256": hash_token(token)}, ensure_ascii=False))
    elif args.command == "hash-token":
        print(hash_token(sys.stdin.readline().strip()))
    elif args.command == "export":
        config = load_config(args.config)
        store = LedgerStore(Path(config["data_dir"]) / "ledger.db")
        print(store.export_desktop_db(args.out))
        store.close()
    elif args.command == "backup":
        config = load_config(args.config)
        store = LedgerStore(Path(config["data_dir"]) / "ledger.db")
        print(store.backup(Path(config["data_dir"]) / "backups", int(config.get("backup_keep", 30))))
        store.close()
    elif args.command == "import":
        config = load_config(args.config)
        store = LedgerStore(Path(config["data_dir"]) / "ledger.db")
        try:
            before = store.backup(Path(config["data_dir"]) / "backups", int(config.get("backup_keep", 30)))
            summary = import_desktop_db(store, args.file, replace=args.replace)
        finally:
            store.close()
        summary["backup_before_import"] = before.name
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1 if summary["invalid"] or summary["rejected"] else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
