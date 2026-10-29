"""
tools/run_android_sync_interop.py
桌面端 ↔ 同步服务 ↔ 安卓端 的端到端互通测试（只用本机临时服务，不碰真实账本）。

前提：模拟器已启动（adb devices 能看到）。运行：.venv/bin/python tools/run_android_sync_interop.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

import profit_sync_server as pss  # noqa: E402
from core.database import DatabaseManager  # noqa: E402
from core.sync_client import SyncConfig, SyncEngine  # noqa: E402

PORT = 18766


def expect(condition: bool, message: str) -> None:
    print(("  ✓ " if condition else "  ✗ ") + message)
    if not condition:
        raise SystemExit(1)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="android-interop-"))
    token = pss.generate_token()
    config = dict(pss.DEFAULT_CONFIG, listen_host="127.0.0.1", listen_port=PORT, data_dir=str(tmp / "server"),
                  tokens=[{"name": "interop", "sha256": pss.hash_token(token)}])
    store = pss.LedgerStore(tmp / "server" / "ledger.db")
    httpd = pss.SyncHTTPServer(("127.0.0.1", PORT), store, config, None)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    desktop = DatabaseManager(tmp / "desktop.db")
    try:
        engine = SyncEngine(desktop, app_version="interop")
        engine.save_config(SyncConfig(f"http://127.0.0.1:{PORT}", token))
        print("1. 桌面端写入种子数据")
        tree = '[{"name": "营业额", "children": ["现金", "微信"]}, {"name": "成本", "children": ["进货"]}, {"name": "零钱", "children": []}]'
        desktop.set_configs({"custom_field_tree": tree, "custom_fields": '["营业额", "现金", "微信", "成本", "进货", "零钱"]',
                             "profit_formula": "现金 + 微信 - 进货 + 零钱"})
        desktop.upsert_record("2026-09-01", 1070.25, {"营业额": {"现金": 1200.5, "微信": 300.0}, "成本": {"进货": 450.25}, "零钱": 20.0},
                              "桌面端录入，含中文与符号：¥/%")
        desktop.upsert_record("2026-09-02", -800.0, {"营业额": {"现金": 100.0, "微信": 0.0}, "成本": {"进货": 900.0}, "零钱": 0.0}, "")
        desktop.upsert_record("2026-09-03", 1.0, {"零钱": 1.0}, "将被删除")
        engine.sync()
        desktop.delete_record("2026-09-03")
        engine.sync()
        expect(store.status()["record_count"] == 2, "服务器上有 2 天记录（另 1 天已删除）")

        print("2. 在模拟器上运行安卓端同步测试")
        cmd = ["./gradlew", "--console=plain", "-q", ":app:connectedDebugAndroidTest",
               f"-Pandroid.testInstrumentationRunnerArguments.serverUrl=http://10.0.2.2:{PORT}",
               f"-Pandroid.testInstrumentationRunnerArguments.syncToken={token}"]
        run = subprocess.run(cmd, cwd=ROOT / "android", capture_output=True, text=True)
        tail = "\n".join((run.stdout + run.stderr).strip().splitlines()[-25:])
        expect(run.returncode == 0, "安卓端 SyncInteropTest 全部通过" + ("" if run.returncode == 0 else "\n" + tail))

        print("3. 桌面端拉取并核对安卓端写回的结果")
        engine.sync()
        second = desktop.get_record("2026-09-02")
        expect(second is not None and second["note"] == "安卓修改" and second["total_profit"] == -700.0
               and second["raw_data"] == {"营业额": {"现金": 200.0, "微信": 0.0}, "成本": {"进货": 900.0}, "零钱": 0.0},
               "安卓修改的 2026-09-02 完整回到桌面端（金额、明细、备注）")
        expect((desktop.get_record("2026-09-05") or {}).get("note") == "B 后改", "先删后改：桌面端看到的是后修改的版本")
        expect(desktop.get_record("2026-09-06") is None, "先改后删：桌面端这一天已删除")
        expect((desktop.get_record("2026-09-08") or {}).get("note") == "正常", "坏记录旁边的正常记录照常同步")
        expect(desktop.get_record("2026/09/07") is None and store.status()["record_count"] == 4,
               "日期格式错误的记录没有进入服务器")
        expect(desktop.count_pending_changes() == 0, "桌面端没有遗留未上传的修改")
        print("全部通过")
        return 0
    finally:
        desktop.close()
        httpd.shutdown()
        httpd.server_close()
        store.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    os.environ.setdefault("JAVA_HOME", "/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home")
    sys.exit(main())
