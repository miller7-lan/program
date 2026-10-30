package com.homeapp.profithelper

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.homeapp.profithelper.core.FieldConfig
import com.homeapp.profithelper.core.FormulaEngine
import com.homeapp.profithelper.data.Ledger
import com.homeapp.profithelper.sync.SyncConfig
import com.homeapp.profithelper.sync.SyncEngine
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 第三轮互通测试：tools/run_android_sync_interop.py 让桌面端改公式并选择“重算历史”、同步之后单独运行本测试，
 * 确认重算后的利润完整到达安卓端，且每一天的利润都与新公式一致、安卓端再算一次不会产生新的修改。
 */
@RunWith(AndroidJUnit4::class)
class DesktopRecomputeInteropTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val args = InstrumentationRegistry.getArguments()
    private val serverUrl = args.getString("serverUrl") ?: "http://10.0.2.2:18766"
    private val token = args.getString("syncToken") ?: error("缺少 syncToken 参数")

    @Test
    fun desktopRecomputedProfitsReachAndroid() {
        context.deleteDatabase("interop-desktop-recompute.db")
        val ledger = Ledger(context, "interop-desktop-recompute.db")
        val engine = SyncEngine(ledger, "test") { "安卓测试-桌面重算" }
        engine.saveConfig(SyncConfig(serverUrl, token))
        engine.sync()

        assertEquals("现金 + 微信收款 - 进货款", ledger.profitFormula())
        assertEquals(1050.25, ledger.getRecord("2026-09-01")!!.totalProfit, 0.0)
        val tree = ledger.fieldTree()
        val records = ledger.getAll()
        assertEquals(4, records.size)
        for (record in records) {
            val expected = FormulaEngine.evaluate(ledger.profitFormula(), FieldConfig.buildVariables(tree, record.rawData))
            assertEquals(record.date, expected, record.totalProfit, 1e-9)
        }
        assertEquals(0, ledger.recomputeProfits())
        assertEquals(0, ledger.countPendingChanges())
        context.deleteDatabase("interop-desktop-recompute.db")
    }
}
