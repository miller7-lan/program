"""
tools/preview_screenshots.py
用演示数据打开桌面端主窗口，依次截取各页面（含设置页整页），用于在 Windows 上检查实际显示效果。

  python tools/preview_screenshots.py --out screenshots --size 1280x720 --scale 1.25
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="screenshots")
    parser.add_argument("--size", default="1280x720")
    parser.add_argument("--scale", type=float, default=1.0)
    args = parser.parse_args()
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QScrollArea

    from core.database import DatabaseManager
    from core.version import APP_VERSION
    from tools.demo_data import make_demo_db
    from ui.main_window import MainWindow
    from ui.sync_manager import SyncManager
    from ui.theme import apply_app_theme

    width, height = (int(v) for v in args.size.lower().split("x"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = f"{width}x{height}-{int(args.scale * 100)}pct"

    app = QApplication(sys.argv[:1])
    apply_app_theme(app)
    tmp = Path(tempfile.mkdtemp(prefix="profit-preview-"))
    db = DatabaseManager(make_demo_db(tmp / "family_ledger.db"))
    window = MainWindow(db, SyncManager(db, APP_VERSION))
    window.resize(width, height)
    window.show()
    pages = ["1-dashboard", "2-entry", "3-history", "4-settings"]
    saved: list[str] = []

    def shoot(index: int = 0) -> None:
        if index == len(pages):
            settings = window._views[3]
            area = settings.findChild(QScrollArea)
            content = area.widget()
            content.resize(area.viewport().width(), content.sizeHint().height())
            target = out / f"win-5-settings-full-{tag}.png"
            content.grab().save(str(target))
            saved.append(target.name)
            window.close()
            app.quit()
            return
        window._switch_page(index)

        def grab() -> None:
            target = out / f"win-{pages[index]}-{tag}.png"
            window.grab().save(str(target))
            saved.append(target.name)
            shoot(index + 1)

        QTimer.singleShot(500, grab)

    QTimer.singleShot(400, shoot)
    app.exec()
    print("\n".join(saved))
    return 0 if len(saved) == len(pages) + 1 else 1


if __name__ == "__main__":
    sys.exit(main())
