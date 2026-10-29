"""
ui/theme.py
统一的颜色、字体与数字格式。QSS 样式表（assets/style.qss）与自绘控件共用这里的取值。
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from core.app_paths import resource_path

BG = "#F3F4F8"
SURFACE = "#FFFFFF"
SURFACE_ALT = "#F8F9FC"
BORDER = "#E4E7EF"
BORDER_STRONG = "#D5D9E5"
TEXT = "#1D2130"
TEXT_2 = "#5B6175"
TEXT_3 = "#8C92A6"
ACCENT = "#5B4FE0"
ACCENT_HOVER = "#4C40D0"
ACCENT_SOFT = "#EEECFD"
DANGER = "#D9363E"
DANGER_SOFT = "#FDECEC"
SUCCESS = "#1A9E5C"
SUCCESS_SOFT = "#E6F6EE"
WARNING = "#C77700"
WARNING_SOFT = "#FFF4E0"

FONT_FAMILIES = ["Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Segoe UI", "sans-serif"]


def apply_app_theme(app: QApplication) -> None:
    """Fusion 风格 + 固定浅色调色板：Win10 开启深色模式时也不会出现白底白字。"""
    app.setStyle("Fusion")
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme(Qt.ColorScheme.Light)

    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: BG,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: SURFACE,
        QPalette.ColorRole.AlternateBase: SURFACE_ALT,
        QPalette.ColorRole.ToolTipBase: TEXT,
        QPalette.ColorRole.ToolTipText: SURFACE,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: SURFACE,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.BrightText: SURFACE,
        QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: SURFACE,
        QPalette.ColorRole.PlaceholderText: TEXT_3,
        QPalette.ColorRole.Link: ACCENT,
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(TEXT_3))
    app.setPalette(palette)

    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    font.setPointSizeF(10.5 if sys.platform == "win32" else 13)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(font)


def load_stylesheet() -> str:
    """读取 QSS，并把 {ASSETS} 占位符替换为资源目录的绝对路径（打包后同样可用）。"""
    path = resource_path("assets", "style.qss")
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    assets = resource_path("assets").as_posix()
    return text.replace("{ASSETS}", assets)


def repolish(widget) -> None:
    """动态属性变化后刷新样式。"""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


# ─────────────────────────────────────────────
# 数字格式
# ─────────────────────────────────────────────

def fmt_money(value: float, with_symbol: bool = True, signed: bool = False) -> str:
    symbol = "¥" if with_symbol else ""
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    return f"{sign}{symbol}{abs(value):,.2f}"


def fmt_compact(value: float) -> str:
    """坐标轴用的紧凑格式：12,000 → 1.2万。"""
    magnitude = abs(value)
    if magnitude >= 100_000_000:
        return f"{value / 100_000_000:.1f}亿".replace(".0亿", "亿")
    if magnitude >= 10_000:
        return f"{value / 10_000:.1f}万".replace(".0万", "万")
    if magnitude >= 1000 or value == int(value):
        return f"{value:,.0f}"
    return f"{value:,.1f}"


def fmt_amount_input(value) -> str:
    """录入框里显示的金额：0 显示为空，整数不带小数位。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number == 0:
        return ""
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return f"{number:.4f}".rstrip("0").rstrip(".")
