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

class ProfitApp : Application() {
    lateinit var ledger: Ledger
        private set
    lateinit var sync: SyncManager
        private set

    val appScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onCreate() {
        super.onCreate()
        ledger = Ledger(this)
        val engine = SyncEngine(ledger, BuildConfig.VERSION_NAME) { deviceName() }
        sync = SyncManager(ledger, engine, appScope)
        ledger.addLocalChangeListener { sync.onLocalChange() }
        applyPresetIfNeeded()
    }

    /**
     * 套用打包时内置的服务器配置（与桌面端 core/sync_preset.py 规则相同）：
     *  - 尚未设置同步 → 套用
     *  - 当前连接来自内置配置、而新版安装包里的内置配置不同（例如轮换了密钥）→ 换用新配置
     *  - 用户手动填写过、或主动断开过 → 不动
     */
    private fun applyPresetIfNeeded() {
        val code = BuildConfig.PRESET_CONNECTION_CODE
        if (code.isBlank() || ledger.getMeta("preset_disabled") == "1") return
        val preset = runCatching { ConnectionCode.parse(code) }.getOrNull() ?: return
        val current = sync.engine.loadConfig()
        if (current.isComplete) {
            if (current == preset) return
            val source = ledger.getMeta("config_source")
            if (source == "manual" || (source.isEmpty() && current.serverUrl != preset.serverUrl)) return
        }
        sync.saveConfig(preset, "", fromPreset = true)
    }

    fun deviceName(): String = ledger.getMeta("device_name").ifBlank {
        listOf(Build.MANUFACTURER, Build.MODEL).filter { it.isNotBlank() }.distinct().joinToString(" ").ifBlank { "安卓手机" }
    }
}
