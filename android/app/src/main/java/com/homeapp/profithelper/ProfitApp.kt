package com.homeapp.profithelper

import android.app.Application
import android.os.Build
import com.homeapp.profithelper.data.Ledger
import com.homeapp.profithelper.sync.ConnectionCode
import com.homeapp.profithelper.sync.SyncEngine
import com.homeapp.profithelper.sync.SyncManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

class ProfitApp : Application() {
    lateinit var ledger: Ledger
        private set
    lateinit var sync: SyncManager
        private set

    val appScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val _resumeTick = MutableStateFlow(0L)

    /** 每次回到前台加一，界面据此检查日期是否已经换了一天。 */
    val resumeTick: StateFlow<Long> = _resumeTick.asStateFlow()

    fun onResumed() {
        _resumeTick.value = _resumeTick.value + 1
    }

    override fun onCreate() {
        super.onCreate()
        ledger = Ledger(this)
        val engine = SyncEngine(ledger, BuildConfig.VERSION_NAME) { deviceName() }
        sync = SyncManager(ledger, engine, appScope)
        ledger.addLocalChangeListener { sync.onLocalChange() }
        applyPreset()
    }

    /** 安装包里是否带有有效的同步配置（正式版打包时写入）。 */
    val hasPreset: Boolean
        get() = runCatching { ConnectionCode.parse(BuildConfig.PRESET_CONNECTION_CODE) }.isSuccess

    /**
     * 套用打包时内置的服务器配置（与桌面端 core/sync_preset.py 规则相同），返回是否换用了内置配置：
     *  - 尚未设置同步（包括旧版本里点过“断开”）→ 套用；新版本已没有断开和手动填写的入口
     *  - 当前连接来自内置配置、而新版安装包里的内置配置不同（例如轮换了密钥）→ 换用新配置
     *  - 旧版本里手动填写的连接 → 不动（force = true 时也换用：用户在设置里点了“重新检测”）
     */
    fun applyPreset(force: Boolean = false): Boolean {
        val code = BuildConfig.PRESET_CONNECTION_CODE
        if (code.isBlank()) return false
        val preset = runCatching { ConnectionCode.parse(code) }.getOrNull() ?: return false
        val current = sync.engine.loadConfig()
        if (current.isComplete) {
            if (current == preset) return false
            val source = ledger.getMeta("config_source")
            if (!force && (source == "manual" || (source.isEmpty() && current.serverUrl != preset.serverUrl))) return false
        }
        sync.saveConfig(preset, "", fromPreset = true)
        return true
    }

    fun deviceName(): String = ledger.getMeta("device_name").ifBlank {
        listOf(Build.MANUFACTURER, Build.MODEL).filter { it.isNotBlank() }.distinct().joinToString(" ").ifBlank { "安卓手机" }
    }
}
