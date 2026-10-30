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
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.FixMethodOrder
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.runners.MethodSorters

/**
 * 安卓同步引擎 ↔ 真实同步服务端（本机测试实例）的端到端测试。
 * 由 tools/run_android_sync_interop.py 启动服务、用桌面端引擎写入种子数据，再在模拟器上运行本测试，
 * 最后用桌面端引擎核对安卓端写回的结果。
 */
@RunWith(AndroidJUnit4::class)
@FixMethodOrder(MethodSorters.NAME_ASCENDING)
class SyncInteropTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val args = InstrumentationRegistry.getArguments()
    private val serverUrl = args.getString("serverUrl") ?: "http://10.0.2.2:18766"
    private val token = args.getString("syncToken") ?: error("缺少 syncToken 参数")

    private fun device(name: String): Pair<Ledger, SyncEngine> {
        context.deleteDatabase("interop-$name.db")
        val ledger = Ledger(context, "interop-$name.db")
        val engine = SyncEngine(ledger, "test") { "安卓测试-$name" }
        engine.saveConfig(SyncConfig(serverUrl, token))
        return ledger to engine
    }

    private fun raw(cash: Double, wechat: Double, cost: Double, change: Double) = linkedMapOf<String, Any>(
        "营业额" to linkedMapOf("现金" to cash, "微信" to wechat),
        "成本" to linkedMapOf("进货" to cost),
        "零钱" to change,
    )

    @Test
    fun step1_pullsDesktopSeedExactly() {
        val (ledger, engine) = device("pull")
        val result = engine.sync()
        assertTrue("应拉到桌面端写入的数据", result.recordsChanged && result.settingsChanged)

        val tree = ledger.fieldTree()
        assertEquals(
            listOf(FieldNode("营业额", listOf("现金", "微信")), FieldNode("成本", listOf("进货")), FieldNode("零钱", emptyList())),
            tree,
        )
        assertEquals("现金 + 微信 - 进货 + 零钱", ledger.profitFormula())

        val first = ledger.getRecord("2026-09-01")!!
        assertEquals(1070.25, first.totalProfit, 0.0)
        assertEquals(raw(1200.5, 300.0, 450.25, 20.0), first.rawData)
        assertEquals("桌面端录入，含中文与符号：¥/%", first.note)
        // 安卓公式引擎用拉下来的数据重算，结果与桌面端保存的一致
        assertEquals(first.totalProfit, FormulaEngine.evaluate(ledger.profitFormula(), FieldConfig.buildVariables(tree, first.rawData)), 0.0)

        assertEquals(-800.0, ledger.getRecord("2026-09-02")!!.totalProfit, 0.0)
        assertNull("桌面端删除的日期不应出现", ledger.getRecord("2026-09-03"))
        assertEquals(0, ledger.countPendingChanges())
    }

    @Test
    fun step2_editsConflictsAndBadDataBehaveLikeDesktop() {
        val (a, syncA) = device("a")
        val (b, syncB) = device("b")
        syncA.sync()
        syncB.sync()

        // 安卓修改桌面端的记录，另一台设备能收到
        a.upsertRecord("2026-09-02", -700.0, raw(200.0, 0.0, 900.0, 0.0), "安卓修改")
        syncA.sync()
        syncB.sync()
        assertEquals("安卓修改", b.getRecord("2026-09-02")!!.note)

        // 先删后改：后修改的赢
        a.upsertRecord("2026-09-05", 10.0, raw(10.0, 0.0, 0.0, 0.0), "原始")
        syncA.sync()
        syncB.sync()
        a.deleteRecords(listOf("2026-09-05"))
        Thread.sleep(20)
        b.upsertRecord("2026-09-05", 50.0, raw(50.0, 0.0, 0.0, 0.0), "B 后改")
        syncA.sync(); syncB.sync(); syncA.sync()
        assertEquals("B 后改", a.getRecord("2026-09-05")!!.note)
        assertEquals("B 后改", b.getRecord("2026-09-05")!!.note)

        // 先改后删：后删除的赢
        a.upsertRecord("2026-09-06", 10.0, raw(10.0, 0.0, 0.0, 0.0), "")
        syncA.sync(); syncB.sync()
        a.upsertRecord("2026-09-06", 11.0, raw(11.0, 0.0, 0.0, 0.0), "A 先改")
        Thread.sleep(20)
        b.deleteRecords(listOf("2026-09-06"))
        syncA.sync(); syncB.sync(); syncA.sync()
        assertNull(a.getRecord("2026-09-06"))
        assertNull(b.getRecord("2026-09-06"))

        // 日期格式不对的坏记录被跳过并提示，不影响其他记录同步
        a.upsertRecord("2026/09/07", 1.0, raw(1.0, 0.0, 0.0, 0.0), "")
        a.upsertRecord("2026-09-08", 8.0, raw(8.0, 0.0, 0.0, 0.0), "正常")
        val result = syncA.sync()
        assertEquals(listOf("2026/09/07"), result.invalid.map { it.first })
        syncB.sync()
        assertEquals("正常", b.getRecord("2026-09-08")!!.note)
    }

    @Test
    fun step3_renamesAndFormulaFormatReachOtherDevices() {
        val (a, syncA) = device("rename")
        syncA.sync()
        // 改名：二级“微信”→“微信收款”，一级“零钱”→“备用金”；公式用全角写法，保存前整理格式（与桌面端相同）
        val tree = listOf(FieldNode("营业额", listOf("现金", "微信收款")), FieldNode("成本", listOf("进货")), FieldNode("备用金", emptyList()))
        val formula = FormulaEngine.format("现金＋微信收款－进货+备用金")
        assertEquals("现金 + 微信收款 - 进货 + 备用金", formula)
        val migrated = a.saveSettings(tree, formula, mapOf("零钱" to "备用金"), mapOf("营业额" to mapOf("微信" to "微信收款")))
        assertEquals("4 天有效记录都改写了名称", 4, migrated)
        val renamed = linkedMapOf<String, Any>(
            "营业额" to linkedMapOf("现金" to 1200.5, "微信收款" to 300.0),
            "成本" to linkedMapOf("进货" to 450.25),
            "备用金" to 20.0,
        )
        assertEquals(renamed, a.getRecord("2026-09-01")!!.rawData)
        syncA.sync()
        assertEquals(0, a.countPendingChanges())

        // 新装的设备拉到改名后的分类、公式和历史记录；按新公式重算的利润与保存的一致
        val (b, syncB) = device("rename-b")
        syncB.sync()
        assertEquals(tree, b.fieldTree())
        assertEquals(formula, b.profitFormula())
        val pulled = b.getRecord("2026-09-01")!!
        assertEquals(renamed, pulled.rawData)
        assertEquals(pulled.totalProfit, FormulaEngine.evaluate(b.profitFormula(), FieldConfig.buildVariables(b.fieldTree(), pulled.rawData)), 0.0)

        // 录入保存：删掉的分类里的旧数据保留，清空备注能真正清空
        val previous = b.getRecord("2026-09-05")!!
        val collected = linkedMapOf<String, Any>("营业额" to linkedMapOf("现金" to 60.0, "微信收款" to 0.0), "成本" to linkedMapOf("进货" to 0.0), "备用金" to 0.0)
        val merged = FieldConfig.mergeEntryRaw(previous.rawData + ("旧分类" to 3.0), collected, tree)
        b.upsertRecord("2026-09-05", 60.0, merged, "", keepNoteIfEmpty = false)
        syncB.sync()
        assertEquals("", b.getRecord("2026-09-05")!!.note)
        assertEquals(3.0, b.getRecord("2026-09-05")!!.rawData["旧分类"])
    }
}
