"""
ui/dashboard_view.py
看板页 — 展示当日利润快照、月度利润折线图。

骨架说明：
  - 顶部：3个"看板卡片"（今日利润 / 本月累计 / 今年总计）
  - 底部：matplotlib 折线图，嵌入 FigureCanvasQTAgg
  - 监听 event_bus.data_changed → 自动触发 refresh()
"""

from PySide6.QtCore import Qt
import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.event_bus import event_bus


class DashboardView(QWidget):
    """看板视图：数据快照 + 利润趋势折线图。"""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._build_ui()

        # 监听数据变化 → 自动刷新
        event_bus.data_changed.connect(self.refresh)

    # ─────────────────────────────────────
    # UI 构建
    # ─────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(24)

        # 页面标题
        title = QLabel("📊 数据看板")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        # ── 顶部卡片行 ───────────────────────
        cards_row = QHBoxLayout()
        cards_row.setSpacing(16)
        self._card_today = self._make_card("今日利润", "—", "#9B6FE8")
        self._card_month = self._make_card("本月累计", "—", "#4ECDC4")
        self._card_year  = self._make_card("今年总计", "—", "#FF6B6B")
        for card in (self._card_today, self._card_month, self._card_year):
            cards_row.addWidget(card)
        layout.addLayout(cards_row)

        # ── 折线图区域（matplotlib） ───────────
        self._chart_container = QFrame()
        self._chart_container.setObjectName("ChartPlaceholder")
        self._chart_container.setMinimumHeight(300)
        self._chart_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        
        self._chart_layout = QVBoxLayout(self._chart_container)
        self._chart_layout.setContentsMargins(10, 10, 10, 10)
        
        # 初始化 Matplotlib 图表
        self._fig = Figure(figsize=(5, 4), dpi=100)
        self._canvas = FigureCanvas(self._fig)
        self._ax = self._fig.add_subplot(111)
        self._chart_layout.addWidget(self._canvas)
        
        layout.addWidget(self._chart_container)

    @staticmethod
    def _make_card(label: str, value: str, accent_color: str) -> QFrame:
        """创建一个数据卡片。"""
        card = QFrame()
        card.setObjectName("DashCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 16, 20, 16)

        lbl = QLabel(label)
        lbl.setObjectName("CardLabel")

        val = QLabel(value)
        val.setObjectName("CardValue")
        val.setProperty("accent", accent_color)

        card_layout.addWidget(lbl)
        card_layout.addWidget(val)
        return card

    # ─────────────────────────────────────
    # 数据刷新
    # ─────────────────────────────────────

    def refresh(self) -> None:
        """从 ReportEngine 获取最新统计摘要并更新卡片显示。"""
        from core.report_engine import ReportEngine
        engine = ReportEngine(self._db)
        data = engine.get_summary_data()

        self._update_card_value(self._card_today, data["today"])
        self._update_card_value(self._card_month, data["month"])
        self._update_card_value(self._card_year, data["year"])

        # 更新折线图
        self._update_chart()

    def _update_chart(self) -> None:
        """从 ReportEngine 获取最近 30 天数据并绘制折线图。"""
        from core.report_engine import ReportEngine
        import datetime
        
        engine = ReportEngine(self._db)
        
        # 获取最近 30 天数据
        end_date = datetime.date.today()
        start_date = end_date - datetime.timedelta(days=29)
        df = engine.generate_daily_df(start_date.strftime("%Y-%m-%d"), 
                                      end_date.strftime("%Y-%m-%d"))
        
        self._ax.clear()
        
        if not df.empty:
            # 绘图
            self._ax.plot(df['date'], df['total_profit'], 
                          marker='o', markersize=4, 
                          linewidth=2, color='#6C5CE7', label='每日利润')
            
            # 填色
            self._ax.fill_between(df['date'], df['total_profit'], color='#6C5CE7', alpha=0.1)
            
            # 设置样式
            self._ax.set_title("最近 30 天利润趋势", fontsize=11, pad=10, color='#1A1A2E')
            self._ax.grid(True, linestyle='--', alpha=0.5, axis='y')
            self._ax.spines['top'].set_visible(False)
            self._ax.spines['right'].set_visible(False)
            self._ax.tick_params(axis='both', which='major', labelsize=8)
            
            # 格式化日期显示
            self._fig.autofmt_xdate()
        else:
            self._ax.text(0.5, 0.5, "暂无充足数据生成趋势图", 
                          ha='center', va='center', transform=self._ax.transAxes,
                          color='#9999BB')

        self._fig.tight_layout()
        self._canvas.draw()

    def _update_card_value(self, card: QFrame, value: float) -> None:
        """辅助方法：更新指定卡片内的数值标签。"""
        # 查找卡片中的 CardValue 标签
        val_label = card.findChild(QLabel, "CardValue")
        if val_label:
            val_label.setText(f"{value:,.2f}")

    def showEvent(self, event) -> None:
        """每次切换到此页时自动刷新。"""
        self.refresh()
        super().showEvent(event)
