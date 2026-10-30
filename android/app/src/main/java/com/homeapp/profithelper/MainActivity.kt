package com.homeapp.profithelper

import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.homeapp.profithelper.sync.ConnectionCode
import com.homeapp.profithelper.ui.AppRoot
import com.homeapp.profithelper.ui.theme.ProfitTheme

class MainActivity : ComponentActivity() {

    private val app get() = application as ProfitApp

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.light(Color.TRANSPARENT, Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.light(Color.WHITE, Color.WHITE),
        )
        super.onCreate(savedInstanceState)
        handleDebugIntent(intent)
        setContent { ProfitTheme { AppRoot(app) } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleDebugIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        app.onResumed()
        app.sync.onForeground()
    }

    override fun onPause() {
        app.sync.onBackground()
        super.onPause()
    }

    /** 仅调试版：adb shell am start ... --es connection_code PH1-… 直接写入同步设置（用于模拟器预览）。 */
    private fun handleDebugIntent(intent: Intent?) {
        if (!BuildConfig.DEBUG) return
        val code = intent?.getStringExtra("connection_code") ?: return
        runCatching {
            app.sync.saveConfig(ConnectionCode.parse(code), intent.getStringExtra("device_name") ?: "")
            app.sync.syncNow()
        }
    }
}
