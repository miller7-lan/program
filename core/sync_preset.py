"""
core/sync_preset.py
内置的同步服务器配置：首次运行时自动连接，无需用户填写。

查找顺序（找到第一个有效的 PH1- 连接码即使用）：
  1. exe 旁边的 ProfitHelper-sync.txt 或「利润助手同步配置.txt」（随 exe 一起分发，不进入公开仓库；
     英文文件名可避免部分解压软件把中文文件名解成乱码）；在压缩包里直接打开时也查找数据文件夹
  2. 写在 exe 末尾的内置连接码（tools/embed_preset.py 在本机对打包好的 exe 写入，单个 exe 就能点开即用；
     GitHub 上公开的打包产物不含连接码）
  3. 打包时放入的 assets/sync_preset.txt（本地自行打包时可用，同样不提交）

密钥轮换：服务器换了密钥后，只需替换 exe 旁边的 ProfitHelper-sync.txt（或安装新版安卓包），
程序下次启动（或在设置里点“重新检测”）会自动换用新配置。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from core.app_paths import data_file_path, executable_dir, resource_path
from core.sync_client import SyncEngine, SyncError, parse_connection_code

PRESET_FILENAMES = ("ProfitHelper-sync.txt", "利润助手同步配置.txt")
_CODE_RE = re.compile(r"PH1-[A-Za-z0-9_-]+")

# 写在 exe 末尾的内置连接码：b"\nPROFITHELPER-PRESET:PH1-…\n"，只在最后 4KB 里查找
EMBED_MARKER = b"PROFITHELPER-PRESET:"
EMBED_TAIL_BYTES = 4096
_EMBED_RE = re.compile(rb"PROFITHELPER-PRESET:(PH1-[A-Za-z0-9_-]+)")


def _valid(code: str | None) -> str | None:
    if not code:
        return None
    try:
        parse_connection_code(code)
    except SyncError:
        return None
    return code


def _code_in_file(path: Path) -> str | None:
    try:
        match = _CODE_RE.search(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError):
        return None
    return _valid(match.group(0)) if match else None


def read_embedded_code(path: str | Path) -> str | None:
    """读取写在文件末尾的内置连接码（见 tools/embed_preset.py）。"""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - EMBED_TAIL_BYTES))
            tail = fh.read()
    except OSError:
        return None
    matches = _EMBED_RE.findall(tail)
    return _valid(matches[-1].decode("ascii")) if matches else None


def _preset_files() -> list[Path]:
    files: list[Path] = []
    if getattr(sys, "frozen", False):  # 在压缩包里直接打开时，exe 所在文件夹与数据文件夹不同
        files += [executable_dir() / name for name in PRESET_FILENAMES]
    files += [data_file_path(name) for name in PRESET_FILENAMES]
    return list(dict.fromkeys(files))


def find_preset() -> tuple[str, str] | None:
    """返回 (连接码, 来源)；来源为 file（exe 旁的配置文件）/ embedded（exe 内置）/ bundled（打包资源）。"""
    for path in _preset_files():
        code = _code_in_file(path)
        if code:
            return code, "file"
    if getattr(sys, "frozen", False):
        code = read_embedded_code(sys.executable)
        if code:
            return code, "embedded"
    code = _code_in_file(resource_path("assets", "sync_preset.txt"))
    return (code, "bundled") if code else None


def find_preset_code() -> str | None:
    found = find_preset()
    return found[0] if found else None


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
