package com.homeapp.profithelper

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.homeapp.profithelper.core.FieldNode
import com.homeapp.profithelper.data.Ledger
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 改公式 / 分类后已保存的历史利润如何处理（与桌面端 tests/test_profit_recompute.py 相同的用例）：
 * 保存前只读预览，用户选择“重算历史”才改写并同步；算不出来的日期保留原利润；重复执行不会重复改动。
 */
@RunWith(AndroidJUnit4::class)
class ProfitRecomputeTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val dbName = "profit-recompute-test.db"
    private lateinit var ledger: Ledger

    private val tree = listOf(FieldNode("营业额", listOf("现金", "微信")), FieldNode("成本", listOf("进货")), FieldNode("零钱", emptyList()))

    @Before
    fun setUp() {
        context.deleteDatabase(dbName)
        ledger = Ledger(context, dbName)
        ledger.saveSettings(tree, "营业额 - 成本")
        for ((date, cash, wechat, cost, change) in listOf(
            Day("2026-10-01", 100.0, 50.5, 30.0, 20.0),
            Day("2026-10-02", 90.0, 0.0, 80.0, 0.0),
            Day("2026-10-03", 10.1, 20.2, 0.3, 5.0),
        )) {
            val raw = linkedMapOf<String, Any>("营业额" to linkedMapOf("现金" to cash, "微信" to wechat), "成本" to linkedMapOf("进货" to cost), "零钱" to change)
            ledger.upsertRecord(date, cash + wechat - cost, raw, "")
        }
        for (record in ledger.collectDirtyRecords(10)) ledger.markRecordSynced(record.getString("date"), 1, record.getLong("modified_at"))
    }

    @After
    fun tearDown() {
        context.deleteDatabase(dbName)
    }

    private data class Day(val date: String, val cash: Double, val wechat: Double, val cost: Double, val change: Double)

    private fun profit(date: String) = ledger.getRecord(date)!!.totalProfit

    private fun dirtyDates() = ledger.collectDirtyRecords(10).map { it.getString("date") }.sorted()

    @Test
    fun sameResultWrittenDifferentlyChangesNothing() {
        val preview = ledger.previewProfitChanges(tree, "现金 + 微信 - 进货")
        assertEquals(listOf(3, 0, 0, 0), listOf(preview.days, preview.changed.size, preview.failed.size, preview.affected))
        assertEquals(0, ledger.recomputeProfits())
        assertEquals(emptyList<String>(), dirtyDates())
    }

    @Test
    fun previewListsChangedDaysWithoutTouchingData() {
        val preview = ledger.previewProfitChanges(tree, "营业额 - 成本 + 零钱")
        assertEquals(listOf("2026-10-01", "2026-10-03"), preview.changed.map { it.first })
        assertEquals(2, preview.affected)
        assertEquals(25.0, preview.totalAfter - preview.totalBefore, 1e-9)
        assertEquals(120.5, profit("2026-10-01"), 1e-9)
        assertEquals(emptyList<String>(), dirtyDates())
    }

    @Test
    fun recomputeRewritesOnlyChangedDaysAndSyncsThem() {
        ledger.saveSettings(tree, "营业额 - 成本 + 零钱")
        assertEquals(120.5, profit("2026-10-01"), 1e-9)
        assertEquals(2, ledger.recomputeProfits())
        assertEquals(140.5, profit("2026-10-01"), 1e-9)
        assertEquals(10.0, profit("2026-10-02"), 1e-9)
        assertEquals(35.0, profit("2026-10-03"), 1e-9)
        assertEquals(listOf("2026-10-01", "2026-10-03"), dirtyDates())
        assertEquals(0, ledger.recomputeProfits())
    }

    @Test
    fun renamesAreAppliedBeforeComparing() {
        val renamed = listOf(FieldNode("营业额", listOf("现金收入", "微信"))) + tree.drop(1)
        val preview = ledger.previewProfitChanges(renamed, "现金收入 + 微信 - 进货", emptyMap(), mapOf("营业额" to mapOf("现金" to "现金收入")))
        assertEquals(listOf(0, 0, 0), listOf(preview.changed.size, preview.failed.size, preview.affected))
    }

    @Test
    fun daysTheNewFormulaCannotComputeKeepTheirProfit() {
        assertEquals(listOf("2026-10-02"), ledger.previewProfitChanges(tree, "营业额 / 微信").failed)
        ledger.saveSettings(tree, "营业额 / 微信")
        assertEquals(2, ledger.recomputeProfits())
        assertEquals(10.0, profit("2026-10-02"), 1e-9)
        assertEquals(false, "2026-10-02" in dirtyDates())
    }

    @Test
    fun affectedCountsOnlyWhatThisChangeAlters() {
        ledger.saveSettings(tree, "营业额 - 成本 + 零钱")
        val preview = ledger.previewProfitChanges(tree + FieldNode("备用", emptyList()), "营业额 - 成本 + 零钱")
        assertEquals(0, preview.affected)
        assertEquals(2, preview.changed.size)
    }
}
