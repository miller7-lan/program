package com.homeapp.profithelper.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import com.homeapp.profithelper.core.FieldConfig
import com.homeapp.profithelper.core.FieldNode
import com.homeapp.profithelper.core.FormulaEngine
import com.homeapp.profithelper.data.Ledger
import com.homeapp.profithelper.data.ProfitPreview
import com.homeapp.profithelper.data.fmtMoney
import java.text.Normalizer

/** 编辑中的一级分类：orig / childOrig 记录载入时的原名（新增的为 null），保存时据此把改名同步到历史记录。 */
data class EditNode(val name: String, val children: List<String>, val orig: String?, val childOrig: List<String?>)

/** 返回 (语气, 说明)，与桌面端设置页的公式校验一致。 */
fun formulaProblem(formula: String, tree: List<FieldNode>): Pair<Tone, String> {
    if (formula.isBlank()) return Tone.DANGER to "公式不能为空"
    FormulaEngine.validate(formula)?.let { return Tone.DANGER to it }
    val used = FormulaEngine.extractVariables(formula).distinct()
    val names = FieldConfig.flatten(tree).map { Normalizer.normalize(it, Normalizer.Form.NFKC) }.toSet()
    val undefined = used.filter { it !in names }
    if (undefined.isNotEmpty()) return Tone.WARNING to "引用了不存在的分类：${undefined.joinToString("、")}，请先添加分类或修改公式"
    return Tone.SUCCESS to "公式有效，引用了 ${used.size} 个分类"
}

/** “历史利润会变化”对话框的说明文字，与桌面端设置页一致。 */
fun recomputeMessage(preview: ProfitPreview): String = buildString {
    append("按新的分类和公式计算，已保存的 ${preview.changed.size} 天利润会变化：\n")
    preview.changed.take(3).forEach { (date, old, new) -> append("  $date：${fmtMoney(old)} → ${fmtMoney(new)}\n") }
    if (preview.changed.size > 3) append("  ……共 ${preview.changed.size} 天\n")
    append("\n全部 ${preview.days} 天合计：${fmtMoney(preview.totalBefore)} → ${fmtMoney(preview.totalAfter)}")
    if (preview.failed.isNotEmpty()) append("\n另有 ${preview.failed.size} 天按新公式算不出来（例如出现除以 0），会保留原利润。")
    append("\n\n重算历史：这些天改用新公式，并同步到电脑。\n保留原利润：以前的利润不变，以后保存的记录才用新公式。")
}

/**
 * 设置页状态：放在界面之外（与录入页 EntryController 相同的做法），切换底部标签不会丢失编辑内容，
 * 离开设置页时也能询问是否保存。
 */
class SettingsController(private val ledger: Ledger) {
    var tree by mutableStateOf<List<EditNode>>(emptyList())
        private set
    var formula by mutableStateOf(TextFieldValue(""))
    /** 保存时发现分类或公式在编辑期间已被云同步更新，等待用户决定。 */
    var conflictPending by mutableStateOf(false)
        private set
    /** 这次修改会改变已保存的历史利润，等待用户选择：重算历史 / 保留原利润 / 取消。 */
    var recomputePending by mutableStateOf<ProfitPreview?>(null)
        private set
    private var recomputeForce = false
    private var savedSignature by mutableStateOf("")
    private var loadedVersion = -1L

    init {
        load()
    }

    val plainTree: List<FieldNode> get() = FieldConfig.normalize(tree.map { FieldNode(it.name, it.children) })

    /** 与 Ledger.settingsSignature 相同的写法：只整理了公式格式不算修改。 */
    private fun signature() = FieldConfig.treeToJson(plainTree) + "\u0000" + FormulaEngine.format(formula.text)

    val isDirty: Boolean get() = signature() != savedSignature

    val allNames: Set<String> get() = FieldConfig.flatten(plainTree).toSet()

    fun load() {
        tree = ledger.fieldTree().map { EditNode(it.name, it.children, it.name, it.children) }
        val text = FormulaEngine.format(ledger.getConfig("profit_formula"))
        formula = TextFieldValue(text, TextRange(text.length))
        savedSignature = ledger.settingsSignature()
        loadedVersion = ledger.version.value
        conflictPending = false
        recomputePending = null
    }

    /** 数据或设置变化（含云同步）后重新读取；有未保存的修改时保留，保存时再提示。 */
    fun onDataVersion(version: Long) {
        if (version != loadedVersion && !isDirty) load()
    }

    // ── 分类结构 ────────────────────────────

    fun addParent(name: String) {
        tree = tree + EditNode(name, emptyList(), null, emptyList())
    }

    fun addChild(parent: Int, name: String) {
        tree = tree.updated(parent) { it.copy(children = it.children + name, childOrig = it.childOrig + null) }
    }

    fun renameParent(parent: Int, name: String) {
        val old = tree[parent].name
        tree = tree.updated(parent) { it.copy(name = name) }
        renameInFormula(old, name)
    }

    fun renameChild(parent: Int, child: Int, name: String) {
        val old = tree[parent].children[child]
        tree = tree.updated(parent) { node -> node.copy(children = node.children.toMutableList().also { it[child] = name }) }
        renameInFormula(old, name)
    }

    fun deleteParent(parent: Int) {
        tree = tree.filterIndexed { index, _ -> index != parent }
    }

    fun deleteChild(parent: Int, child: Int) {
        tree = tree.updated(parent) { node ->
            node.copy(
                children = node.children.filterIndexed { index, _ -> index != child },
                childOrig = node.childOrig.filterIndexed { index, _ -> index != child },
            )
        }
    }

    fun moveParent(parent: Int, step: Int) {
        val target = parent + step
        if (target !in tree.indices) return
        tree = tree.toMutableList().apply { add(target, removeAt(parent)) }
    }

    fun moveChild(parent: Int, child: Int, step: Int) {
        val target = child + step
        if (target !in tree[parent].children.indices) return
        tree = tree.updated(parent) { node ->
            node.copy(
                children = node.children.toMutableList().apply { add(target, removeAt(child)) },
                childOrig = node.childOrig.toMutableList().apply { add(target, removeAt(child)) },
            )
        }
    }

    private fun renameInFormula(old: String, new: String) {
        if (old == new) return
        val pattern = Regex("(?<![\\p{L}\\p{N}_])" + Regex.escape(old) + "(?![\\p{L}\\p{N}_])")
        val updated = pattern.replace(formula.text) { new }
        if (updated != formula.text) {
            formula = TextFieldValue(updated, TextRange(updated.length))
            UiEvents.show("公式中的「$old」已同步改为「$new」")
        }
    }

    private fun renames(): Pair<Map<String, String>, Map<String, Map<String, String>>> {
        val parents = tree.mapNotNull { node -> node.orig?.takeIf { it != node.name }?.let { it to node.name } }.toMap()
        val children = tree.associate { node ->
            node.name to node.childOrig.zip(node.children)
                .mapNotNull { (old, new) -> old?.takeIf { it != new }?.let { it to new } }.toMap()
        }.filterValues { it.isNotEmpty() }
        return parents to children
    }

    // ── 公式 ────────────────────────────────

    fun insertFormula(text: String) {
        val selection = formula.selection
        val newText = formula.text.replaceRange(selection.min, selection.max, text)
        formula = TextFieldValue(newText, TextRange(selection.min + text.length))
    }

    fun backspace() {
        val sel = formula.selection
        if (sel.collapsed && sel.min > 0) {
            formula = TextFieldValue(formula.text.removeRange(sel.min - 1, sel.min), TextRange(sel.min - 1))
        } else if (!sel.collapsed) {
            formula = TextFieldValue(formula.text.removeRange(sel.min, sel.max), TextRange(sel.min))
        }
    }

    fun clearFormula() {
        formula = TextFieldValue("")
    }

    /** 离开公式框时自动整理格式（全角符号、空格）；公式有误时保持原样等待修改。 */
    fun tidyFormula() {
        val tidy = FormulaEngine.format(formula.text)
        if (tidy != formula.text && FormulaEngine.validate(tidy) == null) formula = TextFieldValue(tidy, TextRange(tidy.length))
    }

    // ── 保存 ────────────────────────────────

    /** recompute：null = 还没问过（会改变历史利润时先询问），true = 重算历史，false = 保留原利润。 */
    fun save(force: Boolean = false, recompute: Boolean? = null): Boolean {
        val normalized = plainTree
        if (normalized.isEmpty()) {
            UiEvents.alert("分类不能为空", "请至少添加一个一级分类。")
            return false
        }
        val clean = FormulaEngine.format(formula.text)
        val (tone, message) = formulaProblem(clean, normalized)
        if (tone != Tone.SUCCESS) {
            UiEvents.alert("公式格式错误", "$message\n\n请修改后再保存。")
            return false
        }
        if (!force && ledger.settingsSignature() != savedSignature) {
            conflictPending = true
            return false
        }
        conflictPending = false
        val (parentRenames, childRenames) = renames()
        if (recompute == null) {
            val preview = ledger.previewProfitChanges(normalized, clean, parentRenames, childRenames)
            if (preview.affected > 0 && preview.changed.isNotEmpty()) {  // 只在这次修改真的改变了历史利润时询问
                recomputeForce = force
                recomputePending = preview
                return false
            }
        }
        recomputePending = null
        val migrated = ledger.saveSettings(normalized, clean, parentRenames, childRenames)
        val recomputed = if (recompute == true) ledger.recomputeProfits() else 0
        load()
        var extra = if (migrated > 0) "，$migrated 天的历史记录已同步改名" else ""
        if (recomputed > 0) extra += "，$recomputed 天的利润已按新公式重算"
        UiEvents.show("设置已保存$extra，会自动同步到电脑")
        return true
    }

    /** choice：true = 重算历史，false = 保留原利润，null = 取消（继续编辑）。 */
    fun resolveRecompute(choice: Boolean?) {
        recomputePending = null
        if (choice != null) save(force = recomputeForce, recompute = choice)
    }

    /** choice：true = 仍然保存，false = 载入最新设置，null = 取消（继续编辑）。 */
    fun resolveConflict(choice: Boolean?) {
        conflictPending = false
        when (choice) {
            true -> save(force = true)
            false -> load()
            null -> Unit
        }
    }
}

private fun List<EditNode>.updated(index: Int, transform: (EditNode) -> EditNode): List<EditNode> =
    mapIndexed { i, node -> if (i == index) transform(node) else node }
