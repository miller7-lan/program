"""
ui/sync_manager.py
云同步调度：在后台线程执行同步，避免网络请求卡住界面。

触发时机：
  - 启动后自动同步一次
  - 本机保存 / 删除 / 修改设置后 2.5 秒（合并连续修改）
  - 每 5 分钟一次
  - 用户点击“立即同步”
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QTimer, Signal

from core.event_bus import event_bus
from core.sync_client import SyncConfig, SyncEngine, SyncError, describe_time
from core.sync_preset import apply_preset_if_needed


class SyncManager(QObject):
    status_changed = Signal()
    _worker_done = Signal(object, object)

    DEBOUNCE_MS = 2500
    PERIODIC_MS = 5 * 60 * 1000

    def __init__(self, db, app_version: str, parent=None):
        super().__init__(parent)
        self._db = db
        self.engine = SyncEngine(db, app_version=app_version, platform_name="windows")
        self.state = "off"       # off / idle / busy / ok / error
        self.message = ""
        self._thread: threading.Thread | None = None
        self._pending = False

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self.sync_now)
        self._periodic = QTimer(self)
        self._periodic.setInterval(self.PERIODIC_MS)
        self._periodic.timeout.connect(self.sync_now)

        self._worker_done.connect(self._on_worker_done)
        db.add_local_change_listener(self._on_local_change)
        self.preset_applied = apply_preset_if_needed(self.engine)
        self._refresh_idle_state()

    # ── 配置 ────────────────────────────────

    @property
    def configured(self) -> bool:
        return self.engine.is_configured()

    @property
    def auto_enabled(self) -> bool:
        return self._db.get_sync_meta("auto_sync", "1") == "1"

    def set_auto_enabled(self, enabled: bool) -> None:
        self._db.set_sync_meta("auto_sync", "1" if enabled else "0")
        self._apply_timers()

    def save_config(self, config: SyncConfig, device_name: str = "") -> None:
        self.engine.save_config(config)
        self._db.set_sync_meta("preset_disabled", "0")
        if device_name.strip():
            self._db.set_sync_meta("device_name", device_name.strip()[:40])
        self._refresh_idle_state()
        self._apply_timers()

    def disconnect(self) -> None:
        self._db.forget_sync_server()
        self._db.set_sync_meta("preset_disabled", "1")  # 主动断开后不再自动套用内置配置
        self._refresh_idle_state()
        self._apply_timers()

    def start(self) -> None:
        self._apply_timers()
        if self.configured and self.auto_enabled:
            QTimer.singleShot(1200, self.sync_now)
            if self.preset_applied:
                event_bus.toast_requested.emit("已自动连接云同步服务器，正在同步…", "info")

    def _apply_timers(self) -> None:
        if self.configured and self.auto_enabled:
            self._periodic.start()
        else:
            self._periodic.stop()
            self._debounce.stop()

    # ── 触发 ────────────────────────────────

    def _on_local_change(self) -> None:
        if self.configured and self.auto_enabled:
            self._debounce.start(self.DEBOUNCE_MS)
        self.status_changed.emit()

    def sync_now(self) -> None:
        if not self.configured:
            self._refresh_idle_state()
            return
        if self._thread is not None and self._thread.is_alive():
            self._pending = True
            return
        self._debounce.stop()
        self.state = "busy"
        self.message = "正在同步…"
        self.status_changed.emit()
        self._thread = threading.Thread(target=self._run, name="sync", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self._worker_done.emit(self.engine.sync(), None)
        except SyncError as exc:
            self._worker_done.emit(None, exc)
        except Exception as exc:  # 意外错误也不能让同步线程静默退出
            self._worker_done.emit(None, SyncError(f"同步出错：{exc}"))

    def _on_worker_done(self, result, error) -> None:
        if error is not None:
            self.state = "error"
            self.message = str(error)
        else:
            self.state = "ok"
            self.message = result.summary
            if result.records_changed:
                event_bus.data_changed.emit()
            if result.settings_changed:
                event_bus.config_changed.emit()
            if result.records_changed or result.settings_changed:
                event_bus.toast_requested.emit(f"已从云端同步：{result.summary}", "info")
        self.status_changed.emit()
        if self._pending:
            self._pending = False
            QTimer.singleShot(300, self.sync_now)

    def shutdown(self, timeout: float = 6.0) -> None:
        """退出前：若有尚未上传的修改，尽量在几秒内完成一次同步。"""
        self._periodic.stop()
        had_pending = self._debounce.isActive() or (self.configured and self._db.count_pending_changes() > 0)
        self._debounce.stop()
        if self.configured and self.auto_enabled and had_pending and not (self._thread and self._thread.is_alive()):
            self._thread = threading.Thread(target=self._run_quietly, name="sync-exit", daemon=True)
            self._thread.start()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run_quietly(self) -> None:
        try:
            self.engine.sync()
        except Exception:
            pass

    # ── 状态展示 ────────────────────────────

    def _refresh_idle_state(self) -> None:
        if not self.configured:
            self.state, self.message = "off", "未设置云同步"
        elif self.state in ("off", "idle"):
            error = self._db.get_sync_meta("last_error")
            self.state = "idle"
            self.message = error or self._db.get_sync_meta("last_result")
        self.status_changed.emit()

    def status_view(self) -> tuple[str, str, str]:
        """返回 (标题, 说明, 语气)，供侧边栏和设置页展示。"""
        pending = self._db.count_pending_changes() if self.configured else 0
        last = describe_time(self._db.get_sync_meta("last_sync_at"))
        if self.state == "off":
            return "未开启云同步", "到设置里粘贴连接码即可", "off"
        if self.state == "busy":
            return "正在同步…", f"上次 {last}" if last else "首次同步", "busy"
        if self.state == "error":
            return "同步失败", self.message, "error"
        detail = f"上次 {last}" if last else "尚未同步"
        if pending:
            detail += f" · {pending} 项待上传"
        return ("已同步" if not pending else "等待同步"), detail, ("ok" if not pending else "busy")
