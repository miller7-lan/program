"""
core/sync_preset.py
内置的同步服务器配置：首次运行时自动连接，无需用户填写。

查找顺序（找到第一个有效的 PH1- 连接码即使用）：
  1. 程序同级目录的「利润助手同步配置.txt」（随 exe 一起分发，不进入公开仓库）
  2. 打包时放入的 assets/sync_preset.txt（本地自行打包时可用，同样不提交）

用户在设置里主动“断开”后不再自动套用，直到重新连接。
"""

from __future__ import annotations

import re

from core.app_paths import data_file_path, resource_path
from core.sync_client import SyncEngine, SyncError, parse_connection_code

PRESET_FILENAME = "利润助手同步配置.txt"
_CODE_RE = re.compile(r"PH1-[A-Za-z0-9_-]+")


def find_preset_code() -> str | None:
    for path in (data_file_path(PRESET_FILENAME), resource_path("assets", "sync_preset.txt")):
        try:
            match = _CODE_RE.search(path.read_text(encoding="utf-8-sig"))
        except OSError:
            continue
        if match:
            return match.group(0)
    return None


def apply_preset_if_needed(engine: SyncEngine) -> bool:
    """尚未设置同步、且用户没有主动断开时，自动套用内置配置。返回是否套用了。"""
    if engine.is_configured() or engine.db.get_sync_meta("preset_disabled") == "1":
        return False
    code = find_preset_code()
    if not code:
        return False
    try:
        engine.save_config(parse_connection_code(code))
    except SyncError:
        return False
    return True
