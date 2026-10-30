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
import java.time.temporal.ChronoUnit

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
    /** 要保存的日期在今天之后（多半选错了年份或日期），等待用户确认。 */
    var futurePending by mutableStateOf(false)
        private set
    private var afterFutureSave: LocalDate? = null
    val values = mutableStateMapOf<String, String>()
    private var loadedRecord: DailyRecord? = null

    private var snapshotValues: Map<String, Any> = emptyMap()
    private var snapshotNote = ""
    private var loadedVersion = -1L
    private var loadedAsToday = true

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
        loadedAsToday = target == LocalDate.now()
    }

    /** 正在看的是“今天”而日期已过零点（例如应用一直开着）：没有未保存的修改时自动换到新的今天。 */
    fun rollOverToToday() {
        val today = LocalDate.now()
        if (loadedAsToday && date != today && !isDirty) load(today)
    }

    fun requestDate(target: LocalDate) {
        if (target == date) return
        if (isDirty) pendingDate = target else load(target)
    }

    fun resolvePending(save: Boolean?) {
        val target = pendingDate ?: return
        pendingDate = null
        when (save) {
            true -> if (saveAndReport()) load(target) else if (futurePending) afterFutureSave = target
            false -> load(target)
            null -> Unit
        }
    }

    /**
     * 数据（含云同步拉取）或分类设置变化后重新读取。有未保存的输入时只刷新分类和公式并保留输入
     * （与桌面端一致）；这一天的记录若同时被同步改过，保存时仍会提示，避免静默覆盖。
     */
    fun onDataVersion(version: Long) {
        if (version == loadedVersion) return
        if (!isDirty) {
            load(date)
            return
        }
        loadedVersion = version
        formula = ledger.profitFormula()
        val newTree = ledger.fieldTree()
        if (newTree == tree) return
        val raw = loadedRecord?.rawData ?: emptyMap()
        val keys = mutableSetOf<String>()
        for (node in newTree) {
            if (node.children.isEmpty()) {
                keys += node.name
                values.getOrPut(node.name) {
                    fmtAmountInput(when (val stored = raw[node.name]) {
                        is Map<*, *> -> stored.values.sumOf { FieldConfig.toDouble(it) }
                        else -> FieldConfig.toDouble(stored)
                    })
                }
            } else {
                val stored = raw[node.name] as? Map<*, *>
                for (child in node.children) {
                    val key = key(node.name, child)
                    keys += key
                    values.getOrPut(key) { fmtAmountInput(FieldConfig.toDouble(stored?.get(child))) }
                }
            }
        }
        (values.keys - keys).forEach { values.remove(it) }
        tree = newTree
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

    /** force：已确认覆盖同步冲突（此前也已确认过日期）；futureConfirmed：已确认保存到今天之后的日期。 */
    fun saveAndReport(force: Boolean = false, futureConfirmed: Boolean = false): Boolean {
        firstInvalidKey()?.let {
            UiEvents.alert(
                "金额格式错误",
                "「${displayName(it)}」填写的“${values[it]?.trim()}”不是有效的金额。\n\n请输入数字或算式，例如：120、85.5、120+35、200-12.5",
            )
            return false
        }
        if (note.trim().length > Ledger.NOTE_MAX_CHARS) {
            UiEvents.alert("备注太长", "备注最多 ${Ledger.NOTE_MAX_CHARS} 个字，当前 ${note.trim().length} 个字，请精简后再保存。")
            return false
        }
        val raw = rawData()
        val (profit, error) = profit(raw)
        if (profit == null) {
            UiEvents.alert("公式格式错误", "利润公式无法计算：$error\n\n请到“设置 → 利润公式”中修改。")
            return false
        }
        if (!force && !futureConfirmed && date.isAfter(LocalDate.now())) {
            futurePending = true
            return false
        }
        // 打开这一天之后它被云同步（其他设备）改过：先让用户确认，避免静默覆盖
        if (!force && ledger.getRecord(date.format(ISO)) != loadedRecord) {
            conflictPending = true
            return false
        }
        conflictPending = false
        // 保留这一天里当前分类以外的数据（例如删掉的分类、其他设备新增的分类），不因保存而丢失
        val merged = FieldConfig.mergeEntryRaw(loadedRecord?.rawData ?: emptyMap(), raw, tree)
        ledger.upsertRecord(date.format(ISO), profit, merged, note.trim(), keepNoteIfEmpty = false)
        load(date)
        UiEvents.show("已保存 ${dateTitle()} · 利润 ${fmtMoney(profit)}")
        return true
    }

    fun discard() = load(date)

    fun futureMessage(): String {
        val today = LocalDate.now()
        return "要保存的日期是 ${date.year}年${date.monthValue}月${date.dayOfMonth}日，" +
            "比今天（${today.year}年${today.monthValue}月${today.dayOfMonth}日）晚 ${ChronoUnit.DAYS.between(today, date)} 天。\n" +
            "请确认没有选错年份或日期。"
    }

    /** confirm：true = 仍然保存（之后继续切换到原本要去的日期），false = 取消（继续编辑）。 */
    fun resolveFuture(confirm: Boolean) {
        futurePending = false
        val next = afterFutureSave
        afterFutureSave = null
        if (confirm && saveAndReport(futureConfirmed = true) && next != null) load(next)
    }

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
