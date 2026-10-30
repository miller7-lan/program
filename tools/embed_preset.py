"""
tools/embed_preset.py
把连接码写到打包好的 ProfitHelper.exe 末尾，得到单个文件就能自动连接服务器的程序（点开即用）。

只在本机对 GitHub Actions 打包出的 exe 执行；输出的 exe 含同步密钥，不要上传到公开的地方：

  python tools/embed_preset.py ProfitHelper.exe ProfitHelper-sync.txt 输出文件夹/ProfitHelper.exe

PyInstaller 的启动器会从文件末尾向前查找自己的数据，追加在末尾的内容不影响启动；
程序启动时读取 exe 最后 4KB 里的连接码（见 core/sync_preset.py）。已写入过的会先去掉旧的再写新的。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.sync_client import parse_connection_code  # noqa: E402
from core.sync_preset import EMBED_MARKER, EMBED_TAIL_BYTES  # noqa: E402


def embed(exe: Path, code: str, out: Path) -> None:
    parse_connection_code(code)  # 连接码无效时直接报错，不生成文件
    data = exe.read_bytes()
    previous = data.rfind(b"\n" + EMBED_MARKER, max(0, len(data) - EMBED_TAIL_BYTES))
    if previous != -1:
        data = data[:previous]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data + b"\n" + EMBED_MARKER + code.encode("ascii") + b"\n")


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__)
        return 2
    exe, preset_file, out = (Path(arg) for arg in argv[1:])
    match = re.search(r"PH1-[A-Za-z0-9_-]+", preset_file.read_text(encoding="utf-8-sig"))
    if not match:
        print("配置文件里没有找到 PH1- 开头的连接码")
        return 1
    embed(exe, match.group(0), out)
    print(f"已写入内置连接码：{out}（{out.stat().st_size} 字节）")  # 不打印连接码本身
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
