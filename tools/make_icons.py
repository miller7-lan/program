"""
tools/make_icons.py
从同一份几何定义生成全部图标：
  assets/app_icon.svg / .png / .ico / .icns（桌面端）
  android/app/src/main/res/drawable/ic_launcher_*.xml（安卓自适应图标）

运行：.venv/bin/python tools/make_icons.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACCENT = "#5B4FE0"
AMBER = "#FFC24B"

BARS = [  # x, y, w, h, 透明度
    (236, 560, 140, 228, 0.55),
    (442, 452, 140, 336, 0.78),
    (648, 330, 140, 458, 1.0),
]
BAR_RADIUS = 44
LINE = "M228 540 L400 400 L520 468 L723 286"
ARROW = "M812 206 L771 340 L675 232 Z"


def rounded_rect_path(x: float, y: float, w: float, h: float, r: float) -> str:
    return (
        f"M{x + r},{y} H{x + w - r} A{r},{r} 0 0 1 {x + w},{y + r} V{y + h - r} "
        f"A{r},{r} 0 0 1 {x + w - r},{y + h} H{x + r} A{r},{r} 0 0 1 {x},{y + h - r} "
        f"V{y + r} A{r},{r} 0 0 1 {x + r},{y} Z"
    )


def svg_markup(size: int = 1024, background: bool = True) -> str:
    bars = "".join(
        f'<path d="{rounded_rect_path(x, y, w, h, BAR_RADIUS)}" fill="#FFFFFF" fill-opacity="{a}"/>'
        for x, y, w, h, a in BARS
    )
    bg = f'<rect width="1024" height="1024" rx="232" fill="{ACCENT}"/>' if background else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 1024 1024">'
        f"{bg}{bars}"
        f'<path d="{LINE}" fill="none" stroke="{ACCENT}" stroke-width="100" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<path d="{ARROW}" fill="{ACCENT}" stroke="{ACCENT}" stroke-width="60" stroke-linejoin="round"/>'
        f'<path d="{LINE}" fill="none" stroke="{AMBER}" stroke-width="58" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<path d="{ARROW}" fill="{AMBER}" stroke="{AMBER}" stroke-width="22" stroke-linejoin="round"/>'
        "</svg>"
    )


def render_png(svg: str, size: int, out: Path) -> None:
    from PySide6.QtCore import QByteArray, QRectF, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    _ = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter, QRectF(0, 0, size, size))
    painter.end()
    image.save(str(out))


def write_desktop_icons() -> None:
    from PIL import Image

    assets = ROOT / "assets"
    svg = svg_markup()
    (assets / "app_icon.svg").write_text(svg + "\n", encoding="utf-8")
    render_png(svg, 1024, assets / "app_icon.png")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        # 小尺寸单独渲染，比从 1024 缩小更清晰
        ico_sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
        frames = []
        for size in ico_sizes:
            path = tmp_dir / f"ico-{size}.png"
            render_png(svg, size, path)
            frames.append(Image.open(path).convert("RGBA"))
        frames[-1].save(assets / "app_icon.ico", format="ICO", sizes=[(s, s) for s in ico_sizes],
                        append_images=frames[:-1])

        if shutil.which("iconutil"):
            iconset = tmp_dir / "app_icon.iconset"
            iconset.mkdir()
            for size in (16, 32, 128, 256, 512):
                render_png(svg, size, iconset / f"icon_{size}x{size}.png")
                render_png(svg, size * 2, iconset / f"icon_{size}x{size}@2x.png")
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(assets / "app_icon.icns")], check=True)

        preview = Image.new("RGBA", (16 + 24 + 32 + 48 + 64 + 6 * 12, 64 + 24), (243, 244, 248, 255))
        x = 12
        for size in (16, 24, 32, 48, 64):
            img = frames[ico_sizes.index(size)]
            preview.paste(img, (x, 12 + 64 - size), img)
            x += size + 12
        preview.save(ROOT / "tools" / "icon-small-sizes-preview.png")


def write_android_icons() -> None:
    res = ROOT / "android" / "app" / "src" / "main" / "res"
    drawable = res / "drawable"
    anydpi = res / "mipmap-anydpi-v26"
    drawable.mkdir(parents=True, exist_ok=True)
    anydpi.mkdir(parents=True, exist_ok=True)

    # 前景放进 108dp 画布中心，内容半径约 34dp，落在 66dp 安全区内
    scale = 0.082
    translate = 54 - 515 * scale, 54 - 497 * scale

    def foreground(monochrome: bool) -> str:
        white = "#FFFFFF"
        bars = "".join(
            f'\n        <path android:fillColor="{white}" android:fillAlpha="{a if not monochrome else 1.0}" '
            f'android:pathData="{rounded_rect_path(x, y, w, h, BAR_RADIUS)}" />'
            for x, y, w, h, a in BARS
        )
        if monochrome:
            line = (f'\n        <path android:strokeColor="{white}" android:strokeWidth="58" android:strokeLineCap="round" '
                    f'android:strokeLineJoin="round" android:pathData="{LINE}" />'
                    f'\n        <path android:fillColor="{white}" android:strokeColor="{white}" android:strokeWidth="22" '
                    f'android:strokeLineJoin="round" android:pathData="{ARROW}" />')
            halo = ""
        else:
            halo = (f'\n        <path android:strokeColor="{ACCENT}" android:strokeWidth="100" android:strokeLineCap="round" '
                    f'android:strokeLineJoin="round" android:pathData="{LINE}" />'
                    f'\n        <path android:fillColor="{ACCENT}" android:strokeColor="{ACCENT}" android:strokeWidth="60" '
                    f'android:strokeLineJoin="round" android:pathData="{ARROW}" />')
            line = (f'\n        <path android:strokeColor="{AMBER}" android:strokeWidth="58" android:strokeLineCap="round" '
                    f'android:strokeLineJoin="round" android:pathData="{LINE}" />'
                    f'\n        <path android:fillColor="{AMBER}" android:strokeColor="{AMBER}" android:strokeWidth="22" '
                    f'android:strokeLineJoin="round" android:pathData="{ARROW}" />')
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<!-- 由 tools/make_icons.py 生成，请勿手改 -->\n'
            '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
            '    android:width="108dp" android:height="108dp"\n'
            '    android:viewportWidth="108" android:viewportHeight="108">\n'
            f'    <group android:scaleX="{scale}" android:scaleY="{scale}" '
            f'android:translateX="{translate[0]:.3f}" android:translateY="{translate[1]:.3f}">'
            f"{bars}{halo}{line}\n"
            "    </group>\n"
            "</vector>\n"
        )

    (drawable / "ic_launcher_foreground.xml").write_text(foreground(False), encoding="utf-8")
    (drawable / "ic_launcher_monochrome.xml").write_text(foreground(True), encoding="utf-8")
    (drawable / "ic_launcher_background.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!-- 由 tools/make_icons.py 生成，请勿手改 -->\n'
        '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    android:width="108dp" android:height="108dp"\n'
        '    android:viewportWidth="108" android:viewportHeight="108">\n'
        f'    <path android:fillColor="{ACCENT}" android:pathData="M0,0h108v108h-108z" />\n'
        "</vector>\n",
        encoding="utf-8",
    )
    adaptive = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@drawable/ic_launcher_background" />\n'
        '    <foreground android:drawable="@drawable/ic_launcher_foreground" />\n'
        '    <monochrome android:drawable="@drawable/ic_launcher_monochrome" />\n'
        "</adaptive-icon>\n"
    )
    (anydpi / "ic_launcher.xml").write_text(adaptive, encoding="utf-8")
    (anydpi / "ic_launcher_round.xml").write_text(adaptive, encoding="utf-8")


if __name__ == "__main__":
    write_desktop_icons()
    write_android_icons()
    print("图标已生成")
