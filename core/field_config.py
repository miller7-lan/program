"""
core/field_config.py
字段树配置工具，负责兼容旧版一维字段配置与新版二级分类配置。
"""

from __future__ import annotations

import json
from typing import Any


DEFAULT_FIELD_TREE = [
    {"name": "营业额", "children": ["账户", "微信分", "微信分欠", "现金", "邮政账户"]},
    {"name": "成本", "children": ["小肠", "猪肚", "昨日零钱", "肥肠", "牛肚", "牛肠", "牛碎筋", "肚带", "羊杂", "羊肠", "猪肺", "牛熟", "生牛副筋", "袋子", "毛肚", "运费", "水电", "代买", "龙虾"]},
    {"name": "零钱", "children": []},
    {"name": "营业额备注", "children": []},
]

DEFAULT_PROFIT_FORMULA = (
    "账户 + 邮政账户 + 微信分 + 微信分欠 + 现金 - 小肠 - 猪肚 - 羊杂 - 肥肠 - 牛肚 - 牛肠 - 牛碎筋 "
    "- 肚带 - 猪肺 - 羊肠 - 昨日零钱 - 牛熟 - 生牛副筋 - 袋子 - 毛肚 - 运费 - 水电 - 代买 - 龙虾"
)


def load_field_tree_from_json(config_value: str | None) -> list[dict[str, Any]]:
    """从 JSON 配置中读取字段树，自动兼容旧版字符串列表。"""
    if not config_value:
        return _clone_default_tree()

    try:
        data = json.loads(config_value)
    except json.JSONDecodeError:
        return _clone_default_tree()

    normalized = normalize_field_tree(data)
    return normalized or _clone_default_tree()


def normalize_field_tree(data: Any) -> list[dict[str, Any]]:
    """规范化字段树结构，过滤空值和重复项。"""
    if not isinstance(data, list):
        return []

    normalized: list[dict[str, Any]] = []
    used_parent_names: set[str] = set()

    for item in data:
        if isinstance(item, str):
            parent_name = item.strip()
            child_items: list[str] = []
        elif isinstance(item, dict):
            parent_name = str(item.get("name", "")).strip()
            raw_children = item.get("children", [])
            child_items = []
            if isinstance(raw_children, list):
                child_seen: set[str] = set()
                for child in raw_children:
                    child_name = str(child).strip()
                    if not child_name or child_name in child_seen:
                        continue
                    child_seen.add(child_name)
                    child_items.append(child_name)
        else:
            continue

        if not parent_name or parent_name in used_parent_names:
            continue

        used_parent_names.add(parent_name)
        normalized.append({"name": parent_name, "children": child_items})

    return normalized


def flatten_formula_fields(field_tree: list[dict[str, Any]]) -> list[str]:
    """返回公式可直接引用的变量名列表，包含一级汇总和二级明细。"""
    fields: list[str] = []
    seen: set[str] = set()

    for item in field_tree:
        parent_name = item["name"]
        if parent_name not in seen:
            seen.add(parent_name)
            fields.append(parent_name)

        for child_name in item.get("children", []):
            if child_name in seen:
                continue
            seen.add(child_name)
            fields.append(child_name)

    return fields


def build_formula_variables(field_tree: list[dict[str, Any]], raw_data: dict[str, Any]) -> dict[str, float]:
    """根据字段树，将录入数据转换成公式计算所需变量，包含一级汇总和二级明细。"""
    variables: dict[str, float] = {}

    for item in field_tree:
        parent_name = item["name"]
        children = item.get("children", [])
        stored_value = raw_data.get(parent_name, {})

        if children:
            total = 0.0
            if isinstance(stored_value, dict):
                for child_name in children:
                    child_value = _to_float(stored_value.get(child_name, 0.0))
                    variables[child_name] = child_value
                    total += child_value
            elif isinstance(stored_value, (int, float)):
                total = float(stored_value)
            variables[parent_name] = total
            continue

        variables[parent_name] = _to_float(stored_value)

    for key, value in raw_data.items():
        if key not in variables and isinstance(value, (int, float)):
            variables[key] = float(value)
        elif isinstance(value, dict):
            for child_name, amount in value.items():
                if child_name not in variables:
                    variables[child_name] = _to_float(amount)

    return variables


def summarize_raw_data(raw_data: dict[str, Any], limit: int = 3) -> str:
    """将嵌套账目数据压缩成适合历史列表展示的摘要。"""
    parts: list[str] = []

    for key, value in raw_data.items():
        if isinstance(value, dict):
            child_parts = [f"{child}: {amount}" for child, amount in value.items()]
            rendered = f"{key}（" + "，".join(child_parts) + "）"
        else:
            rendered = f"{key}: {value}"
        parts.append(rendered)
        if len(parts) >= limit:
            break

    return "  |  ".join(parts)


def _clone_default_tree() -> list[dict[str, Any]]:
    return [{"name": item["name"], "children": list(item["children"])} for item in DEFAULT_FIELD_TREE]


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
