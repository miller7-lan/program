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
  各视图在 showEvent 里刷新，保证数据最新；云同步拉取到新数据时通过 event_bus 通知刷新。
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.app_paths import resource_path
from core.event_bus import event_bus
from core.version import APP_VERSION
from ui import icons, theme
from ui.dashboard_view import DashboardView
from ui.entry_view import EntryView
from ui.history_view import HistoryView
from ui.settings_view import SettingsView
from ui.widgets import IconLabel, Toast, label, make_button, set_tone


class NavButton(QPushButton):
    """左侧导航栏的单个按钮。"""

    def __init__(self, text: str, icon_name: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("NavButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(42)
        icons.set_button_icon(self, icon_name, theme.TEXT_2, 18, checked_color=theme.ACCENT)


class SyncPanel(QFrame):
    """侧边栏底部的云同步状态。"""

    def __init__(self, sync_manager, open_settings, parent=None):
        super().__init__(parent)
        self.setObjectName("SyncPanel")
        self._sync = sync_manager
        self._open_settings = open_settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        self._icon = IconLabel("cloud", theme.TEXT_3, 16)
        top.addWidget(self._icon)
        self._title = label("", "SyncTitle")
        top.addWidget(self._title, 1)
        layout.addLayout(top)

        self._detail = label("", "SyncDetail", wrap=True)
        layout.addWidget(self._detail)

        self._button = make_button("立即同步", "ghost", "sync")
        self._button.clicked.connect(self._on_click)
        layout.addWidget(self._button, alignment=Qt.AlignmentFlag.AlignLeft)

        sync_manager.status_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        title, detail, tone = self._sync.status_view()
        self._title.setText(title)
        set_tone(self._title, tone)
        self._detail.setText(detail)
        self._detail.setToolTip(detail)
        icon_name, color = {
            "ok": ("cloud-check", theme.SUCCESS),
            "error": ("alert", theme.DANGER),
            "busy": ("sync", theme.ACCENT),
            "off": ("cloud-off", theme.TEXT_3),
        }.get(tone, ("cloud", theme.TEXT_3))
        self._icon.set_icon(icon_name, color)
        configured = self._sync.configured
        self._button.setText("立即同步" if configured else "去设置")
        icons.set_button_icon(self._button, "sync" if configured else "settings", theme.TEXT_2, 16)
        self._button.setEnabled(tone != "busy" or not configured)

    def _on_click(self) -> None:
        if self._sync.configured:
            self._sync.sync_now()
        else:
            self._open_settings()


class MainWindow(QMainWindow):
    """应用主窗口。"""

    _NAV_ITEMS = [
        ("看板", "dashboard", DashboardView),
        ("录入", "entry", EntryView),
        ("历史", "history", HistoryView),
        ("设置", "settings", SettingsView),
    ]

    def __init__(self, db, sync_manager, parent=None):
        super().__init__(parent)
        self._db = db
        self._sync = sync_manager
        self.setWindowTitle("利润助手")
        self.setMinimumSize(960, 560)
        self._apply_app_icon()
        self._build_ui()
        self.setStyleSheet(theme.load_stylesheet())
        self._fit_to_screen()

        self.toast = Toast(self.centralWidget())
        event_bus.toast_requested.connect(self.toast.show_message)
        event_bus.edit_date_requested.connect(self._open_entry_for_date)

        for index in range(len(self._NAV_ITEMS)):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda idx=index: self._switch_page(idx))

        self._switch_page(0)

    # ─────────────────────────────────────
    # UI 构建
    # ─────────────────────────────────────

    def _apply_app_icon(self) -> None:
        icon_path = resource_path("assets", "app_icon.png")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("AppRoot")
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(208)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 18, 14, 14)
        side.setSpacing(4)

        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 14)
        brand.setSpacing(10)
        logo = QLabel()
        icon_path = resource_path("assets", "app_icon.png")
        if icon_path.exists():
            pixmap = QPixmap(str(icon_path))
            pixmap.setDevicePixelRatio(2.0)
            logo.setPixmap(pixmap.scaled(QSize(68, 68), Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
        logo.setFixedSize(34, 34)
        brand.addWidget(logo)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        brand_text.addWidget(label("利润助手", "BrandTitle"))
        brand_text.addWidget(label("家庭利润记账", "BrandSubtitle"))
        brand.addLayout(brand_text)
        brand.addStretch()
        side.addLayout(brand)

        self._nav_buttons: list[NavButton] = []
        self._stack = QStackedWidget()
        self._views = []
        for index, (text, icon_name, view_class) in enumerate(self._NAV_ITEMS):
            button = NavButton(f"  {text}", icon_name)
            button.setToolTip(f"{text}（Ctrl+{index + 1}）")
            button.clicked.connect(lambda checked=False, idx=index: self._switch_page(idx))
            self._nav_buttons.append(button)
            side.addWidget(button)
            view = view_class(self._db, self._sync) if view_class is SettingsView else view_class(self._db)
            self._views.append(view)
            self._stack.addWidget(view)

        side.addStretch()
        side.addWidget(SyncPanel(self._sync, lambda: self._switch_page(3)))
        side.addSpacing(8)
        version = label(f"v{APP_VERSION}", "VersionLabel")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side.addWidget(version)

        root_layout.addWidget(sidebar)
        root_layout.addWidget(self._stack, stretch=1)

    def _fit_to_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1280, 820)
            return
        available = screen.availableGeometry()
        width = min(1360, max(self.minimumWidth(), int(available.width() * 0.86)))
        height = min(900, max(self.minimumHeight(), int(available.height() * 0.88)))
        self.resize(width, height)
        self.move(available.center().x() - width // 2, available.center().y() - height // 2)

    # ─────────────────────────────────────
    # 页面切换
    # ─────────────────────────────────────

    @property
    def _entry_view(self) -> EntryView:
        return self._views[1]

    def _switch_page(self, index: int) -> None:
        current = self._stack.currentIndex()
        if current == 1 and index != 1 and not self._entry_view.confirm_leave():
            self._nav_buttons[current].setChecked(True)
            return
        for i, button in enumerate(self._nav_buttons):
            button.setChecked(i == index)
        self._stack.setCurrentIndex(index)

    def _open_entry_for_date(self, date_text: str) -> None:
        self._switch_page(1)
        if self._stack.currentIndex() == 1:
            self._entry_view.open_date(date_text)

    # ─────────────────────────────────────
    # 生命周期
    # ─────────────────────────────────────

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if hasattr(self, "toast") and self.toast.isVisible():
            self.toast.move((self.centralWidget().width() - self.toast.width()) // 2,
                            self.centralWidget().height() - self.toast.height() - 36)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        """关闭窗口时：确认未保存的录入，完成最后一次同步，再安全关闭数据库连接。"""
        if not self._entry_view.confirm_leave():
            event.ignore()
            return
        self._sync.shutdown()
        self._db.close()
        super().closeEvent(event)
