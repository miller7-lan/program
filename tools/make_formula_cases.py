"""
tools/make_formula_cases.py
用桌面端（Python）公式引擎生成测试向量，安卓端单元测试逐条比对，保证两端算出的利润完全一致。

运行：python3 tools/make_formula_cases.py
"""

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.field_config import DEFAULT_FIELD_TREE, DEFAULT_PROFIT_FORMULA, build_formula_variables  # noqa: E402
from core.formula_engine import FormulaEngine, FormulaError  # noqa: E402

engine = FormulaEngine()
variables = {"营业额": 1200.5, "成本": 300.25, "零钱": 50, "现金": 800, "微信": 400.5, "租金": 0, "a": 3, "b_2": 7.5}

FORMULAS = [
    "营业额 - 成本", "营业额 - 成本 + 零钱", "(营业额 - 成本) * 0.9", "现金+微信-租金", "-营业额", "+成本",
    "--营业额", "2*-3", "-2*3", "1/3", "10/4*2", "2*(3+4)*5", "((营业额))", "a*b_2-a/b_2", "1e3+营业额",
    "1_000 + 2", ".5 + 0.25", "3.", "0.5", "00", "0", "007", "05 + 1", "1abc", "1 2", "营业额 成本",
    "营业额 +", "(营业额 - 成本", "营业额 - 成本)", "营业额 / 租金", "未定义 + 1", "营业额 ** 2", "营业额 // 2",
    "营业额 % 2", "营业额 > 成本", "abs(营业额)", "营业额.real", "营业额[0]", "营业额, 成本", "~营业额",
    "营业额 & 1", "营业额 ^ 2", "营业额 | 1", "营业额 << 1", "True + 1", "None", "1j", "not 营业额",
    "营业额 and 成本", "", "   ", "营业额 - 成本 * 2 + 零钱 / 4 - (现金 - 微信) * 1.5", "0x1F + 1", "1e-2 * 营业额",
    "营业额-成本", "（营业额 - 成本）", "a +\n b_2", "现金×2÷4", "１２０＋３５", "营业额 − 成本", "0。5 * a",
    "ａ + 1", "(营业额 - 成本", "营业额(", "a ** 2 + abs(b_2)", "not a ** 2", "-a % 2 < 1", "(a, b_2)[0]",
    "a if b_2 else 1", "[a]", "()", "营业额 = 1",
]

rng = random.Random(20261029)
names = list(variables)
for _ in range(120):
    parts = []
    for i in range(rng.randint(2, 7)):
        if i:
            parts.append(rng.choice([" + ", " - ", " * ", " / "]))
        token = rng.choice(names + [str(rng.randint(1, 99)), f"{rng.uniform(0, 50):.2f}"])
        if rng.random() < 0.15:
            token = "-" + token
        if rng.random() < 0.2:
            token = f"({token} {rng.choice(['+', '-', '*'])} {rng.choice(names)})"
        parts.append(token)
    FORMULAS.append("".join(parts))


def classify(message: str) -> str:
    if message == "公式不能为空":
        return "empty"
    if message.startswith("公式语法错误"):
        return "syntax"
    if message.startswith("公式包含不允许的操作"):
        return "disallowed"
    if message.startswith("变量"):
        return "undefined"
    if message.startswith("公式出现除以零"):
        return "divzero"
    if message.startswith("不支持非数字常量"):
        return "nonnumeric"
    return "other"


cases = []
for formula in FORMULAS:
    ok, message = engine.validate(formula)
    case = {"formula": formula, "valid": ok}
    if not ok:
        case["error_kind"] = classify(message)
        case["error"] = message
    else:
        case["variables_used"] = sorted(set(engine.extract_variables(formula)))
        try:
            case["result"] = engine.evaluate(formula, variables)
        except FormulaError as exc:
            case["error_kind"] = classify(str(exc))
            case["error"] = str(exc)
    cases.append(case)

raw = {
    "营业额": {"账户": 714, "微信分": 2431, "微信分欠": 0, "现金": 755, "邮政账户": 0},
    "成本": {"昨日零钱": 270, "牛肚": 292, "牛肠": 297, "肚带": 148, "羊杂": 269, "运费": 136, "代买": 108, "龙虾": 61},
    "零钱": 134, "营业额备注": 0, "旧字段": 9.5,
}
tree_variables = build_formula_variables(DEFAULT_FIELD_TREE, raw)
default_case = {
    "formula": DEFAULT_PROFIT_FORMULA,
    "raw_data": raw,
    "variables": tree_variables,
    "result": engine.evaluate(DEFAULT_PROFIT_FORMULA, tree_variables),
}

out = ROOT / "android" / "app" / "src" / "test" / "resources" / "formula_cases.json"
out.write_text(json.dumps({"variables": variables, "cases": cases, "default_tree_case": default_case},
                          ensure_ascii=False, indent=1), encoding="utf-8")
print(f"{len(cases)} 条公式用例已写入 {out.relative_to(ROOT)}")
