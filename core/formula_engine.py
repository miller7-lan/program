"""
core/formula_engine.py
安全公式解析引擎 — 基于 Python 标准库 ast 模块。

设计原则：
  - 不使用 eval()，彻底杜绝代码注入风险
  - 只允许：数字字面量、变量名、四则运算符（+-*/）、括号
  - 遇到不合法节点（函数调用、导入等）立刻抛出 FormulaError

使用方法：
    from core.formula_engine import FormulaEngine

    engine = FormulaEngine()

    # 基本计算
    result = engine.evaluate("营业额 - 成本 + 其他收入",
                             {"营业额": 1000, "成本": 200, "其他收入": 50})
    # → 850.0

    # 验证公式合法性（不需要传变量值）
    ok, msg = engine.validate("营业额 - 成本")
    # → (True, "")
"""

import ast
from typing import Any


class FormulaError(Exception):
    """公式解析或计算错误。"""


# ──────────────────────────────────────────
# AST 节点白名单
# ──────────────────────────────────────────
_ALLOWED_NODE_TYPES = (
    ast.Expression,
    ast.BinOp,       # 二元运算  A + B
    ast.UnaryOp,     # 一元运算  -A
    ast.Constant,    # Python ≥‘3.8 字面量（包括整数和浮点数）
    ast.Name,        # 变量名（字段名）
    ast.Add,         # +
    ast.Sub,         # -
    ast.Mult,        # *
    ast.Div,         # /
    ast.USub,        # 一元负号
    ast.UAdd,        # 一元正号
    ast.Load,        # 变量读取上下文
)


class FormulaEngine:
    """安全公式引擎。"""

    # ─────────────────────────────────────────
    # 公开接口
    # ─────────────────────────────────────────

    def evaluate(self, formula: str, variables: dict[str, float]) -> float:
        """
        安全求值。

        Args:
            formula:   公式字符串，例如 '营业额 - 成本'
            variables: 变量字典，例如 {'营业额': 1000, '成本': 200}

        Returns:
            计算结果（float）

        Raises:
            FormulaError: 公式语法非法、变量未定义或除以零时抛出
        """
        tree = self._parse(formula)
        return self._eval_node(tree.body, variables)

    def validate(self, formula: str) -> tuple[bool, str]:
        """
        验证公式语法是否合法（不需要传入变量值）。

        Returns:
            (True, "")          — 合法
            (False, "错误原因") — 非法
        """
        try:
            self._parse(formula)
            return True, ""
        except FormulaError as e:
            return False, str(e)

    def extract_variables(self, formula: str) -> list[str]:
        """
        从公式中提取所有变量名（用于检查缺失字段）。

        Example:
            extract_variables("A - B + C") → ["A", "B", "C"]
        """
        try:
            tree = self._parse(formula)
        except FormulaError:
            return []
        return [
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        ]

    # ─────────────────────────────────────────
    # 内部方法
    # ─────────────────────────────────────────

    def _parse(self, formula: str) -> ast.Expression:
        """解析公式字符串，返回 AST，并验证节点白名单。"""
        formula = formula.strip()
        if not formula:
            raise FormulaError("公式不能为空")
        try:
            tree = ast.parse(formula, mode="eval")
        except SyntaxError as e:
            raise FormulaError(f"公式语法错误：{e.msg}") from e

        # 检查所有节点是否在白名单内
        for node in ast.walk(tree):
            # 允许基本的 AST 节点
            if isinstance(node, _ALLOWED_NODE_TYPES):
                continue
            # 允许上下文节点（Load, Store 等，尽管 eval 只用 Load）
            if isinstance(node, ast.expr_context) or type(node).__name__ == "Load":
                continue
            
            raise FormulaError(
                f"公式包含不允许的操作（{type(node).__name__}），"
                "只支持四则运算和字段变量。"
            )
        return tree

    def _eval_node(self, node: ast.expr, variables: dict[str, float]) -> float:
        """递归求值 AST 节点。"""
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)):
                raise FormulaError(f"不支持非数字常量：{node.value!r}")
            return float(node.value)

        if isinstance(node, ast.Name):
            name = node.id
            if name not in variables:
                raise FormulaError(
                    f"变量 '{name}' 未在当日记录中定义，请先在录入页填写该字段。"
                )
            return float(variables[name])

        if isinstance(node, ast.BinOp):
            left = self._eval_node(node.left, variables)
            right = self._eval_node(node.right, variables)
            op = node.op
            if isinstance(op, ast.Add):
                return left + right
            if isinstance(op, ast.Sub):
                return left - right
            if isinstance(op, ast.Mult):
                return left * right
            if isinstance(op, ast.Div):
                if right == 0:
                    raise FormulaError("公式出现除以零，请检查数据。")
                return left / right
            raise FormulaError(f"不支持的运算符：{type(op).__name__}")  # 理论上不会到达

        if isinstance(node, ast.UnaryOp):
            operand = self._eval_node(node.operand, variables)
            if isinstance(node.op, ast.USub):
                return -operand
            if isinstance(node.op, ast.UAdd):
                return +operand

        raise FormulaError(f"不支持的表达式类型：{type(node).__name__}")
