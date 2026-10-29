"""
tools/make_android_ui_icons.py
把桌面端 ui/icons.py 里的线条图标转换成安卓矢量图（res/drawable/ic_*.xml），两端图标保持一致。

运行：python3 tools/make_android_ui_icons.py
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "android" / "app" / "src" / "main" / "res" / "drawable"


def load_paths() -> dict[str, str]:
    tree = ast.parse((ROOT / "ui" / "icons.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_PATHS":
            return ast.literal_eval(node.value)
    raise SystemExit("ui/icons.py 中找不到 _PATHS")


def attrs(fragment: str) -> dict[str, float]:
    return {k: float(v) for k, v in re.findall(r'([a-z]+)="([-0-9.]+)"', fragment)}


def to_path_data(svg: str) -> list[str]:
    result = []
    for tag, body in re.findall(r"<(rect|circle|path)\b([^>]*)/>", svg):
        if tag == "path":
            result.append(re.search(r'd="([^"]+)"', body).group(1))
        elif tag == "circle":
            a = attrs(body)
            cx, cy, r = a["cx"], a["cy"], a["r"]
            result.append(f"M{cx - r},{cy} A{r},{r} 0 1,0 {cx + r},{cy} A{r},{r} 0 1,0 {cx - r},{cy} Z")
        else:
            a = attrs(body)
            x, y, w, h, r = a["x"], a["y"], a["width"], a["height"], a.get("rx", 0.0)
            if r:
                result.append(
                    f"M{x + r},{y} H{x + w - r} A{r},{r} 0 0 1 {x + w},{y + r} V{y + h - r} "
                    f"A{r},{r} 0 0 1 {x + w - r},{y + h} H{x + r} A{r},{r} 0 0 1 {x},{y + h - r} "
                    f"V{y + r} A{r},{r} 0 0 1 {x + r},{y} Z"
                )
            else:
                result.append(f"M{x},{y} H{x + w} V{y + h} H{x} Z")
    return result


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, svg in load_paths().items():
        paths = "".join(
            f'\n    <path android:pathData="{d}" android:fillColor="#00000000" android:strokeColor="#FF000000" '
            'android:strokeWidth="1.9" android:strokeLineCap="round" android:strokeLineJoin="round" />'
            for d in to_path_data(svg)
        )
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<!-- 由 tools/make_android_ui_icons.py 根据 ui/icons.py 生成，请勿手改 -->\n"
            '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
            '    android:width="24dp" android:height="24dp" android:viewportWidth="24" android:viewportHeight="24">'
            f"{paths}\n</vector>\n"
        )
        (OUT / f"ic_{name.replace('-', '_')}.xml").write_text(xml, encoding="utf-8")
    print("已生成", len(load_paths()), "个安卓图标")


if __name__ == "__main__":
    main()
