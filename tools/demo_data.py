"""
tools/demo_data.py
生成演示账本（随机但可复现，约 80 天），用于截图和界面预览，不包含任何真实数据。
"""

from __future__ import annotations

import datetime as dt
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.database import DatabaseManager  # noqa: E402
from core.field_config import DEFAULT_FIELD_TREE, DEFAULT_PROFIT_FORMULA, build_formula_variables  # noqa: E402
from core.formula_engine import FormulaEngine  # noqa: E402

NOTES = ["", "", "", "", "周末客流多", "下雨，生意一般", "进了一批牛肚", "房租已交", "活动日营业额偏高", "水电费已缴"]


def make_demo_db(path: Path, days: int = 80, seed: int = 20261029) -> Path:
    path = Path(path)
    path.unlink(missing_ok=True)
    db = DatabaseManager(path)
    engine = FormulaEngine()
    rng = random.Random(seed)
    today = dt.date.today()
    revenue_names = DEFAULT_FIELD_TREE[0]["children"]
    cost_names = DEFAULT_FIELD_TREE[1]["children"]
    for offset in range(days, -1, -1):
        day = today - dt.timedelta(days=offset)
        if offset and rng.random() < 0.08:
            continue
        weekend = day.weekday() >= 5
        revenue = {name: 0.0 for name in revenue_names}
        revenue["微信分"] = round(rng.uniform(1600, 2600) * (1.25 if weekend else 1), 0)
        revenue["现金"] = round(rng.uniform(500, 1100), 0)
        revenue["账户"] = round(rng.uniform(300, 900), 0)
        revenue["微信分欠"] = round(rng.choice([0, 0, 0, rng.uniform(50, 200)]), 0)
        revenue["邮政账户"] = round(rng.choice([0, 0, rng.uniform(100, 400)]), 0)
        costs = {name: 0.0 for name in cost_names}
        for name in rng.sample(cost_names, 8):
            costs[name] = round(rng.uniform(60, 420), 0)
        if rng.random() < 0.06:
            costs["龙虾"] = round(rng.uniform(2500, 4200), 0)
        raw = {"营业额": revenue, "成本": costs, "零钱": round(rng.uniform(100, 300), 0), "营业额备注": 0.0}
        profit = engine.evaluate(DEFAULT_PROFIT_FORMULA, build_formula_variables(DEFAULT_FIELD_TREE, raw))
        db.upsert_record(day.isoformat(), profit, raw, rng.choice(NOTES))
    db.close()
    return path


if __name__ == "__main__":
    print(make_demo_db(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "demo_ledger.db"))
