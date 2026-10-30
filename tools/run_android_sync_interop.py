"""
tools/run_android_sync_interop.py
桌面端 ↔ 同步服务 ↔ 安卓端 的端到端互通测试（只用本机临时服务，不碰真实账本）。

前提：模拟器已启动（adb devices 能看到）。运行：.venv/bin/python tools/run_android_sync_interop.py
"""

from __future__ import annotations

import json
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
from core.formula_engine import format_formula  # noqa: E402
from core.sync_client import SyncConfig, SyncEngine  # noqa: E402

PORT = 18766
TEST_PACKAGE = "com.homeapp.profithelper"


def expect(condition: bool, message: str) -> None:
    print(("  ✓ " if condition else "  ✗ ") + message)
    if not condition:
        raise SystemExit(1)


def run_android_tests(token: str, filter_key: str, filter_value: str, label: str) -> None:
    cmd = ["./gradlew", "--console=plain", "-q", ":app:connectedDebugAndroidTest",
           f"-Pandroid.testInstrumentationRunnerArguments.serverUrl=http://10.0.2.2:{PORT}",
           f"-Pandroid.testInstrumentationRunnerArguments.syncToken={token}",
           f"-Pandroid.testInstrumentationRunnerArguments.{filter_key}={filter_value}"]
    run = subprocess.run(cmd, cwd=ROOT / "android", capture_output=True, text=True)
    tail = "\n".join((run.stdout + run.stderr).strip().splitlines()[-25:])
    expect(run.returncode == 0, f"安卓端 {label} 全部通过" + ("" if run.returncode == 0 else "\n" + tail))


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

        print("2. 在模拟器上运行安卓端同步测试（含安卓端改名）")
        later = f"{TEST_PACKAGE}.DesktopRenameInteropTest,{TEST_PACKAGE}.DesktopRecomputeInteropTest"
        run_android_tests(token, "notClass", later, "SyncInteropTest 与本机账本测试")

        print("3. 桌面端拉取并核对安卓端写回的结果")
        engine.sync()
        expect(desktop.get_config("profit_formula") == "现金 + 微信收款 - 进货 + 备用金",
               "安卓端改名后整理过格式的公式到达桌面端")
        expect([n["name"] for n in json.loads(desktop.get_config("custom_field_tree"))] == ["营业额", "成本", "备用金"],
               "安卓端改名后的分类到达桌面端")
        second = desktop.get_record("2026-09-02")
        expect(second is not None and second["note"] == "安卓修改" and second["total_profit"] == -700.0
               and second["raw_data"] == {"营业额": {"现金": 200.0, "微信收款": 0.0}, "成本": {"进货": 900.0}, "备用金": 0.0},
               "安卓修改、又被安卓端改名的 2026-09-02 完整回到桌面端（金额、明细、备注）")
        fifth = desktop.get_record("2026-09-05") or {}
        expect(fifth.get("note") == "" and fifth.get("raw_data", {}).get("旧分类") == 3.0,
               "安卓端清空的备注、保留的旧分类数据到达桌面端")
        expect(desktop.get_record("2026-09-06") is None, "先改后删：桌面端这一天已删除")
        expect((desktop.get_record("2026-09-08") or {}).get("note") == "正常", "坏记录旁边的正常记录照常同步")
        expect(desktop.get_record("2026/09/07") is None and store.status()["record_count"] == 4,
               "日期格式错误的记录没有进入服务器")
        expect(desktop.count_pending_changes() == 0, "桌面端没有遗留未上传的修改")

        print("4. 桌面端改名（一级“成本”→“支出”、二级“进货”→“进货款”）并同步")
        tree = [{"name": "营业额", "children": ["现金", "微信收款"]}, {"name": "支出", "children": ["进货款"]},
                {"name": "备用金", "children": []}]
        migrated = desktop.save_settings(tree, format_formula("现金+微信收款-进货款+备用金"),
                                         {"成本": "支出"}, {"支出": {"进货": "进货款"}})
        expect(migrated == 4, "桌面端 4 天记录改写了名称")
        engine.sync()
        expect(desktop.count_pending_changes() == 0, "改名后的设置和记录都已上传")
        run_android_tests(token, "class", f"{TEST_PACKAGE}.DesktopRenameInteropTest", "DesktopRenameInteropTest")

        print("5. 桌面端改公式（去掉“+ 备用金”）并选择“重算历史”，同步到安卓端")
        preview = desktop.preview_profit_changes(tree, format_formula("现金+微信收款-进货款"))
        expect(preview.affected >= 1 and preview.changed, f"保存前预览：{len(preview.changed)} 天利润会变化")
        desktop.save_settings(tree, format_formula("现金+微信收款-进货款"))
        recomputed = desktop.recompute_profits()
        expect(recomputed == len(preview.changed) and desktop.get_record("2026-09-01")["total_profit"] == 1050.25,
               f"桌面端按新公式重算了 {recomputed} 天（2026-09-01：1070.25 → 1050.25）")
        engine.sync()
        expect(desktop.count_pending_changes() == 0, "重算后的记录都已上传")
        run_android_tests(token, "class", f"{TEST_PACKAGE}.DesktopRecomputeInteropTest", "DesktopRecomputeInteropTest")
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
