package com.homeapp.profithelper

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.homeapp.profithelper.core.FieldConfig
import com.homeapp.profithelper.core.FieldNode
import com.homeapp.profithelper.core.FormulaEngine
import com.homeapp.profithelper.data.Ledger
import com.homeapp.profithelper.sync.SyncConfig
import com.homeapp.profithelper.sync.SyncEngine
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 第二轮互通测试：tools/run_android_sync_interop.py 在 SyncInteropTest 之后用桌面端改名并同步，
 * 再单独运行本测试，确认桌面端的改名、公式和改写后的历史记录完整到达安卓端。
 */
@RunWith(AndroidJUnit4::class)
class DesktopRenameInteropTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val args = InstrumentationRegistry.getArguments()
    private val serverUrl = args.getString("serverUrl") ?: "http://10.0.2.2:18766"
    private val token = args.getString("syncToken") ?: error("缺少 syncToken 参数")

    @Test
    fun desktopRenameReachesAndroid() {
        context.deleteDatabase("interop-desktop-rename.db")
        val ledger = Ledger(context, "interop-desktop-rename.db")
        val engine = SyncEngine(ledger, "test") { "安卓测试-桌面改名" }
        engine.saveConfig(SyncConfig(serverUrl, token))
        engine.sync()

        val tree = listOf(FieldNode("营业额", listOf("现金", "微信收款")), FieldNode("支出", listOf("进货款")), FieldNode("备用金", emptyList()))
        assertEquals(tree, ledger.fieldTree())
        assertEquals("现金 + 微信收款 - 进货款 + 备用金", ledger.profitFormula())
        val first = ledger.getRecord("2026-09-01")!!
        assertEquals(
            linkedMapOf<String, Any>(
                "营业额" to linkedMapOf("现金" to 1200.5, "微信收款" to 300.0),
                "支出" to linkedMapOf("进货款" to 450.25),
                "备用金" to 20.0,
            ),
            first.rawData,
        )
        assertEquals(1070.25, FormulaEngine.evaluate(ledger.profitFormula(), FieldConfig.buildVariables(tree, first.rawData)), 0.0)
        assertEquals(3.0, ledger.getRecord("2026-09-05")!!.rawData["旧分类"])
        assertEquals(0, ledger.countPendingChanges())
    }
}
