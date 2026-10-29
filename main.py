"""
main.py — 利润助手程序入口

启动流程：
  1. 创建 QApplication，应用浅色主题（Win10 深色模式下同样清晰）
  2. 确保只运行一个实例（两个窗口同时写同一个账本容易出错）
  3. 初始化 DatabaseManager（自动建表 / 升级，生成 family_ledger.db）
  4. 显示 MainWindow，并启动云同步调度
  5. 进入 Qt 事件循环，用户关闭窗口后完成最后一次同步并安全退出

跨平台说明：
  - 数据库文件 family_ledger.db 存储于可执行文件同级目录
  - 所有路径拼接使用 pathlib.Path，兼容 Windows / macOS / Linux

打包为 Windows .exe：见 README.md 与 build_windows_exe.bat
"""

import sys
import traceback

from PySide6.QtCore import QLockFile, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from core.app_paths import data_file_path, resource_path
from core.database import DatabaseManager
from core.version import APP_VERSION
from ui.main_window import MainWindow
from ui.sync_manager import SyncManager
from ui.theme import apply_app_theme


def _run_smoke_test(app: QApplication, window: MainWindow) -> None:
    """打包自检（--smoke-test）：依次打开四个页面后退出，结果写入 smoke-test.txt。"""
    errors: list[str] = []
    sys.excepthook = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))

    def step(index: int = 0) -> None:
        if index < 4:
            window._switch_page(index)
            QTimer.singleShot(400, lambda: step(index + 1))
            return
        data_file_path("smoke-test.txt").write_text("ok\n" if not errors else "\n".join(errors), encoding="utf-8")
        app.exit(1 if errors else 0)

    QTimer.singleShot(500, step)


def main() -> None:
    # 高分屏自适应（Windows 10/11 尤其需要）
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("利润助手")
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("HomeApp")
    apply_app_theme(app)
    icon_path = resource_path("assets", "app_icon.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    lock = QLockFile(str(data_file_path("family_ledger.lock")))
    lock.setStaleLockTime(0)  # 只在原进程已退出时才视为失效
    if not lock.tryLock(200):
        QMessageBox.information(None, "利润助手", "利润助手已经打开了，请在任务栏中切换到已打开的窗口。")
        sys.exit(0)

    # 初始化数据库（第一次运行时自动建表写入默认配置；旧版本账本会自动升级）
    db = DatabaseManager()
    sync_manager = SyncManager(db, APP_VERSION)

    window = MainWindow(db, sync_manager)
    window.show()
    if "--smoke-test" in sys.argv:
        _run_smoke_test(app, window)
    else:
        sync_manager.start()

    exit_code = app.exec()
    lock.unlock()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
