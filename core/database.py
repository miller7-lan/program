"""
core/database.py
SQLite DAO 层 — 负责所有数据库交互，与 UI 完全解耦。

表结构：
  DailyRecords (date TEXT PRIMARY KEY, total_profit REAL, raw_data TEXT, note TEXT)
    - date: ISO 日期字符串 '2024-01-15'
    - total_profit: 当日利润（缓存计算结果，避免每次重算）
    - raw_data: JSON 字符串，存储所有自定义基数项，例如
                '{"营业额": 1000, "成本": 200, "零钱": 50}'
    - note: 备注文本，独立存储，避免影响利润计算

  Config (key TEXT PRIMARY KEY, value TEXT)
    - 存储用户自定义配置：基数项列表、利润公式字符串等
    - 例如 key='profit_formula', value='营业额 - 成本'
         key='custom_fields', value='["营业额","成本","零钱"]'
"""

import json
import sqlite3
from pathlib import Path

from core.app_paths import data_file_path
from core.field_config import DEFAULT_FIELD_TREE, DEFAULT_PROFIT_FORMULA, flatten_formula_fields


# 默认数据库文件路径：可执行文件同级目录，跨平台兼容
DEFAULT_DB_PATH = data_file_path("family_ledger.db")


class DatabaseManager:
    """单例式 SQLite 数据库管理器。"""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row  # 允许通过列名访问
        self._initialize_tables()

    # ─────────────────────────────────────────────
    # 初始化
    # ─────────────────────────────────────────────

    def _initialize_tables(self) -> None:
        """建表（如果不存在），程序每次启动时调用。"""
        with self._conn:
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

    # ─────────────────────────────────────────────
    # DailyRecords CRUD
    # ─────────────────────────────────────────────

    def upsert_record(self, date: str, total_profit: float, raw_data: dict, note: str = "") -> None:
        """新增或更新一条每日记录。"""
        raw_json = json.dumps(raw_data, ensure_ascii=False)
        with self._conn:
            self._conn.execute("""
                INSERT INTO DailyRecords (date, total_profit, raw_data, note)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    total_profit = excluded.total_profit,
                    raw_data     = excluded.raw_data,
                    note         = CASE
                        WHEN trim(excluded.note) = '' THEN DailyRecords.note
                        ELSE excluded.note
                    END
            """, (date, total_profit, raw_json, note))

    def get_record(self, date: str) -> dict | None:
        """获取指定日期的记录，返回字典或 None。"""
        row = self._conn.execute(
            "SELECT * FROM DailyRecords WHERE date = ?", (date,)
        ).fetchone()
        if row is None:
            return None
        return {
            "date": row["date"],
            "total_profit": row["total_profit"],
            "raw_data": json.loads(row["raw_data"]),
            "note": row["note"] or "",
        }

    def get_range(self, start_date: str, end_date: str) -> list[dict]:
        """获取日期区间内的所有记录，按日期升序排列。"""
        rows = self._conn.execute(
            "SELECT * FROM DailyRecords WHERE date BETWEEN ? AND ? ORDER BY date ASC",
            (start_date, end_date),
        ).fetchall()
        return [
            {
                "date": r["date"],
                "total_profit": r["total_profit"],
                "raw_data": json.loads(r["raw_data"]),
                "note": r["note"] or "",
            }
            for r in rows
        ]

    def get_all_records(self) -> list[dict]:
        """获取所有记录，按日期升序排列。"""
        rows = self._conn.execute(
            "SELECT * FROM DailyRecords ORDER BY date ASC"
        ).fetchall()
        return [
            {
                "date": r["date"],
                "total_profit": r["total_profit"],
                "raw_data": json.loads(r["raw_data"]),
                "note": r["note"] or "",
            }
            for r in rows
        ]

    def delete_record(self, date: str) -> None:
        """删除指定日期的记录。"""
        with self._conn:
            self._conn.execute("DELETE FROM DailyRecords WHERE date = ?", (date,))

    # ─────────────────────────────────────────────
    # Config CRUD
    # ─────────────────────────────────────────────

    def get_config(self, key: str, default: str = "") -> str:
        """读取配置项，若不存在则返回 default。"""
        row = self._conn.execute(
            "SELECT value FROM Config WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_config(self, key: str, value: str) -> None:
        """写入或更新配置项。"""
        with self._conn:
            self._conn.execute("""
                INSERT INTO Config (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (key, value))

    def get_all_config(self) -> dict[str, str]:
        """返回所有配置项字典。"""
        rows = self._conn.execute("SELECT key, value FROM Config").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ─────────────────────────────────────────────
    # 工具方法
    # ─────────────────────────────────────────────

    def close(self) -> None:
        """关闭数据库连接（程序退出时调用）。"""
        self._conn.close()
