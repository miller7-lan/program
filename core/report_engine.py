"""
core/report_engine.py
报表生成引擎骨架 — 基于 pandas，负责数据聚合与 Excel 导出。

后续在此模块填充具体业务逻辑，与 UI 层完全解耦。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from core.database import DatabaseManager


class ReportEngine:
    """基于 pandas 的报表生成引擎。"""

    def __init__(self, db: "DatabaseManager"):
        self._db = db

    # ─────────────────────────────────────────
    # 数据帧生成
    # ─────────────────────────────────────────

    def generate_daily_df(self, start_date: str, end_date: str) -> pd.DataFrame:
        """
        生成指定日期区间的每日利润数据帧。

        Returns:
            DataFrame，列：['date', 'total_profit']（日期升序排列）
        """
        records = self._db.get_range(start_date, end_date)
        if not records:
            return pd.DataFrame(columns=["date", "total_profit"])
        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"])
        df = df[["date", "total_profit"]].sort_values("date")
        return df.reset_index(drop=True)

    def generate_monthly_df(self, year: int) -> pd.DataFrame:
        """
        生成指定年份的月度利润汇总数据帧。

        Returns:
            DataFrame，列：['month', 'total_profit']（1-12月）
        """
        start = f"{year}-01-01"
        end = f"{year}-12-31"
        df = self.generate_daily_df(start, end)
        if df.empty:
            return pd.DataFrame(columns=["month", "total_profit"])
        df["month"] = df["date"].dt.month
        monthly = df.groupby("month", as_index=False)["total_profit"].sum()
        return monthly

    def generate_yearly_summary(self) -> pd.DataFrame:
        """
        生成所有年份的年度利润汇总数据帧。

        Returns:
            DataFrame，列：['year', 'total_profit']
        """
        records = self._db.get_all_records()
        if not records:
            return pd.DataFrame(columns=["year", "total_profit"])
        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"])
        df["year"] = df["date"].dt.year
        yearly = df.groupby("year", as_index=False)["total_profit"].sum()
        return yearly

    # ─────────────────────────────────────────
    # Excel 导出
    # ─────────────────────────────────────────

    def export_to_excel(self, export_path: str | Path, start_date: str, end_date: str) -> Path:
        """
        将指定日期区间的记录导出为 .xlsx 文件。

        Args:
            export_path: 导出文件路径（含文件名），例如 Path.home() / "利润报表.xlsx"
            start_date:  开始日期 'YYYY-MM-DD'
            end_date:    结束日期 'YYYY-MM-DD'

        Returns:
            写入成功的文件 Path 对象
        """
        export_path = Path(export_path)
        records = self._db.get_range(start_date, end_date)

        if not records:
            # 写入空报表，防止 UI 端无法打开
            empty_df = pd.DataFrame(columns=["日期", "总利润", "原始数据"])
            empty_df.to_excel(export_path, index=False)
            return export_path

        # 展开 raw_data 字段到独立列（方便阅读）
        rows = []
        for r in records:
            row = {"日期": r["date"], "总利润": r["total_profit"]}
            row.update(r["raw_data"])  # 将 JSON 键值展平
            rows.append(row)

        df = pd.DataFrame(rows)
        df.to_excel(export_path, index=False, engine="openpyxl")
        return export_path

    # ─────────────────────────────────────────
    # 统计快照
    # ─────────────────────────────────────────

    def get_summary_data(self) -> dict[str, float]:
        """
        获取统计摘要：今日利润、本月利润、今年利润。
        
        Returns:
            dict: {"today": float, "month": float, "year": float}
        """
        import datetime
        now = datetime.date.today()
        today_str = now.strftime("%Y-%m-%d")
        
        # 1. 今日
        today_rec = self._db.get_record(today_str)
        today_val = today_rec["total_profit"] if today_rec else 0.0

        # 2. 本月
        month_start = now.replace(day=1).strftime("%Y-%m-%d")
        month_records = self._db.get_range(month_start, today_str)
        month_val = sum(r["total_profit"] for r in month_records)

        # 3. 今年
        year_start = now.replace(month=1, day=1).strftime("%Y-%m-%d")
        year_records = self._db.get_range(year_start, today_str)
        year_val = sum(r["total_profit"] for r in year_records)

        return {
            "today": today_val,
            "month": month_val,
            "year": year_val
        }
