"""
ui/dashboard_view.py
看板页 — 今日 / 本月 / 今年利润快照、利润趋势图、最近记录。

  - 顶部：4 张指标卡（今日利润 / 本月累计 / 今年累计 / 本月日均）
  - 中部：趋势柱状图（近 30 天 / 近 12 个月），点击柱子可直接打开当天记录
  - 右侧：最近记录列表，点击打开编辑
  - 监听 event_bus.data_changed → 自动刷新（包括云同步拉取到的数据）
"""

import datetime as dt

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QBoxLayout,
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.event_bus import event_bus
from core.field_config import build_formula_variables
from core.report_engine import WEEKDAYS, ReportEngine, load_field_tree, weekday_label
from ui import theme
from ui.widgets import Card, SegmentedControl, StatCard, TrendChart, label, make_button, page_header, page_scroll, set_tone


class RecentRow(QWidget):
    def __init__(self, record: dict, field_tree: list[dict]):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        day = dt.date.fromisoformat(record["date"])
        text_col.addWidget(label(f"{day.month}月{day.day}日  {weekday_label(record['date'])}", "RecentDate"))
        note = record.get("note", "").strip().replace("\n", " ")
        if not note:
            totals = build_formula_variables(field_tree, record["raw_data"])
            note = " · ".join(
                f"{item['name']} {totals.get(item['name'], 0):,.0f}"
                for item in field_tree[:2] if item["children"]
            )
        note_label = label(note, "RecentNote")
        note_label.setMaximumWidth(220)
        note_label.setText(note_label.fontMetrics().elidedText(note_label.text(), Qt.TextElideMode.ElideRight, 210))
        text_col.addWidget(note_label)
        layout.addLayout(text_col, 1)
        profit = label(theme.fmt_money(record["total_profit"]), "RecentProfit")
        set_tone(profit, "negative" if record["total_profit"] < 0 else "")
        layout.addWidget(profit)


class DashboardView(QWidget):
    """看板视图：数据快照 + 利润趋势图 + 最近记录。"""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._engine = ReportEngine(db)
        self._series_dates: list[str] = []
        self._build_ui()
        event_bus.data_changed.connect(self.refresh)

    # ─────────────────────────────────────
    # UI 构建
    # ─────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        outer.addWidget(page_scroll(content))

        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(18)

        header, self._subtitle, actions = page_header("数据看板")
        self._today_button = make_button("录入今天", "primary", "entry")
        self._today_button.clicked.connect(
            lambda: event_bus.edit_date_requested.emit(dt.date.today().isoformat())
        )
        actions.addWidget(self._today_button)
        layout.addLayout(header)

        self._cards_grid = QGridLayout()
        self._cards_grid.setHorizontalSpacing(14)
        self._cards_grid.setVerticalSpacing(14)
        self._card_today = StatCard("今日利润", "coins", theme.ACCENT)
        self._card_month = StatCard("本月累计", "calendar", "#0E9F8E")
        self._card_year = StatCard("今年累计", "trend", "#E8743B")
        self._card_avg = StatCard("本月日均", "wallet", "#3B82F6")
        self._cards = [self._card_today, self._card_month, self._card_year, self._card_avg]
        layout.addLayout(self._cards_grid)
        self._cards_columns = 0
        self._relayout_cards(4)

        self._lower = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._lower.setSpacing(14)

        chart_card = Card()
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(18, 16, 18, 14)
        chart_layout.setSpacing(10)
        chart_head = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(label("利润趋势", "SectionTitle"))
        self._chart_caption = label("", "SectionHint")
        title_col.addWidget(self._chart_caption)
        chart_head.addLayout(title_col)
        chart_head.addStretch()
        self._range_switch = SegmentedControl(["近 30 天", "近 12 个月"])
        self._range_switch.changed.connect(lambda _: self._update_chart())
        chart_head.addWidget(self._range_switch, alignment=Qt.AlignmentFlag.AlignTop)
        chart_layout.addLayout(chart_head)
        self._chart = TrendChart()
        self._chart.point_clicked.connect(self._on_chart_clicked)
        chart_layout.addWidget(self._chart, 1)
        self._lower.addWidget(chart_card, 3)

        recent_card = Card()
        recent_card.setMinimumWidth(270)
        recent_layout = QVBoxLayout(recent_card)
        recent_layout.setContentsMargins(14, 16, 14, 12)
        recent_layout.setSpacing(8)
        recent_head = QHBoxLayout()
        recent_head.setContentsMargins(4, 0, 4, 0)
        recent_head.addWidget(label("最近记录", "SectionTitle"))
        recent_head.addStretch()
        recent_head.addWidget(label("点击可编辑", "SectionHint"))
        recent_layout.addLayout(recent_head)
        self._recent = QListWidget()
        self._recent.setObjectName("RecentList")
        self._recent.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._recent.itemClicked.connect(self._on_recent_clicked)
        recent_layout.addWidget(self._recent, 1)
        self._recent_empty = label("还没有任何记录。\n点击右上角“录入今天”开始记账。", "SectionHint", wrap=True)
        self._recent_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        recent_layout.addWidget(self._recent_empty, 1)
        self._lower.addWidget(recent_card, 2)

        layout.addLayout(self._lower, 1)

    def _relayout_cards(self, columns: int) -> None:
        if columns == self._cards_columns:
            return
        self._cards_columns = columns
        for card in self._cards:
            self._cards_grid.removeWidget(card)
        for index, card in enumerate(self._cards):
            self._cards_grid.addWidget(card, index // columns, index % columns)
        for column in range(4):
            self._cards_grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        width = event.size().width()
        self._relayout_cards(4 if width >= 900 else 2)
        self._lower.setDirection(
            QBoxLayout.Direction.LeftToRight if width >= 1000 else QBoxLayout.Direction.TopToBottom
        )
        self._chart.setMinimumHeight(260 if width >= 1000 else 230)

    # ─────────────────────────────────────
    # 数据刷新
    # ─────────────────────────────────────

    def refresh(self) -> None:
        today = dt.date.today()
        self._subtitle.setText(f"{today.year}年{today.month}月{today.day}日  {WEEKDAYS[today.weekday()]}")
        data = self._engine.get_summary_data(today)

        if data["today_recorded"]:
            yesterday = data["yesterday"]
            if yesterday is None:
                sub, tone = "昨天没有记录", ""
            else:
                diff = data["today"] - yesterday
                sub = f"较昨天 {theme.fmt_money(diff, signed=True)}"
                tone = "up" if diff > 0 else ("down" if diff < 0 else "")
            self._card_today.set_value(data["today"], sub, tone)
            self._today_button.setText("修改今天")
        else:
            self._card_today.set_value(None, "今天还没有录入", "warn")
            self._today_button.setText("录入今天")

        self._card_month.set_value(data["month"], f"已记录 {data['month_days']} 天")
        self._card_year.set_value(data["year"], f"已记录 {data['year_days']} 天 · 日均 {theme.fmt_money(data['year_avg'])}")
        best = data["month_best"]
        best_text = ""
        if best:
            best_day = dt.date.fromisoformat(best[0])
            best_text = f"最高 {best_day.month}月{best_day.day}日 {theme.fmt_money(best[1])}"
        self._card_avg.set_value(data["month_avg"] if data["month_days"] else None, best_text or "本月暂无记录")

        self._update_chart()
        self._update_recent()

    def _update_chart(self) -> None:
        if self._range_switch.current() == 0:
            series = self._engine.daily_series(30)
            self._series_dates = [day.isoformat() for day, _ in series]
            points = [
                (f"{day.month}-{day.day:02d}", value, f"{day.month}月{day.day}日 {WEEKDAYS[day.weekday()]}")
                for day, value in series
            ]
            recorded = [v for _, v in series if v is not None]
            caption = f"近 30 天记录 {len(recorded)} 天，合计 {theme.fmt_money(sum(recorded))}"
            self._chart.set_points(points, "近 30 天暂无记录")
        else:
            series = self._engine.monthly_series(12)
            self._series_dates = []
            points = [
                (f"{month}月", total if days else None, f"{year}年{month}月 · {days} 天")
                for year, month, total, days in series
            ]
            total = sum(t for _, _, t, d in series if d)
            caption = f"近 12 个月合计 {theme.fmt_money(total)}"
            self._chart.set_points(points, "近 12 个月暂无记录")
        self._chart_caption.setText(caption)

    def _update_recent(self) -> None:
        self._recent.clear()
        records = self._db.get_recent_records(8)
        field_tree = load_field_tree(self._db)
        for record in records:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, record["date"])
            row = RecentRow(record, field_tree)
            item.setSizeHint(row.sizeHint())
            self._recent.addItem(item)
            self._recent.setItemWidget(item, row)
        self._recent.setVisible(bool(records))
        self._recent_empty.setVisible(not records)

    def _on_recent_clicked(self, item: QListWidgetItem) -> None:
        event_bus.edit_date_requested.emit(item.data(Qt.ItemDataRole.UserRole))

    def _on_chart_clicked(self, index: int) -> None:
        if 0 <= index < len(self._series_dates):
            event_bus.edit_date_requested.emit(self._series_dates[index])

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        """每次切换到此页时自动刷新。"""
        self.refresh()
        super().showEvent(event)
