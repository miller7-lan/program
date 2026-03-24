"""
core/event_bus.py
全局事件总线 — 基于 PySide6 Signals & Slots 机制。

用法：
    from core.event_bus import event_bus

    # 发射信号（数据写入后调用）
    event_bus.data_changed.emit()

    # 监听信号（在视图的 __init__ 中连接）
    event_bus.data_changed.connect(self._refresh)

所有 UI 组件通过同一个全局 event_bus 实例通信，无需互相持有引用。
"""

from PySide6.QtCore import QObject, Signal


class EventBus(QObject):
    """全局信号总线（单例）。

    信号说明：
        data_changed   — 每日记录增/删/改 时发射，触发图表和看板刷新
        config_changed — 用户更新自定义字段或公式配置时发射
    """

    data_changed = Signal()    # 数据库记录变化
    config_changed = Signal()  # 配置项变化（字段列表 / 公式）


# ──────────────────────────────────────────
# 全局单例，整个应用共享同一个实例
# ──────────────────────────────────────────
event_bus = EventBus()
