"""
ui/main_window.py
主窗口 — 左侧导航栏（固定宽度） + 右侧 QStackedWidget 内容区。

导航项：
  0: 看板     DashboardView
  1: 录入     EntryView
  2: 历史     HistoryView
  3: 设置     SettingsView

信号联动：
  点击导航按钮 → setCurrentIndex → 当前视图激活
  各视图在 showEvent 里触发 self.refresh()，保证数据最新。
"""

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui.dashboard_view import DashboardView
from ui.entry_view import EntryView
from ui.history_view import HistoryView
from ui.settings_view import SettingsView


# ──────────────────────────────────────────────────────────────
# 导航按钮
# ──────────────────────────────────────────────────────────────
class NavButton(QPushButton):
    """左侧导航栏的单个按钮。激活状态时显示高亮左边条。"""

    def __init__(self, label: str, parent=None):
        super().__init__(label, parent)
        self.setCheckable(True)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(52)
        self.setFont(QFont("PingFang SC", 14))
        self.setObjectName("NavButton")


# ──────────────────────────────────────────────────────────────
# 主窗口
# ──────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    """应用主窗口。"""

    # 导航项：(中文标签, 视图类)
    _NAV_ITEMS = [
        ("📊  看板", DashboardView),
        ("✏️  录入", EntryView),
        ("📋  历史", HistoryView),
        ("⚙️  设置", SettingsView),
    ]

    def __init__(self, db, parent=None):
        """
        Args:
            db: DatabaseManager 实例，传递给各视图
        """
        super().__init__(parent)
        self._db = db
        self.setWindowTitle("利润助手")
        self.setMinimumSize(960, 640)
        self.resize(1100, 700)

        self._build_ui()
        self._apply_styles()

        # 默认选中第一个导航项
        self._nav_buttons[0].setChecked(True)
        self._stack.setCurrentIndex(0)

    # ─────────────────────────────────────
    # UI 构建
    # ─────────────────────────────────────

    def _build_ui(self) -> None:
        """构建整体布局：左侧导航栏 + 右侧内容区。"""
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── 左侧导航栏 ──────────────────────
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(210)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        # App 标题区域
        title_area = QFrame()
        title_area.setObjectName("TitleArea")
        title_area.setFixedHeight(72)
        title_layout = QHBoxLayout(title_area)
        app_title = QLabel("💰 利润助手")
        app_title.setObjectName("AppTitle")
        app_title.setFont(QFont("PingFang SC", 16, QFont.Weight.Bold))
        title_layout.addWidget(app_title)
        sidebar_layout.addWidget(title_area)

        # 导航按钮区域
        self._nav_buttons: list[NavButton] = []
        self._stack = QStackedWidget()

        for i, (label, ViewClass) in enumerate(self._NAV_ITEMS):
            btn = NavButton(label)
            btn.clicked.connect(lambda checked, idx=i: self._switch_page(idx))
            self._nav_buttons.append(btn)
            sidebar_layout.addWidget(btn)

            # 创建对应视图（把 db 传入）
            view = ViewClass(self._db)
            self._stack.addWidget(view)

        # 底部弹性空间
        sidebar_layout.addStretch()

        # 版本号
        version_label = QLabel("v0.1.0  骨架版")
        version_label.setObjectName("VersionLabel")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sidebar_layout.addWidget(version_label)
        sidebar_layout.addSpacing(12)

        # ── 分隔线 ───────────────────────────
        separator = QFrame()
        separator.setObjectName("Separator")
        separator.setFixedWidth(1)

        # ── 右侧内容区 ───────────────────────
        root_layout.addWidget(sidebar)
        root_layout.addWidget(separator)
        root_layout.addWidget(self._stack, stretch=1)

    # ─────────────────────────────────────
    # 页面切换
    # ─────────────────────────────────────

    def _switch_page(self, index: int) -> None:
        """切换到指定页面，并更新导航按钮高亮状态。"""
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == index)
        self._stack.setCurrentIndex(index)

    # ─────────────────────────────────────
    # 样式
    # ─────────────────────────────────────

    def _apply_styles(self) -> None:
        """从 assets/style.qss 加载样式表，加载失败则使用内置默认样式。"""
        import os
        qss_path = os.path.join(os.path.dirname(__file__), "..", "assets", "style.qss")
        qss_path = os.path.normpath(qss_path)
        try:
            with open(qss_path, encoding="utf-8") as f:
                self.setStyleSheet(f.read())
        except FileNotFoundError:
            self._apply_fallback_styles()

    def _apply_fallback_styles(self) -> None:
        """内置最小化样式（载入 QSS 失败时的备用方案）。"""
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #F5F6FA; color: #1A1A2E; }
            #Sidebar       { background: #FFFFFF; }
            #TitleArea     { background: #FFFFFF; padding-left: 20px; }
            #AppTitle      { color: #6C5CE7; }
            #VersionLabel  { color: #BABACF; font-size: 11px; }
            #Separator     { background: #E8E8F0; }
            #NavButton     { text-align: left; padding-left: 24px;
                            color: #7A7A9D; background: transparent;
                            border: none; border-left: 3px solid transparent; }
            #NavButton:hover    { background: #F0EEFF; color: #3D3D5C; }
            #NavButton:checked  { color: #6C5CE7;
                                  border-left: 3px solid #6C5CE7;
                                  background: #EDE9FF; }
        """)

    # ─────────────────────────────────────
    # 生命周期
    # ─────────────────────────────────────

    def closeEvent(self, event) -> None:
        """关闭窗口时，安全关闭数据库连接。"""
        self._db.close()
        super().closeEvent(event)
