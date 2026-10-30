"""
ui/widgets.py
通用界面组件：卡片、指标卡、趋势图（QPainter 自绘，替代 matplotlib）、提示气泡、流式布局等。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QWidgetItem,
)

from ui import icons, theme


# ─────────────────────────────────────────────
# 基础组件
# ─────────────────────────────────────────────

class Card(QFrame):
    def __init__(self, parent=None, object_name: str = "Card"):
        super().__init__(parent)
        self.setObjectName(object_name)


def make_button(text: str, variant: str = "", icon_name: str | None = None, icon_color: str | None = None,
                tooltip: str = "") -> QPushButton:
    button = QPushButton(text)
    if variant:
        button.setProperty("variant", variant)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_name:
        default_color = {
            "primary": "#FFFFFF",
            "danger": theme.DANGER,
            "soft": theme.ACCENT,
        }.get(variant, theme.TEXT_2)
        icons.set_button_icon(button, icon_name, icon_color or default_color, 16)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def soft_tint(color: str, strength: float = 0.12) -> str:
    """把颜色按比例与白色混合，得到图标底色之类的浅色。"""
    base = QColor(color)
    mix = lambda channel: round(255 - (255 - channel) * strength)  # noqa: E731
    return QColor(mix(base.red()), mix(base.green()), mix(base.blue())).name()


def label(text: str = "", object_name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if object_name:
        widget.setObjectName(object_name)
    widget.setWordWrap(wrap)
    return widget


def set_tone(widget: QWidget, tone: str) -> None:
    if widget.property("tone") != tone:
        widget.setProperty("tone", tone)
        theme.repolish(widget)


class Badge(QLabel):
    def __init__(self, text: str = "", tone: str = "neutral"):
        super().__init__(text)
        self.setObjectName("Badge")
        self.setProperty("tone", tone)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set(self, text: str, tone: str) -> None:
        self.setText(text)
        self.setVisible(bool(text))
        set_tone(self, tone)


class IconLabel(QLabel):
    def __init__(self, name: str, color: str = theme.TEXT_2, size: int = 18):
        super().__init__()
        self._size = size
        self.set_icon(name, color)
        self.setFixedSize(size, size)

    def set_icon(self, name: str, color: str) -> None:
        self.setPixmap(icons.pixmap(name, color, self._size, 2.0))


def page_scroll(content: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setObjectName("PageScroll")
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    content.setObjectName("PageContent")
    area.setWidget(content)
    return area


def page_header(title: str, subtitle: str = "") -> tuple[QHBoxLayout, QLabel, QHBoxLayout]:
    """返回 (整行布局, 副标题标签, 右侧操作区布局)。"""
    row = QHBoxLayout()
    row.setSpacing(12)
    text_col = QVBoxLayout()
    text_col.setSpacing(2)
    text_col.addWidget(label(title, "PageTitle"))
    sub = label(subtitle, "PageSubtitle")
    text_col.addWidget(sub)
    row.addLayout(text_col)
    row.addStretch()
    actions = QHBoxLayout()
    actions.setSpacing(8)
    row.addLayout(actions)
    return row, sub, actions


class StatCard(Card):
    """指标卡：标题 + 大号数值 + 补充说明。"""

    def __init__(self, title: str, icon_name: str, accent: str = theme.ACCENT):
        super().__init__(object_name="StatCard")
        self.setMinimumHeight(118)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(label(title, "StatTitle"))
        top.addStretch()
        badge = QLabel()
        badge.setObjectName("StatIcon")
        badge.setFixedSize(30, 30)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setPixmap(icons.pixmap(icon_name, accent, 16, 2.0))
        badge.setStyleSheet(f"background: {soft_tint(accent)}; border-radius: 9px;")
        top.addWidget(badge)
        layout.addLayout(top)

        self.value_label = label("—", "StatValue")
        layout.addWidget(self.value_label)
        self.sub_label = label("", "StatSub")
        layout.addWidget(self.sub_label)
        layout.addStretch()

    def set_value(self, value: float | None, sub: str = "", sub_tone: str = "") -> None:
        self.value_label.setText("—" if value is None else theme.fmt_money(value))
        set_tone(self.value_label, "negative" if value is not None and value < 0 else "")
        self.sub_label.setText(sub)
        set_tone(self.sub_label, sub_tone)


class SegmentedControl(QFrame):
    changed = Signal(int)

    def __init__(self, options: list[str], parent=None):
        super().__init__(parent)
        self.setObjectName("Segmented")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for index, text in enumerate(options):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._group.addButton(button, index)
            layout.addWidget(button)
        self._group.button(0).setChecked(True)
        self._group.idClicked.connect(self.changed.emit)

    def current(self) -> int:
        return self._group.checkedId()


# ─────────────────────────────────────────────
# 流式布局（用于公式变量卡片、预设按钮等）
# ─────────────────────────────────────────────

class FlowLayout(QLayout):
    def __init__(self, parent=None, h_spacing: int = 8, v_spacing: int = 8):
        super().__init__(parent)
        self._items: list = []
        self._h = h_spacing
        self._v = v_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802 - Qt API
        self._items.append(item)
        self.invalidate()  # 新增的按钮立即参与排版，不必等窗口改变大小

    def addWidget(self, widget):  # noqa: N802 - Qt API
        self.addChildWidget(widget)
        self.addItem(QWidgetItem(widget))

    def count(self):
        return len(self._items)

    def itemAt(self, index):  # noqa: N802 - Qt API
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):  # noqa: N802 - Qt API
        if not 0 <= index < len(self._items):
            return None
        item = self._items.pop(index)
        self.invalidate()
        return item

    def expandingDirections(self):  # noqa: N802 - Qt API
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width):  # noqa: N802 - Qt API
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):  # noqa: N802 - Qt API
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):  # noqa: N802 - Qt API
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802 - Qt API
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line_height = effective.x(), effective.y(), 0
        for item in self._items:
            if item.widget() is not None and not item.widget().isVisible() and not test_only:
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h
            if next_x - self._h > effective.right() + 1 and line_height > 0:
                x = effective.x()
                y += line_height + self._v
                next_x = x + hint.width() + self._h
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()

    def clear(self) -> None:
        while self._items:
            item = self._items.pop()
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.invalidate()


# ─────────────────────────────────────────────
# 趋势图
# ─────────────────────────────────────────────

class TrendChart(QWidget):
    """柱状趋势图：正值为主题色、负值为红色，未录入的日期显示为灰点；鼠标悬停显示数值。"""

    point_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(240)
        self._points: list[tuple[str, float | None, str]] = []
        self._hover = -1
        self._empty_text = "暂无数据"

    def set_points(self, points: list[tuple[str, float | None, str]], empty_text: str = "暂无数据") -> None:
        """points: [(横轴短标签, 数值或 None, 悬停标题)]"""
        self._points = points
        self._empty_text = empty_text
        self._hover = -1
        self.update()

    # ── 坐标计算 ────────────────────────────

    def _plot_rect(self) -> QRectF:
        return QRectF(58, 14, max(10, self.width() - 58 - 14), max(10, self.height() - 14 - 34))

    def _scale(self) -> tuple[float, float, float]:
        values = [v for _, v, _ in self._points if v is not None]
        low = min(values + [0.0])
        high = max(values + [0.0])
        if math.isclose(low, high):
            high = low + 100
        raw_step = (high - low) / 4
        magnitude = 10 ** math.floor(math.log10(raw_step))
        step = magnitude * next(m for m in (1, 2, 2.5, 5, 10) if raw_step / magnitude <= m)
        return math.floor(low / step) * step, math.ceil(high / step) * step, step

    def _slot(self, plot: QRectF) -> float:
        return plot.width() / max(1, len(self._points))

    def _index_at(self, x: float) -> int:
        plot = self._plot_rect()
        if not self._points or not (plot.left() <= x <= plot.right()):
            return -1
        return min(len(self._points) - 1, int((x - plot.left()) / self._slot(plot)))

    # ── 事件 ────────────────────────────────

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt API
        index = self._index_at(event.position().x())
        if index != self._hover:
            self._hover = index
            self.setCursor(Qt.CursorShape.PointingHandCursor if index >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, event):  # noqa: N802 - Qt API
        self._hover = -1
        self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt API
        index = self._index_at(event.position().x())
        if index >= 0 and event.button() == Qt.MouseButton.LeftButton:
            self.point_clicked.emit(index)

    # ── 绘制 ────────────────────────────────

    def paintEvent(self, event):  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        small = QFont(self.font())
        small.setPixelSize(11)
        painter.setFont(small)
        metrics = QFontMetrics(small)

        if not any(v is not None for _, v, _ in self._points):
            painter.setPen(QColor(theme.TEXT_3))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._empty_text)
            return

        plot = self._plot_rect()
        low, high, step = self._scale()

        def y_of(value: float) -> float:
            return plot.bottom() - (value - low) / (high - low) * plot.height()

        # 网格与纵轴刻度
        tick = low
        while tick <= high + step / 2:
            y = y_of(tick)
            painter.setPen(QPen(QColor(theme.BORDER_STRONG if abs(tick) < 1e-9 else "#EDEFF4"), 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(theme.TEXT_3))
            painter.drawText(QRectF(0, y - 8, plot.left() - 8, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, theme.fmt_compact(tick))
            tick += step

        slot = self._slot(plot)
        bar_width = max(3.0, min(28.0, slot * 0.62))
        zero_y = y_of(0.0)
        label_every = max(1, math.ceil(metrics.horizontalAdvance("00-00") * 1.6 / slot))

        for index, (short, value, _) in enumerate(self._points):
            center = plot.left() + slot * index + slot / 2
            hovered = index == self._hover
            if hovered:
                painter.fillRect(QRectF(center - slot / 2, plot.top(), slot, plot.height()), QColor(theme.ACCENT_SOFT))
            if value is None:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#CDD1DC"))
                painter.drawEllipse(QPointF(center, zero_y), 2.2, 2.2)
            else:
                top = y_of(max(value, 0.0))
                bottom = y_of(min(value, 0.0))
                color = QColor(theme.DANGER if value < 0 else theme.ACCENT)
                if not hovered and self._hover >= 0:
                    color.setAlpha(150)
                path = QPainterPath()
                rect = QRectF(center - bar_width / 2, top, bar_width, max(1.5, bottom - top))
                radius = min(4.0, bar_width / 2)
                path.addRoundedRect(rect, radius, radius)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawPath(path)
            if index % label_every == 0 or index == len(self._points) - 1:
                if index == len(self._points) - 1 and index % label_every != 0 and label_every > 1:
                    continue
                painter.setPen(QColor(theme.TEXT_3))
                painter.drawText(QRectF(center - 40, plot.bottom() + 8, 80, 18), Qt.AlignmentFlag.AlignCenter, short)

        if 0 <= self._hover < len(self._points):
            self._draw_tooltip(painter, plot, slot)

    def _draw_tooltip(self, painter: QPainter, plot: QRectF, slot: float) -> None:
        _, value, title = self._points[self._hover]
        text_value = "未录入" if value is None else theme.fmt_money(value)
        title_font = QFont(self.font())
        title_font.setPixelSize(12)
        value_font = QFont(self.font())
        value_font.setPixelSize(14)
        value_font.setBold(True)
        width = max(QFontMetrics(title_font).horizontalAdvance(title),
                    QFontMetrics(value_font).horizontalAdvance(text_value)) + 24
        height = 50
        center = plot.left() + slot * self._hover + slot / 2
        x = min(max(plot.left(), center - width / 2), plot.right() - width)
        box = QRectF(x, plot.top() + 4, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(29, 33, 48, 235))
        painter.drawRoundedRect(box, 8, 8)
        painter.setFont(title_font)
        painter.setPen(QColor("#C9CCD6"))
        painter.drawText(box.adjusted(12, 7, -12, -26), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        painter.setFont(value_font)
        painter.setPen(QColor("#FFFFFF") if value is None or value >= 0 else QColor("#FF9EA2"))
        painter.drawText(box.adjusted(12, 24, -12, -6), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text_value)


# ─────────────────────────────────────────────
# 提示气泡
# ─────────────────────────────────────────────

class Toast(QLabel):
    """窗口底部居中的非阻塞提示，代替频繁弹出的“保存成功”对话框。"""

    def __init__(self, host: QWidget):
        super().__init__(host)
        self.setObjectName("Toast")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, tone: str = "info", duration_ms: int = 2600) -> None:
        self.setText(text)
        set_tone(self, tone)
        self.adjustSize()
        host = self.parentWidget()
        self.move((host.width() - self.width()) // 2, host.height() - self.height() - 36)
        self.raise_()
        self.show()
        self._timer.start(duration_ms)
