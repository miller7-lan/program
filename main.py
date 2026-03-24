"""
main.py — 利润助手程序入口

启动流程：
  1. 创建 QApplication
  2. 初始化 DatabaseManager（自动建表，生成 family_ledger.db）
  3. 显示 MainWindow（内置导航 + 4个页面视图）
  4. 进入 Qt 事件循环，用户关闭窗口后安全退出

跨平台说明：
  - 数据库文件 family_ledger.db 存储于可执行文件同级目录
  - 所有路径拼接使用 pathlib.Path，兼容 Windows / macOS / Linux

打包为 Windows .exe（在 Windows 10 机器或虚拟机上执行）：
  pip install pyinstaller
  pyinstaller --noconsole --onefile main.py
"""

import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from core.database import DatabaseManager
from ui.main_window import MainWindow


def main() -> None:
    # 高分屏自适应（Windows 10/11 尤其需要）
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("利润助手")
    app.setOrganizationName("HomeApp")

    # 初始化数据库（第一次运行时自动建表写入默认配置）
    db = DatabaseManager()

    # 显示主窗口
    window = MainWindow(db)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
