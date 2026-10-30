"""
core/sync_preset.py
内置的同步服务器配置：首次运行时自动连接，无需用户填写。

查找顺序（找到第一个有效的 PH1- 连接码即使用）：
  1. 程序同级目录的 ProfitHelper-sync.txt 或「利润助手同步配置.txt」（随 exe 一起分发，不进入公开仓库；
     英文文件名可避免部分解压软件把中文文件名解成乱码）
  2. 打包时放入的 assets/sync_preset.txt（本地自行打包时可用，同样不提交）

密钥轮换：服务器换了密钥后，只需替换 exe 旁边的 ProfitHelper-sync.txt（或安装新版安卓包），
程序下次启动（或在设置里点“重新检测”）会自动换用新配置。
"""

from __future__ import annotations

import re

from core.app_paths import data_file_path, resource_path
from core.sync_client import SyncEngine, SyncError, parse_connection_code

PRESET_FILENAMES = ("ProfitHelper-sync.txt", "利润助手同步配置.txt")
_CODE_RE = re.compile(r"PH1-[A-Za-z0-9_-]+")


def find_preset_code() -> str | None:
    candidates = [data_file_path(name) for name in PRESET_FILENAMES] + [resource_path("assets", "sync_preset.txt")]
    for path in candidates:
        try:
            match = _CODE_RE.search(path.read_text(encoding="utf-8-sig"))
        except OSError:
            continue
        if match:
            return match.group(0)
    return None


def apply_preset_if_needed(engine: SyncEngine, force: bool = False) -> bool:
    """
    套用内置配置，返回是否套用了：
      - 尚未设置同步（包括旧版本里点过“断开”）→ 套用；新版本已没有断开和手动填写的入口
      - 当前连接来自内置配置、而内置配置已更新（例如轮换了密钥）→ 换用新配置
      - 旧版本里手动填写的连接 → 不动（force=True 时也换用：用户在设置里点了“重新检测”）
    """
    db = engine.db
    code = find_preset_code()
    if not code:
        return False
    try:
        preset = parse_connection_code(code)
    except SyncError:
        return False
    current = engine.load_config()
    if current.is_complete:
        if (current.server_url, current.token, current.fallback_ip) == (preset.server_url, preset.token, preset.fallback_ip):
            return False
        source = db.get_sync_meta("config_source")
        if not force and (source == "manual" or (source == "" and current.server_url != preset.server_url)):
            return False
    engine.save_config(preset)
    db.set_sync_metas({"config_source": "preset", "preset_disabled": "0"})
    return True
