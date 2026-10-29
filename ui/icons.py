"""
ui/icons.py
线条风格 SVG 图标（替代 emoji：Win10 上 emoji 渲染不一致，图标颜色也无法跟随状态变化）。
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ui import theme

_PATHS = {
    "dashboard": '<rect x="3" y="3" width="7.5" height="7.5" rx="1.8"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.8"/>'
                 '<rect x="3" y="13.5" width="7.5" height="7.5" rx="1.8"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.8"/>',
    "entry": '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    "history": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3.5 2"/>',
    "settings": '<path d="M4 6h9"/><path d="M19 6h1"/><circle cx="16" cy="6" r="2.5"/><path d="M4 12h2"/>'
                '<path d="M12 12h8"/><circle cx="9" cy="12" r="2.5"/><path d="M4 18h11"/><circle cx="18" cy="18" r="2.5"/>',
    "sync": '<path d="M21 12a9 9 0 0 0-15.5-6.2L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 15.5 6.2L21 16"/>'
            '<path d="M21 21v-5h-5"/>',
    "cloud": '<path d="M17.5 19H8a5 5 0 1 1 .9-9.9A6 6 0 0 1 20 11.5a3.75 3.75 0 0 1-2.5 7.5Z"/>',
    "cloud-check": '<path d="M17.5 19H8a5 5 0 1 1 .9-9.9A6 6 0 0 1 20 11.5a3.75 3.75 0 0 1-2.5 7.5Z"/><path d="m9.5 14 2 2 3.5-3.5"/>',
    "cloud-off": '<path d="m3 3 18 18"/><path d="M8.5 8.6A5 5 0 0 0 8 19h9.5"/><path d="M20.7 16.2A3.75 3.75 0 0 0 17.5 11.5h-.4A6 6 0 0 0 11 6.2"/>',
    "alert": '<circle cx="12" cy="12" r="9"/><path d="M12 8v4.5"/><path d="M12 16h.01"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "calendar": '<rect x="3" y="4.5" width="18" height="16.5" rx="2"/><path d="M16 2.5v4M8 2.5v4M3 10h18"/>',
    "today": '<rect x="3" y="4.5" width="18" height="16.5" rx="2"/><path d="M16 2.5v4M8 2.5v4M3 10h18"/><circle cx="12" cy="15.5" r="1.6"/>',
    "save": '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/>',
    "trash": '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="m19 6-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/>',
    "download": '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
    "upload": '<path d="M12 21V9"/><path d="m7 14 5-5 5 5"/><path d="M5 3h14"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "edit": '<path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>',
    "arrow-up": '<path d="M12 19V5"/><path d="m5 12 7-7 7 7"/>',
    "arrow-down": '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    "sheet": '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z"/><path d="M14 3v6h6"/>'
             '<path d="M8 13h8M8 17h8M12 13v4"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.8-3.8"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
    "copy": '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    "eye-off": '<path d="m3 3 18 18"/><path d="M10.6 5.1A10.4 10.4 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.2"/>'
               '<path d="M6.6 6.6C3.8 8.4 2 12 2 12s3.5 7 10 7a9.7 9.7 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
    "trend": '<path d="m3 17 6-6 4 4 8-8"/><path d="M14 7h7v7"/>',
    "wallet": '<path d="M19 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0 0 4h14a2 2 0 0 1 2 2v3"/><path d="M3 5v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-3"/>'
              '<path d="M21 12h-4a2 2 0 0 0 0 4h4Z"/>',
    "coins": '<circle cx="8" cy="8" r="5"/><path d="M18.1 10.4A5 5 0 1 1 10.4 18"/><path d="M8 6v4M7 14h.01"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/>',
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
    "paste": '<rect x="8" y="2.5" width="8" height="4" rx="1.2"/><path d="M16 4.5h2a2 2 0 0 1 2 2V20a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6.5a2 2 0 0 1 2-2h2"/>',
    "more": '<circle cx="12" cy="5" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="12" cy="19" r="1.2"/>',
}


def _svg(name: str, color: str, stroke: float) -> bytes:
    body = _PATHS[name]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    ).encode("utf-8")


@lru_cache(maxsize=512)
def pixmap(name: str, color: str = theme.TEXT_2, size: int = 18, scale: float = 2.0, stroke: float = 1.9) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(_svg(name, color, stroke)))
    pm = QPixmap(int(size * scale), int(size * scale))
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size * scale, size * scale))
    painter.end()
    pm.setDevicePixelRatio(scale)
    return pm


def icon(name: str, color: str = theme.TEXT_2, size: int = 18, checked_color: str | None = None,
         disabled_color: str | None = theme.TEXT_3) -> QIcon:
    result = QIcon()
    for scale in (1.0, 1.5, 2.0):
        result.addPixmap(pixmap(name, color, size, scale), QIcon.Mode.Normal, QIcon.State.Off)
        if checked_color:
            result.addPixmap(pixmap(name, checked_color, size, scale), QIcon.Mode.Normal, QIcon.State.On)
            result.addPixmap(pixmap(name, checked_color, size, scale), QIcon.Mode.Active, QIcon.State.On)
        if disabled_color:
            result.addPixmap(pixmap(name, disabled_color, size, scale), QIcon.Mode.Disabled, QIcon.State.Off)
    return result


def set_button_icon(button, name: str, color: str = theme.TEXT_2, size: int = 16,
                    checked_color: str | None = None) -> None:
    button.setIcon(icon(name, color, size, checked_color))
    button.setIconSize(QSize(size, size))
