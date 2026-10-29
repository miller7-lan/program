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

    /** 尚未设置同步、且用户没有主动断开时，套用打包时内置的服务器配置。 */
    private fun applyPresetIfNeeded() {
        val preset = BuildConfig.PRESET_CONNECTION_CODE
        if (preset.isBlank() || sync.configured || ledger.getMeta("preset_disabled") == "1") return
        runCatching { sync.saveConfig(ConnectionCode.parse(preset), "") }
    }

    fun deviceName(): String = ledger.getMeta("device_name").ifBlank {
        listOf(Build.MANUFACTURER, Build.MODEL).filter { it.isNotBlank() }.distinct().joinToString(" ").ifBlank { "安卓手机" }
    }
}
