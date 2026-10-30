"""
core/database.py
SQLite DAO 层 — 负责所有数据库交互，与 UI 完全解耦。

表结构：
  DailyRecords (date TEXT PRIMARY KEY, total_profit REAL, raw_data TEXT, note TEXT)
    - date: ISO 日期字符串 '2024-01-15'
    - total_profit: 当日利润（缓存计算结果，避免每次重算）
    - raw_data: JSON 字符串，存储所有自定义基数项，例如
                '{"营业额": {"现金": 1000}, "成本": {"小肠": 200}, "零钱": 50}'
    - note: 备注文本，独立存储，避免影响利润计算

  Config (key TEXT PRIMARY KEY, value TEXT)
    - 存储用户自定义配置：基数项列表、利润公式字符串等
    - 例如 key='profit_formula', value='营业额 - 成本'
         key='custom_fields', value='["营业额","成本","零钱"]'

  云同步元数据（导出账本时会被移除，不影响与旧版本互相导入）：
  SyncRecordState (date, modified_at, base_rev, dirty, deleted)
    - 记录每一天在本机的修改时间、最后一次与服务器一致时的修订号、是否待上传、是否已删除
  SyncMeta (key, value)
    - 设备 ID、服务器地址、同步密钥、已拉取到的修订号、设置的同步状态等
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.app_paths import data_file_path
from core.field_config import (
    DEFAULT_FIELD_TREE,
    DEFAULT_PROFIT_FORMULA,
    build_formula_variables,
    flatten_formula_fields,
    load_field_tree_from_json,
    migrate_raw_data,
)
from core.formula_engine import FormulaEngine, FormulaError


# 默认数据库文件路径：可执行文件同级目录，跨平台兼容
DEFAULT_DB_PATH = data_file_path("family_ledger.db")

# 参与云同步的配置项；history_start_date 等界面偏好只保存在本机
SYNCED_CONFIG_KEYS = ("custom_field_tree", "custom_fields", "profit_formula")

# 升级前已有数据的“修改时间”标记：比任何真实修改都旧，冲突时让位于真实修改
LEGACY_MODIFIED_AT = 1

# 与同步服务器的限制保持一致，超出的数据无法上传
NOTE_MAX_CHARS = 4000
NAME_MAX_CHARS = 100
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_SYNC_TABLES = ("SyncRecordState", "SyncMeta")


def now_ms() -> int:
    return int(time.time() * 1000)


def _clean_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number and abs(number) != float("inf") else 0.0


def is_valid_date(text: str) -> bool:
    """同步与导入要求的日期格式：YYYY-MM-DD 且是真实存在的日期。"""
    if not isinstance(text, str) or not _DATE_RE.match(text):
        return False
    try:
        _dt.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _json_dict(text: str | None) -> dict:
    try:
        value = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def sanitize_raw_data(raw_data: Any) -> dict[str, Any]:
    """保证 raw_data 只包含数字或“名称 → 数字”的一层嵌套，便于同步到服务器。"""
    if not isinstance(raw_data, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in raw_data.items():
        name = str(key).strip()
        if not name:
            continue
        if isinstance(value, dict):
            cleaned[name] = {
                str(child).strip(): _clean_number(amount)
                for child, amount in value.items()
                if str(child).strip()
            }
        else:
            cleaned[name] = _clean_number(value)
    return cleaned


# 利润相差不到半分钱视为相同（浮点误差），不算“变化”
PROFIT_TOLERANCE = 0.005


@dataclass
class ProfitPreview:
    """按某套分类与公式重算全部记录利润的结果（只读，不改数据库）。"""

    days: int = 0                 # 参与计算的记录天数
    total_before: float = 0.0     # 目前保存的利润合计
    changed: list[tuple[str, float, float]] = field(default_factory=list)  # (日期, 原利润, 新利润)
    failed: list[str] = field(default_factory=list)  # 新公式算不出来的日期（例如除以 0），保留原利润
    affected: int = 0             # 这次修改让算出来的利润变了的天数（与修改前的分类和公式相比）

    @property
    def total_after(self) -> float:
        return self.total_before + sum(new - old for _, old, new in self.changed)


def _safe_profit(engine: FormulaEngine, formula: str, field_tree: list[dict], raw: dict) -> float | None:
    try:
        value = engine.evaluate(formula, build_formula_variables(field_tree, raw))
    except FormulaError:
        return None
    return value if value == value and abs(value) != float("inf") else None


def _profit_preview(rows, field_tree: list[dict], formula: str,
                    parent_renames: dict[str, str] | None = None,
                    child_renames: dict[str, dict[str, str]] | None = None,
                    current: tuple[list[dict], str] | None = None) -> ProfitPreview:
    """
    rows 需要 date / total_profit / raw_data 三列；有改名时先按改名后的键名计算（与保存设置时一致）。
    current = 修改前的 (分类, 公式)：给出时同时统计 affected。
    """
    engine = FormulaEngine()
    preview = ProfitPreview()
    for row in rows:
        stored_raw = _json_dict(row["raw_data"])
        raw = migrate_raw_data(stored_raw, parent_renames or {}, child_renames or {}) \
            if parent_renames or child_renames else stored_raw
        old = _clean_number(row["total_profit"])
        preview.days += 1
        preview.total_before += old
        new = _safe_profit(engine, formula, field_tree, raw)
        if current is not None:
            before = _safe_profit(engine, current[1], current[0], stored_raw)
            if (new is None) != (before is None) or (new is not None and abs(new - before) > PROFIT_TOLERANCE):
                preview.affected += 1
        if new is None:
            preview.failed.append(row["date"])
        elif abs(new - old) > PROFIT_TOLERANCE:
            preview.changed.append((row["date"], old, new))
    return preview


class DatabaseManager:
    """SQLite 数据库管理器。所有方法线程安全（UI 线程与后台同步线程共用一个连接）。"""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row  # 允许通过列名访问
        self._local_change_listeners: list[Callable[[], None]] = []
        self._initialize_tables()

    # ─────────────────────────────────────────────
    # 初始化
    # ─────────────────────────────────────────────

    def _initialize_tables(self) -> None:
        """建表（如果不存在），程序每次启动时调用。"""
        with self._lock, self._conn:
            existing_tables = self._table_names(self._conn)
            fresh_database = "DailyRecords" not in existing_tables

            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS DailyRecords (
                    date          TEXT PRIMARY KEY,
                    total_profit  REAL DEFAULT 0.0,
                    raw_data      TEXT DEFAULT '{}',
                    note          TEXT DEFAULT ''
                )
            """)
            columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(DailyRecords)").fetchall()
            }
            if "note" not in columns:
                self._conn.execute("ALTER TABLE DailyRecords ADD COLUMN note TEXT DEFAULT ''")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS Config (
                    key   TEXT PRIMARY KEY,
                    value TEXT DEFAULT ''
                )
            """)
            # 写入默认配置（仅首次）
            default_fields = json.dumps(flatten_formula_fields(DEFAULT_FIELD_TREE), ensure_ascii=False)
            default_tree = json.dumps(DEFAULT_FIELD_TREE, ensure_ascii=False)
            self._conn.execute(
                """
                INSERT OR IGNORE INTO Config (key, value)
                VALUES (?, ?), (?, ?), (?, ?)
                """,
                (
                    "profit_formula",
                    DEFAULT_PROFIT_FORMULA,
                    "custom_fields",
                    default_fields,
                    "custom_field_tree",
                    default_tree,
                ),
            )
            self._initialize_sync_tables(fresh_database)

    def _initialize_sync_tables(self, fresh_database: bool) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS SyncRecordState (
                date        TEXT PRIMARY KEY,
                modified_at INTEGER NOT NULL DEFAULT 1,
                base_rev    INTEGER NOT NULL DEFAULT 0,
                dirty       INTEGER NOT NULL DEFAULT 1,
                deleted     INTEGER NOT NULL DEFAULT 0
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS SyncMeta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            )
        """)
        if self._meta_get("schema") == "":
            # 第一次启用同步：旧记录全部标记为待上传（修改时间记为“旧数据”）
            legacy_settings = not fresh_database
            self._meta_set_many({
                "schema": "1",
                "device_id": uuid.uuid4().hex,
                "settings_dirty": "1" if legacy_settings else "0",
                "settings_modified_at": str(LEGACY_MODIFIED_AT if legacy_settings else 0),
                "settings_base_rev": "0",
                "last_rev": "0",
            })
        # 防御：任何没有同步状态的记录（例如被旧版本写入）都补上待上传标记
        self._conn.execute(
            """
            INSERT OR IGNORE INTO SyncRecordState (date, modified_at, base_rev, dirty, deleted)
            SELECT date, ?, 0, 1, 0 FROM DailyRecords
            """,
            (LEGACY_MODIFIED_AT,),
        )

    # ─────────────────────────────────────────────
    # 本地修改通知（供同步模块安排上传）
    # ─────────────────────────────────────────────

    def add_local_change_listener(self, callback: Callable[[], None]) -> None:
        self._local_change_listeners.append(callback)

    def _notify_local_change(self) -> None:
        for callback in list(self._local_change_listeners):
            try:
                callback()
            except Exception:  # 通知失败不能影响保存
                pass

    # ─────────────────────────────────────────────
    # DailyRecords CRUD
    # ─────────────────────────────────────────────

    def upsert_record(self, date: str, total_profit: float, raw_data: dict, note: str = "",
                      keep_note_if_empty: bool = True) -> None:
        """
        新增或更新一条每日记录。
        keep_note_if_empty=True 时空备注不覆盖原备注（兼容旧调用）；录入页会先载入原备注，
        因此录入页传 False：用户清空备注框即表示删除备注。
        """
        raw_json = json.dumps(raw_data, ensure_ascii=False)
        note_sql = "CASE WHEN trim(excluded.note) = '' THEN DailyRecords.note ELSE excluded.note END" \
            if keep_note_if_empty else "excluded.note"
        with self._lock, self._conn:
            self._conn.execute(f"""
                INSERT INTO DailyRecords (date, total_profit, raw_data, note)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    total_profit = excluded.total_profit,
                    raw_data     = excluded.raw_data,
                    note         = {note_sql}
            """, (date, total_profit, raw_json, note))
            self._mark_record_dirty(date, deleted=False)
        self._notify_local_change()

    def get_record(self, date: str) -> dict | None:
        """获取指定日期的记录，返回字典或 None。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM DailyRecords WHERE date = ?", (date,)
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def get_range(self, start_date: str, end_date: str) -> list[dict]:
        """获取日期区间内的所有记录，按日期升序排列。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM DailyRecords WHERE date BETWEEN ? AND ? ORDER BY date ASC",
                (start_date, end_date),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_all_records(self) -> list[dict]:
        """获取所有记录，按日期升序排列。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM DailyRecords ORDER BY date ASC"
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_recent_records(self, limit: int = 6) -> list[dict]:
        """获取最近的若干条记录，按日期降序排列。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM DailyRecords ORDER BY date DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_date_bounds(self) -> tuple[str | None, str | None]:
        with self._lock:
            row = self._conn.execute("SELECT MIN(date) AS first, MAX(date) AS last FROM DailyRecords").fetchone()
        return row["first"], row["last"]

    def delete_record(self, date: str) -> None:
        """删除指定日期的记录。"""
        self.delete_records([date])

    def delete_records(self, dates: list[str]) -> int:
        """批量删除记录，返回实际删除的条数。"""
        deleted = 0
        with self._lock, self._conn:
            for date in dates:
                cursor = self._conn.execute("DELETE FROM DailyRecords WHERE date = ?", (date,))
                known = self._conn.execute(
                    "SELECT 1 FROM SyncRecordState WHERE date = ?", (date,)
                ).fetchone()
                if cursor.rowcount or known:
                    self._mark_record_dirty(date, deleted=True)
                deleted += cursor.rowcount
        if deleted:
            self._notify_local_change()
        return deleted

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> dict:
        return {
            "date": row["date"],
            "total_profit": float(row["total_profit"] or 0.0),
            "raw_data": _json_dict(row["raw_data"]),
            "note": row["note"] or "",
        }

    # ─────────────────────────────────────────────
    # 导入 / 导出
    # ─────────────────────────────────────────────

    def export_database(self, export_path: str | Path) -> Path:
        """将当前账本导出为兼容格式的 SQLite 数据库文件（不含同步密钥等本机信息）。"""
        export_path = Path(export_path)
        if export_path.resolve() == self.db_path.resolve():
            raise ValueError("导出路径不能与当前正在使用的账本文件相同。")

        export_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = export_path.with_name(export_path.name + ".part")
        temp_path.unlink(missing_ok=True)

        with self._lock, contextlib.closing(sqlite3.connect(str(temp_path))) as target_conn:
            self._conn.backup(target_conn)
            with target_conn:
                for table in _SYNC_TABLES:
                    target_conn.execute(f"DROP TABLE IF EXISTS {table}")
                self._ensure_android_metadata(target_conn)
            target_conn.execute("VACUUM")  # 彻底清除已删除表残留的数据页

        temp_path.replace(export_path)
        return export_path

    def import_database(self, import_path: str | Path) -> dict[str, int]:
        """从兼容格式的 SQLite 数据库导入数据，并替换当前账本（同时会同步到其他设备）。"""
        import_path = Path(import_path)
        if not import_path.exists():
            raise FileNotFoundError(f"导入文件不存在：{import_path}")

        with contextlib.closing(sqlite3.connect(str(import_path))) as source_conn:
            source_conn.row_factory = sqlite3.Row
            self._validate_import_schema(source_conn)

            record_rows = source_conn.execute(
                "SELECT date, total_profit, raw_data, note FROM DailyRecords ORDER BY date ASC"
            ).fetchall()
            config_rows = source_conn.execute(
                "SELECT key, value FROM Config ORDER BY key ASC"
            ).fetchall()

            records = []
            for row in record_rows:
                if not is_valid_date(str(row["date"])):
                    raise ValueError(f"导入文件中有无效日期：{row['date']}（应为 YYYY-MM-DD），未做任何修改。")
                raw_data = row["raw_data"] or "{}"
                try:
                    json.loads(raw_data)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{row['date']} 的 raw_data 不是合法 JSON。") from exc

                records.append((
                    str(row["date"]),
                    float(row["total_profit"] or 0.0),
                    raw_data,
                    row["note"] or "",
                ))

            configs = [(str(row["key"]), row["value"] or "") for row in config_rows]

        with self._lock:
            with self._conn:
                previous_dates = {
                    row["date"] for row in self._conn.execute("SELECT date FROM DailyRecords")
                }
                self._conn.execute("DELETE FROM DailyRecords")
                self._conn.execute("DELETE FROM Config")
                self._conn.executemany(
                    """
                    INSERT INTO DailyRecords (date, total_profit, raw_data, note)
                    VALUES (?, ?, ?, ?)
                    """,
                    records,
                )
                self._conn.executemany(
                    "INSERT INTO Config (key, value) VALUES (?, ?)",
                    configs,
                )
                imported_dates = {record[0] for record in records}
                for date in imported_dates:
                    self._mark_record_dirty(date, deleted=False)
                for date in previous_dates - imported_dates:
                    self._mark_record_dirty(date, deleted=True)
                self._mark_settings_dirty()

            self._initialize_tables()
        self._notify_local_change()
        return {"records": len(records), "configs": len(configs)}

    # ─────────────────────────────────────────────
    # Config CRUD
    # ─────────────────────────────────────────────

    def get_config(self, key: str, default: str = "") -> str:
        """读取配置项，若不存在则返回 default。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM Config WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_config(self, key: str, value: str) -> None:
        """写入或更新配置项。"""
        self.set_configs({key: value})

    def set_configs(self, values: dict[str, str]) -> None:
        """在一个事务里写入多个配置项。"""
        synced_changed = False
        with self._lock, self._conn:
            for key, value in values.items():
                old = self._conn.execute("SELECT value FROM Config WHERE key = ?", (key,)).fetchone()
                self._conn.execute("""
                    INSERT INTO Config (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """, (key, value))
                if key in SYNCED_CONFIG_KEYS and (old is None or old["value"] != value):
                    synced_changed = True
            if synced_changed:
                self._mark_settings_dirty()
        if synced_changed:
            self._notify_local_change()

    def save_settings(
        self,
        field_tree: list[dict],
        formula: str,
        parent_renames: dict[str, str] | None = None,
        child_renames: dict[str, dict[str, str]] | None = None,
    ) -> int:
        """
        在一个事务里保存分类与公式；有改名时同时改写历史记录里的键名（这些记录会同步到其他设备）。
        返回被改写的记录数。
        """
        parent_renames = parent_renames or {}
        child_renames = {k: v for k, v in (child_renames or {}).items() if v}
        values = {
            "custom_field_tree": json.dumps(field_tree, ensure_ascii=False),
            "custom_fields": json.dumps(flatten_formula_fields(field_tree), ensure_ascii=False),
            "profit_formula": formula,
        }
        migrated = 0
        settings_changed = False
        with self._lock, self._conn:
            if parent_renames or child_renames:
                for row in self._conn.execute("SELECT date, raw_data FROM DailyRecords").fetchall():
                    raw = _json_dict(row["raw_data"])
                    new_raw = migrate_raw_data(raw, parent_renames, child_renames)
                    if new_raw != raw or list(new_raw) != list(raw):
                        self._conn.execute(
                            "UPDATE DailyRecords SET raw_data = ? WHERE date = ?",
                            (json.dumps(new_raw, ensure_ascii=False), row["date"]),
                        )
                        self._mark_record_dirty(row["date"], deleted=False)
                        migrated += 1
            for key, value in values.items():
                old = self._conn.execute("SELECT value FROM Config WHERE key = ?", (key,)).fetchone()
                if old is None or old["value"] != value:
                    settings_changed = True
                self._conn.execute("""
                    INSERT INTO Config (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """, (key, value))
            if settings_changed:
                self._mark_settings_dirty()
        if migrated or settings_changed:
            self._notify_local_change()
        return migrated

    def preview_profit_changes(
        self,
        field_tree: list[dict],
        formula: str,
        parent_renames: dict[str, str] | None = None,
        child_renames: dict[str, dict[str, str]] | None = None,
    ) -> ProfitPreview:
        """
        设置页保存前调用：按新的分类与公式（含改名）重算每一天的利润，只读不改。
        已保存的利润是保存那天按当时公式算的，改公式后会和新公式不一致，由用户决定是否重算。
        """
        current = self._profit_settings()
        with self._lock:
            rows = self._conn.execute(
                "SELECT date, total_profit, raw_data FROM DailyRecords ORDER BY date ASC"
            ).fetchall()
        return _profit_preview(rows, field_tree, formula, parent_renames, child_renames, current)

    def _profit_settings(self) -> tuple[list[dict], str]:
        """当前保存的 (分类树, 公式)，读法与录入页相同。"""
        tree = load_field_tree_from_json(
            self.get_config("custom_field_tree", "") or self.get_config("custom_fields", '["营业额", "成本"]')
        )
        return tree, self.get_config("profit_formula", "营业额 - 成本")

    def recompute_profits(self) -> int:
        """
        按当前保存的分类与公式重算全部记录的利润；有变化的记录会同步到其他设备，
        算不出来的日期（例如除以 0）保留原利润。返回被更新的天数。
        """
        tree, formula = self._profit_settings()
        with self._lock, self._conn:
            rows = self._conn.execute("SELECT date, total_profit, raw_data FROM DailyRecords").fetchall()
            preview = _profit_preview(rows, tree, formula)
            for date, _old, new in preview.changed:
                self._conn.execute("UPDATE DailyRecords SET total_profit = ? WHERE date = ?", (new, date))
                self._mark_record_dirty(date, deleted=False)
        if preview.changed:
            self._notify_local_change()
        return len(preview.changed)

    def settings_signature(self) -> str:
        """当前分类与公式的指纹，用于判断编辑期间设置是否被云同步改过。"""
        from core.formula_engine import format_formula

        tree = load_field_tree_from_json(self.get_config("custom_field_tree", "") or self.get_config("custom_fields", ""))
        return json.dumps([tree, format_formula(self.get_config("profit_formula", ""))], ensure_ascii=False)

    def get_all_config(self) -> dict[str, str]:
        """返回所有配置项字典。"""
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM Config").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ─────────────────────────────────────────────
    # 云同步：元数据
    # ─────────────────────────────────────────────

    def get_sync_meta(self, key: str, default: str = "") -> str:
        with self._lock:
            value = self._meta_get(key)
        return value if value != "" else default

    def set_sync_meta(self, key: str, value: str) -> None:
        self.set_sync_metas({key: value})

    def set_sync_metas(self, values: dict[str, str]) -> None:
        with self._lock, self._conn:
            self._meta_set_many(values)

    def _meta_get(self, key: str) -> str:
        row = self._conn.execute("SELECT value FROM SyncMeta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else ""

    def _meta_set_many(self, values: dict[str, str]) -> None:
        self._conn.executemany(
            """
            INSERT INTO SyncMeta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            [(key, str(value)) for key, value in values.items()],
        )

    def _mark_record_dirty(self, date: str, deleted: bool) -> None:
        previous = self._conn.execute(
            "SELECT modified_at FROM SyncRecordState WHERE date = ?", (date,)
        ).fetchone()
        modified_at = now_ms()
        if previous is not None and previous["modified_at"] >= modified_at:
            modified_at = previous["modified_at"] + 1  # 同一毫秒内的多次修改也保持递增
        self._conn.execute(
            """
            INSERT INTO SyncRecordState (date, modified_at, base_rev, dirty, deleted)
            VALUES (?, ?, 0, 1, ?)
            ON CONFLICT(date) DO UPDATE SET
                modified_at = excluded.modified_at,
                dirty       = 1,
                deleted     = excluded.deleted
            """,
            (date, modified_at, 1 if deleted else 0),
        )

    def _mark_settings_dirty(self) -> None:
        previous = int(self._meta_get("settings_modified_at") or 0)
        self._meta_set_many({
            "settings_dirty": "1",
            "settings_modified_at": str(max(now_ms(), previous + 1)),
        })

    # ─────────────────────────────────────────────
    # 云同步：供同步引擎读取 / 写回
    # ─────────────────────────────────────────────

    def count_pending_changes(self) -> int:
        with self._lock:
            count = self._conn.execute("SELECT COUNT(*) FROM SyncRecordState WHERE dirty = 1").fetchone()[0]
            if self._meta_get("settings_dirty") == "1":
                count += 1
        return int(count)

    def invalid_dirty_dates(self) -> list[str]:
        """待上传记录中日期格式不正确的（只可能来自外部导入的旧数据），这些记录不能上传。"""
        with self._lock:
            rows = self._conn.execute("SELECT date FROM SyncRecordState WHERE dirty = 1").fetchall()
        return [row["date"] for row in rows if not is_valid_date(row["date"])]

    def collect_dirty_records(self, limit: int, exclude: set[str] | None = None) -> list[dict]:
        """取出待上传的记录（含删除标记），按修改时间排序。"""
        exclude = exclude or set()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT s.date, s.modified_at, s.base_rev, s.deleted,
                       d.date AS live_date, d.total_profit, d.raw_data, d.note
                FROM SyncRecordState s
                LEFT JOIN DailyRecords d ON d.date = s.date
                WHERE s.dirty = 1
                ORDER BY s.modified_at ASC
                """
            ).fetchall()

        changes: list[dict] = []
        for row in rows:
            if row["date"] in exclude:
                continue
            deleted = bool(row["deleted"]) or row["live_date"] is None
            change = {
                "date": row["date"],
                "modified_at": int(row["modified_at"]),
                "base_rev": int(row["base_rev"]),
                "deleted": deleted,
            }
            if not deleted:
                change.update({
                    "total_profit": _clean_number(row["total_profit"]),
                    "raw_data": sanitize_raw_data(_json_dict(row["raw_data"])),
                    "note": row["note"] or "",
                })
            changes.append(change)
            if len(changes) >= limit:
                break
        return changes

    def collect_dirty_settings(self) -> dict | None:
        with self._lock:
            if self._meta_get("settings_dirty") != "1":
                return None
            config = {
                row["key"]: row["value"]
                for row in self._conn.execute(
                    f"SELECT key, value FROM Config WHERE key IN ({','.join('?' * len(SYNCED_CONFIG_KEYS))})",
                    SYNCED_CONFIG_KEYS,
                )
            }
            modified_at = int(self._meta_get("settings_modified_at") or 0)
            base_rev = int(self._meta_get("settings_base_rev") or 0)

        def _json_list(key: str) -> list:
            try:
                value = json.loads(config.get(key, "[]") or "[]")
            except json.JSONDecodeError:
                return []
            return value if isinstance(value, list) else []

        tree = []
        for item in _json_list("custom_field_tree"):
            if isinstance(item, dict) and str(item.get("name", "")).strip():
                children = item.get("children", [])
                tree.append({
                    "name": str(item["name"]).strip(),
                    "children": [str(c).strip() for c in children if str(c).strip()] if isinstance(children, list) else [],
                })
            elif isinstance(item, str) and item.strip():
                tree.append({"name": item.strip(), "children": []})
        return {
            "data": {
                "custom_field_tree": tree,
                "custom_fields": [str(f) for f in _json_list("custom_fields") if str(f).strip()],
                "profit_formula": config.get("profit_formula", ""),
            },
            "modified_at": max(modified_at, 0),
            "base_rev": base_rev,
        }

    def mark_record_synced(self, date: str, rev: int, pushed_modified_at: int) -> None:
        """服务器已接受本机上传的记录。上传期间若又被修改，则保持待上传状态。"""
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE SyncRecordState
                SET base_rev = ?,
                    dirty = CASE WHEN modified_at = ? THEN 0 ELSE dirty END
                WHERE date = ?
                """,
                (rev, pushed_modified_at, date),
            )

    def mark_settings_synced(self, rev: int, pushed_modified_at: int) -> None:
        with self._lock, self._conn:
            values = {"settings_base_rev": str(rev)}
            if int(self._meta_get("settings_modified_at") or 0) == pushed_modified_at:
                values["settings_dirty"] = "0"
            self._meta_set_many(values)

    def apply_remote_record(self, record: dict, force_if_modified_at: int | None = None) -> bool:
        """
        写入服务器版本。本机有尚未上传的修改时不覆盖（交给服务器按修改时间裁决），
        除非 force_if_modified_at 与本机修改时间一致（即服务器明确拒绝了这次上传）。
        返回本地数据是否发生变化。
        """
        date = record["date"]
        with self._lock, self._conn:
            state = self._conn.execute(
                "SELECT modified_at, dirty FROM SyncRecordState WHERE date = ?", (date,)
            ).fetchone()
            if state is not None and state["dirty"]:
                if force_if_modified_at is None or state["modified_at"] != force_if_modified_at:
                    return False

            before = self._conn.execute(
                "SELECT total_profit, raw_data, note FROM DailyRecords WHERE date = ?", (date,)
            ).fetchone()
            if record.get("deleted"):
                self._conn.execute("DELETE FROM DailyRecords WHERE date = ?", (date,))
                changed = before is not None
            else:
                raw_json = json.dumps(sanitize_raw_data(record.get("raw_data", {})), ensure_ascii=False)
                note = record.get("note", "") or ""
                profit = _clean_number(record.get("total_profit", 0.0))
                self._conn.execute(
                    """
                    INSERT INTO DailyRecords (date, total_profit, raw_data, note)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(date) DO UPDATE SET
                        total_profit = excluded.total_profit,
                        raw_data     = excluded.raw_data,
                        note         = excluded.note
                    """,
                    (date, profit, raw_json, note),
                )
                changed = before is None or (
                    abs(float(before["total_profit"] or 0) - profit) > 1e-9
                    or (before["note"] or "") != note
                    or _json_dict(before["raw_data"]) != _json_dict(raw_json)
                )
            self._conn.execute(
                """
                INSERT INTO SyncRecordState (date, modified_at, base_rev, dirty, deleted)
                VALUES (?, ?, ?, 0, ?)
                ON CONFLICT(date) DO UPDATE SET
                    modified_at = excluded.modified_at,
                    base_rev    = excluded.base_rev,
                    dirty       = 0,
                    deleted     = excluded.deleted
                """,
                (date, int(record.get("modified_at", 0)), int(record["rev"]), 1 if record.get("deleted") else 0),
            )
        return changed

    def apply_remote_settings(self, settings: dict, force_if_modified_at: int | None = None) -> bool:
        with self._lock, self._conn:
            if self._meta_get("settings_dirty") == "1":
                local_modified = int(self._meta_get("settings_modified_at") or 0)
                if force_if_modified_at is None or local_modified != force_if_modified_at:
                    return False
            data = settings.get("data", {})
            new_values = {
                "custom_field_tree": json.dumps(data.get("custom_field_tree", []), ensure_ascii=False),
                "custom_fields": json.dumps(data.get("custom_fields", []), ensure_ascii=False),
                "profit_formula": str(data.get("profit_formula", "")),
            }
            changed = False
            for key, value in new_values.items():
                old = self._conn.execute("SELECT value FROM Config WHERE key = ?", (key,)).fetchone()
                if old is None or old["value"] != value:
                    changed = True
                self._conn.execute(
                    """
                    INSERT INTO Config (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (key, value),
                )
            self._meta_set_many({
                "settings_dirty": "0",
                "settings_base_rev": str(int(settings.get("rev", 0))),
                "settings_modified_at": str(int(settings.get("modified_at", 0))),
            })
        return changed

    def reset_sync_state(self) -> None:
        """服务器被重建或恢复旧备份后调用：把本机全部数据重新标记为待上传。"""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO SyncRecordState (date, modified_at, base_rev, dirty, deleted)
                SELECT date, ?, 0, 1, 0 FROM DailyRecords
                """,
                (LEGACY_MODIFIED_AT,),
            )
            self._conn.execute("UPDATE SyncRecordState SET base_rev = 0, dirty = 1")
            settings_modified = int(self._meta_get("settings_modified_at") or 0)
            self._meta_set_many({
                "last_rev": "0",
                "ledger_id": "",
                "settings_base_rev": "0",
                "settings_dirty": "1" if settings_modified > 0 else "0",
            })

    def forget_sync_server(self) -> None:
        """断开同步：清除服务器信息，本机数据保留并重新标记为待上传，便于以后连接新服务器。"""
        with self._lock:
            self.reset_sync_state()
            with self._conn:
                self._conn.execute(
                    "DELETE FROM SyncMeta WHERE key IN ('server_url', 'token', 'fallback_ip', 'last_sync_at', "
                    "'last_error', 'last_result')"
                )

    def backup_copy(self, target: str | Path) -> Path:
        """用 SQLite 在线备份接口复制一份完整账本（包括同步元数据）。"""
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, contextlib.closing(sqlite3.connect(str(target))) as dest:
            self._conn.backup(dest)
        return target

    def backup_before_first_sync(self) -> Path | None:
        """首次同步前在数据库旁边留一份备份，防止合并结果不符合预期。"""
        if self.get_sync_meta("pre_sync_backup") or not self.db_path.exists():
            return None
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.db_path.with_name(f"{self.db_path.stem}.before-sync-{stamp}.db")
        self.backup_copy(target)
        # 备份只用于找回数据，去掉同步密钥，避免备份文件被拷走时泄露
        with contextlib.closing(sqlite3.connect(str(target))) as conn:
            with conn:
                conn.execute("DELETE FROM SyncMeta WHERE key = 'token'")
            conn.execute("VACUUM")
        self.set_sync_meta("pre_sync_backup", target.name)
        return target

    # ─────────────────────────────────────────────
    # 工具方法
    # ─────────────────────────────────────────────

    def close(self) -> None:
        """关闭数据库连接（程序退出时调用）。"""
        with self._lock:
            self._conn.close()

    def data_directory(self) -> Path:
        return self.db_path.resolve().parent

    @staticmethod
    def _table_names(conn: sqlite3.Connection) -> set[str]:
        return {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }

    def _validate_import_schema(self, conn: sqlite3.Connection) -> None:
        tables = self._table_names(conn)
        missing_tables = {"DailyRecords", "Config"} - tables
        if missing_tables:
            raise ValueError(f"导入文件缺少表：{', '.join(sorted(missing_tables))}")

        daily_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(DailyRecords)").fetchall()
        }
        missing_daily = {"date", "total_profit", "raw_data", "note"} - daily_columns
        if missing_daily:
            raise ValueError(f"DailyRecords 缺少字段：{', '.join(sorted(missing_daily))}")

        config_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(Config)").fetchall()
        }
        missing_config = {"key", "value"} - config_columns
        if missing_config:
            raise ValueError(f"Config 缺少字段：{', '.join(sorted(missing_config))}")

    def _ensure_android_metadata(self, conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE IF NOT EXISTS android_metadata (locale TEXT)")
        exists = conn.execute("SELECT 1 FROM android_metadata LIMIT 1").fetchone()
        if not exists:
            conn.execute("INSERT INTO android_metadata (locale) VALUES (?)", ("zh_CN",))

