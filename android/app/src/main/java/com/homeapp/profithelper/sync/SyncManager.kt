package com.homeapp.profithelper.sync

import com.homeapp.profithelper.data.Ledger
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

enum class SyncTone { OFF, OK, BUSY, ERROR }

data class SyncStatus(val tone: SyncTone, val title: String, val detail: String)

/**
 * 同步调度：
 *  - 应用回到前台时同步一次，前台期间每 5 分钟一次
 *  - 本机保存 / 删除 / 修改设置后 2.5 秒（合并连续修改）
 *  - 离开应用时若还有未上传的修改，立即同步
 *  - 用户手动“立即同步”
 */
class SyncManager(
    private val ledger: Ledger,
    val engine: SyncEngine,
    private val scope: CoroutineScope,
) {
    private val mutex = Mutex()
    @Volatile private var pending = false
    @Volatile private var busy = false
    @Volatile private var lastError: String? = null
    private var debounceJob: Job? = null
    private var periodicJob: Job? = null

    private val _status = MutableStateFlow(computeStatus())
    val status: StateFlow<SyncStatus> = _status.asStateFlow()

    private val _messages = MutableSharedFlow<String>(extraBufferCapacity = 4)

    /** 需要提示给用户的同步结果（例如从云端拉取到了新数据）。 */
    val messages: SharedFlow<String> = _messages.asSharedFlow()

    val configured: Boolean get() = engine.isConfigured()

    var autoEnabled: Boolean
        get() = ledger.getMeta("auto_sync", "1") == "1"
        set(value) {
            ledger.setMetas(mapOf("auto_sync" to if (value) "1" else "0"))
            refresh()
        }

    fun onLocalChange() {
        refresh()
        if (!configured || !autoEnabled) return
        debounceJob?.cancel()
        debounceJob = scope.launch {
            delay(2500)
            syncNow()
        }
    }

    fun onForeground() {
        refresh()
        if (!configured || !autoEnabled) return
        syncNow()
        periodicJob?.cancel()
        periodicJob = scope.launch {
            while (isActive) {
                delay(5 * 60 * 1000L)
                syncNow()
            }
        }
    }

    fun onBackground() {
        periodicJob?.cancel()
        periodicJob = null
        if (configured && autoEnabled && (debounceJob?.isActive == true || ledger.countPendingChanges() > 0)) {
            debounceJob?.cancel()
            syncNow()
        }
    }

    fun saveConfig(config: SyncConfig, deviceName: String) {
        engine.saveConfig(config)
        ledger.setMetas(mapOf("preset_disabled" to "0"))
        if (deviceName.isNotBlank()) ledger.setMetas(mapOf("device_name" to deviceName.trim().take(40)))
        lastError = null
        refresh()
    }

    fun disconnect() {
        ledger.forgetServer()
        ledger.setMetas(mapOf("preset_disabled" to "1"))
        lastError = null
        refresh()
    }

    fun syncNow() {
        if (!configured) {
            refresh()
            return
        }
        if (busy) {
            pending = true
            return
        }
        scope.launch(Dispatchers.IO) {
            mutex.withLock {
                busy = true
                refresh()
                try {
                    val result = engine.sync()
                    lastError = null
                    if (result.recordsChanged || result.settingsChanged) _messages.tryEmit("已从云端同步：${result.summary}")
                } catch (e: SyncException) {
                    lastError = e.message
                } catch (e: Exception) {
                    lastError = "同步出错：${e.message}"
                } finally {
                    busy = false
                    refresh()
                }
            }
            if (pending) {
                pending = false
                syncNow()
            }
        }
    }

    fun refresh() {
        _status.value = computeStatus()
    }

    private fun computeStatus(): SyncStatus {
        if (!engine.isConfigured()) return SyncStatus(SyncTone.OFF, "未开启云同步", "到设置里粘贴连接码即可")
        val last = describeTime(ledger.getMeta("last_sync_at").toLongOrNull() ?: 0L)
        if (busy) return SyncStatus(SyncTone.BUSY, "正在同步…", if (last.isEmpty()) "首次同步" else "上次 $last")
        lastError?.let { return SyncStatus(SyncTone.ERROR, "同步失败", it) }
        val pendingCount = ledger.countPendingChanges()
        var detail = if (last.isEmpty()) "尚未同步" else "上次 $last"
        if (pendingCount > 0) detail += " · $pendingCount 项待上传"
        return if (pendingCount > 0) SyncStatus(SyncTone.BUSY, "等待同步", detail) else SyncStatus(SyncTone.OK, "已同步", detail)
    }

    private fun describeTime(ms: Long): String {
        if (ms <= 0) return ""
        val moment = Calendar.getInstance().apply { timeInMillis = ms }
        val today = Calendar.getInstance()
        val sameDay = moment.get(Calendar.YEAR) == today.get(Calendar.YEAR) &&
            moment.get(Calendar.DAY_OF_YEAR) == today.get(Calendar.DAY_OF_YEAR)
        val pattern = if (sameDay) "今天 HH:mm" else "MM-dd HH:mm"
        return SimpleDateFormat(pattern, Locale.CHINA).format(Date(ms))
    }
}
