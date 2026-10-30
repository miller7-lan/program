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
 * 同步密钥被拒绝后暂停自动同步（只保留手动“立即同步”），避免旧密钥反复请求触发服务器的失败次数限制。
 *
 * 同步服务器内置在安装包里（BuildConfig.PRESET_CONNECTION_CODE），用户不需要填写。
 *
 * 同步调度：
 *  - 应用回到前台时同步一次，前台期间每分钟一次（及时拉取电脑上的修改）
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
    @Volatile private var authFailed = false
    @Volatile private var invalid: List<Pair<String, String>> = emptyList()
    private var debounceJob: Job? = null
    private var periodicJob: Job? = null

    private val _status = MutableStateFlow(computeStatus())
    val status: StateFlow<SyncStatus> = _status.asStateFlow()

    private val _messages = MutableSharedFlow<String>(extraBufferCapacity = 4)

    /** 需要提示给用户的同步结果（例如从云端拉取到了新数据）。 */
    val messages: SharedFlow<String> = _messages.asSharedFlow()

    val configured: Boolean get() = engine.isConfigured()

    /** 没有可用的同步配置（未内置，或密钥已失效），需要安装新版安装包后“重新检测”。 */
    val needsPreset: Boolean get() = !configured || authFailed

    var autoEnabled: Boolean
        get() = ledger.getMeta("auto_sync", "1") == "1"
        set(value) {
            ledger.setMetas(mapOf("auto_sync" to if (value) "1" else "0"))
            refresh()
        }

    fun onLocalChange() {
        refresh()
        if (!configured || !autoEnabled || authFailed) return
        debounceJob?.cancel()
        debounceJob = scope.launch {
            delay(2500)
            syncNow()
        }
    }

    fun onForeground() {
        refresh()
        if (!configured || !autoEnabled) return
        if (!authFailed) syncNow()
        periodicJob?.cancel()
        periodicJob = scope.launch {
            while (isActive) {
                delay(60 * 1000L)
                if (configured && autoEnabled && !authFailed) syncNow()  // 中途关闭了自动同步就不再定时同步
            }
        }
    }

    fun onBackground() {
        periodicJob?.cancel()
        periodicJob = null
        if (configured && autoEnabled && !authFailed && (debounceJob?.isActive == true || ledger.countPendingChanges() > 0)) {
            debounceJob?.cancel()
            syncNow()
        }
    }

    fun saveConfig(config: SyncConfig, deviceName: String, fromPreset: Boolean = false) {
        engine.saveConfig(config)
        ledger.setMetas(mapOf("preset_disabled" to "0", "config_source" to if (fromPreset) "preset" else "manual"))
        authFailed = false
        if (deviceName.isNotBlank()) ledger.setMetas(mapOf("device_name" to deviceName.trim().take(40)))
        lastError = null
        refresh()
    }

    /** 设置页“重新检测”：重新套用安装包内置的配置后立即同步。 */
    fun retryWithPreset() {
        authFailed = false
        lastError = null
        refresh()
        syncNow()
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
                    authFailed = false
                    invalid = result.invalid.toList()
                    if (result.recordsChanged || result.settingsChanged) _messages.tryEmit("已从云端同步：${result.summary}")
                } catch (e: SyncAuthException) {
                    authFailed = true
                    lastError = "同步密钥已失效，已暂停自动同步。请安装最新版利润助手，再到设置里点“重新检测”"
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
        if (!engine.isConfigured()) return SyncStatus(SyncTone.OFF, "未开启云同步", "这个安装包没有内置同步配置")
        val last = describeTime(ledger.getMeta("last_sync_at").toLongOrNull() ?: 0L)
        if (busy) return SyncStatus(SyncTone.BUSY, "正在同步…", if (last.isEmpty()) "首次同步" else "上次 $last")
        lastError?.let { return SyncStatus(SyncTone.ERROR, if (authFailed) "同步密钥不正确" else "同步失败", it) }
        invalid.firstOrNull()?.let { (date, reason) ->
            val more = if (invalid.size > 1) " 等 ${invalid.size} 条" else ""
            return SyncStatus(SyncTone.ERROR, "部分记录无法同步", "$date$more：$reason")
        }
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
