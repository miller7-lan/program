package com.homeapp.profithelper.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.homeapp.profithelper.core.FieldConfig
import com.homeapp.profithelper.core.FieldNode
import com.homeapp.profithelper.core.FormulaEngine
import com.homeapp.profithelper.core.FormulaException
import com.homeapp.profithelper.core.RawData
import com.homeapp.profithelper.data.DailyRecord
import com.homeapp.profithelper.data.ISO
import com.homeapp.profithelper.data.Ledger
import com.homeapp.profithelper.data.fmtAmountInput
import com.homeapp.profithelper.data.fmtMoney
import java.time.LocalDate

private val LEADING_ZEROS = Regex("(?<![\\d.])0+(?=\\d)")
private val PLAIN_NUMBER = Regex("-?\\d+(\\.\\d+)?")

/** 录入框文本 → 金额（数字或简单算式），非法时返回 null；与桌面端 evaluate_amount 相同。 */
fun evaluateAmount(text: String): Double? {
    val normalized = FormulaEngine.normalizeFormula(text).replace(",", "").trim()
    if (normalized.isEmpty()) return 0.0
    return try {
        FormulaEngine.evaluate(LEADING_ZEROS.replace(normalized, ""), emptyMap())
    } catch (e: FormulaException) {
        null
    }
}

/** 录入页状态：放在界面之外，切换底部标签时询问是否保存也能用到。 */
class EntryController(private val ledger: Ledger) {
    var date by mutableStateOf(LocalDate.now())
        private set
    var tree by mutableStateOf<List<FieldNode>>(emptyList())
        private set
    var formula by mutableStateOf("")
        private set
    var hasRecord by mutableStateOf(false)
        private set
    var note by mutableStateOf("")
    var pendingDate by mutableStateOf<LocalDate?>(null)
    /** 保存时发现这一天在编辑期间已被云同步更新，等待用户决定是否覆盖。 */
    var conflictPending by mutableStateOf(false)
        private set
    val values = mutableStateMapOf<String, String>()
    private var loadedRecord: DailyRecord? = null

    private var snapshotValues: Map<String, Any> = emptyMap()
    private var snapshotNote = ""
    private var loadedVersion = -1L

    init {
        load(date)
    }

    fun key(parent: String, child: String?) = if (child == null) parent else "$parent::$child"

    fun displayName(key: String) = key.replace("::", " / ")

    private fun normalizedValues(): Map<String, Any> =
        values.mapValues { (_, text) -> evaluateAmount(text)?.let { Math.round(it * 1_000_000) / 1_000_000.0 } ?: text.trim() }

    val isDirty: Boolean get() = normalizedValues() != snapshotValues || note.trim() != snapshotNote

    fun dateTitle() = "${date.monthValue}月${date.dayOfMonth}日"

    fun load(target: LocalDate) {
        date = target
        tree = ledger.fieldTree()
        formula = ledger.profitFormula()
        val record = ledger.getRecord(target.format(ISO))
        loadedRecord = record
        hasRecord = record != null
        val raw = record?.rawData ?: emptyMap()
        values.clear()
        for (node in tree) {
            if (node.children.isEmpty()) {
                val amount = when (val stored = raw[node.name]) {
                    is Map<*, *> -> stored.values.sumOf { FieldConfig.toDouble(it) }
                    else -> FieldConfig.toDouble(stored)
                }
                values[key(node.name, null)] = fmtAmountInput(amount)
            } else {
                val stored = raw[node.name] as? Map<*, *>
                for (child in node.children) {
                    values[key(node.name, child)] = fmtAmountInput(FieldConfig.toDouble(stored?.get(child)))
                }
            }
        }
        note = record?.note ?: ""
        snapshotValues = normalizedValues()
        snapshotNote = note.trim()
        loadedVersion = ledger.version.value
    }

    fun requestDate(target: LocalDate) {
        if (target == date) return
        if (isDirty) pendingDate = target else load(target)
    }

    fun resolvePending(save: Boolean?) {
        val target = pendingDate ?: return
        pendingDate = null
        when (save) {
            true -> if (saveAndReport()) load(target)
            false -> load(target)
            null -> Unit
        }
    }

    /** 数据（含云同步拉取）或分类设置变化后重新读取；有未保存的修改时保留用户输入。 */
    fun onDataVersion(version: Long) {
        if (version != loadedVersion && !isDirty) load(date)
    }

    fun firstInvalidKey(): String? = values.entries.firstOrNull { evaluateAmount(it.value) == null }?.key

    fun rawData(): RawData {
        val raw = LinkedHashMap<String, Any>()
        for (node in tree) {
            if (node.children.isEmpty()) {
                raw[node.name] = evaluateAmount(values[node.name] ?: "") ?: 0.0
            } else {
                raw[node.name] = node.children.associateWith { evaluateAmount(values[key(node.name, it)] ?: "") ?: 0.0 }
            }
        }
        return raw
    }

    fun profit(raw: RawData = rawData()): Pair<Double?, String?> = try {
        FormulaEngine.evaluate(formula, FieldConfig.buildVariables(tree, raw)) to null
    } catch (e: FormulaException) {
        null to e.message
    }

    /** 失去焦点时把算式换算成结果（例如 120+35 → 155）。 */
    fun collapse(key: String) {
        val text = values[key]?.trim() ?: return
        if (text.isEmpty() || PLAIN_NUMBER.matches(text)) return
        val value = evaluateAmount(text) ?: return
        values[key] = fmtAmountInput(value).ifEmpty { "0" }
    }

    fun saveAndReport(force: Boolean = false): Boolean {
        firstInvalidKey()?.let {
            UiEvents.show("「${displayName(it)}」请输入数字或算式")
            return false
        }
        if (note.trim().length > Ledger.NOTE_MAX_CHARS) {
            UiEvents.show("备注最多 ${Ledger.NOTE_MAX_CHARS} 个字，请精简后再保存")
            return false
        }
        val raw = rawData()
        val (profit, error) = profit(raw)
        if (profit == null) {
            UiEvents.show("利润公式有误：$error")
            return false
        }
        // 打开这一天之后它被云同步（其他设备）改过：先让用户确认，避免静默覆盖
        if (!force && ledger.getRecord(date.format(ISO)) != loadedRecord) {
            conflictPending = true
            return false
        }
        conflictPending = false
        ledger.upsertRecord(date.format(ISO), profit, raw, note.trim())
        load(date)
        UiEvents.show("已保存 ${dateTitle()} · 利润 ${fmtMoney(profit)}")
        return true
    }

    fun discard() = load(date)

    /** choice：true = 仍然保存，false = 载入最新数据，null = 取消（继续编辑）。 */
    fun resolveConflict(choice: Boolean?) {
        conflictPending = false
        when (choice) {
            true -> saveAndReport(force = true)
            false -> load(date)
            null -> Unit
        }
    }
}
