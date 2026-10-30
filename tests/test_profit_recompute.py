"""
tests/test_profit_recompute.py
改公式 / 分类后，已保存的历史利润如何处理：保存前只读预览哪些天会变，用户选择“重算历史”才改写，
改写的记录会同步到其他设备；算不出来的日期保留原利润；重复执行不会重复改动。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.database import DatabaseManager  # noqa: E402

TREE = [
    {"name": "营业额", "children": ["现金", "微信"]},
    {"name": "成本", "children": ["进货"]},
    {"name": "零钱", "children": []},
]
DAYS = [  # 日期, 现金, 微信, 进货, 零钱
    ("2026-10-01", 100.0, 50.5, 30.0, 20.0),
    ("2026-10-02", 90.0, 0.0, 80.0, 0.0),
    ("2026-10-03", 10.1, 20.2, 0.3, 5.0),
]


class ProfitRecomputeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="profit-"))
        self.db = DatabaseManager(self.tmp / "ledger.db")
        self.db.save_settings(TREE, "营业额 - 成本")
        for date, cash, wechat, cost, change in DAYS:
            raw = {"营业额": {"现金": cash, "微信": wechat}, "成本": {"进货": cost}, "零钱": change}
            self.db.upsert_record(date, cash + wechat - cost, raw, "")
        for record in self.db.collect_dirty_records(10):
            self.db.mark_record_synced(record["date"], 1, record["modified_at"])

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def profit(self, date: str) -> float:
        return self.db.get_record(date)["total_profit"]

    def dirty_dates(self) -> list[str]:
        return sorted(record["date"] for record in self.db.collect_dirty_records(10))

    def test_same_result_written_differently_changes_nothing(self):
        preview = self.db.preview_profit_changes(TREE, "现金 + 微信 - 进货")
        self.assertEqual((preview.days, preview.changed, preview.failed, preview.affected), (3, [], [], 0))
        self.assertEqual(self.db.recompute_profits(), 0)
        self.assertEqual(self.dirty_dates(), [])

    def test_preview_lists_changed_days_without_touching_data(self):
        preview = self.db.preview_profit_changes(TREE, "营业额 - 成本 + 零钱")
        self.assertEqual([date for date, _, _ in preview.changed], ["2026-10-01", "2026-10-03"])  # 10-02 零钱为 0
        self.assertEqual(preview.affected, 2)
        self.assertAlmostEqual(preview.total_before, 120.5 + 10.0 + 30.0)
        self.assertAlmostEqual(preview.total_after - preview.total_before, 25.0)
        self.assertAlmostEqual(self.profit("2026-10-01"), 120.5)  # 只读
        self.assertEqual(self.dirty_dates(), [])

    def test_recompute_rewrites_only_changed_days_and_syncs_them(self):
        self.db.save_settings(TREE, "营业额 - 成本 + 零钱")
        self.assertAlmostEqual(self.profit("2026-10-01"), 120.5)  # 只保存设置不会改历史
        self.assertEqual(self.db.recompute_profits(), 2)
        self.assertAlmostEqual(self.profit("2026-10-01"), 140.5)
        self.assertAlmostEqual(self.profit("2026-10-02"), 10.0)
        self.assertAlmostEqual(self.profit("2026-10-03"), 35.0)
        self.assertEqual(self.dirty_dates(), ["2026-10-01", "2026-10-03"])
        self.assertEqual(self.db.recompute_profits(), 0)  # 再算一次不会重复改动、不会叠加

    def test_renames_are_applied_before_comparing(self):
        tree = [dict(TREE[0], children=["现金收入", "微信"]), *TREE[1:]]
        preview = self.db.preview_profit_changes(tree, "现金收入 + 微信 - 进货", {}, {"营业额": {"现金": "现金收入"}})
        self.assertEqual((preview.changed, preview.failed, preview.affected), ([], [], 0))

    def test_days_the_new_formula_cannot_compute_keep_their_profit(self):
        preview = self.db.preview_profit_changes(TREE, "营业额 / 微信")  # 10-02 微信为 0 → 除以 0
        self.assertEqual(preview.failed, ["2026-10-02"])
        self.db.save_settings(TREE, "营业额 / 微信")
        self.assertEqual(self.db.recompute_profits(), 2)
        self.assertAlmostEqual(self.profit("2026-10-02"), 10.0)
        self.assertNotIn("2026-10-02", self.dirty_dates())

    def test_affected_counts_only_what_this_change_alters(self):
        # 之前选择了“保留原利润”：历史与公式已不一致；之后加一个不参与计算的分类，不算“这次修改影响了利润”
        self.db.save_settings(TREE, "营业额 - 成本 + 零钱")
        tree = [*TREE, {"name": "备用", "children": []}]
        preview = self.db.preview_profit_changes(tree, "营业额 - 成本 + 零钱")
        self.assertEqual(preview.affected, 0)
        self.assertEqual(len(preview.changed), 2)  # 若选择重算，仍会把这两天改成新公式的结果


if __name__ == "__main__":
    unittest.main()
