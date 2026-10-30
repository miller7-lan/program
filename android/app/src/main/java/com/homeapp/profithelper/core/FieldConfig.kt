package com.homeapp.profithelper.core

import org.json.JSONArray
import org.json.JSONObject

/** 一级分类及其二级分类。没有二级分类时，一级分类本身就是一个录入项。 */
data class FieldNode(val name: String, val children: List<String>)

/** 一天的原始数据：一级分类名 → 金额（Double）或 二级分类名 → 金额（Map<String, Double>）。 */
typealias RawData = Map<String, Any>

/**
 * 分类树工具 —— 与桌面端 core/field_config.py 保持一致。
 */
object FieldConfig {

    val DEFAULT_TREE = listOf(
        FieldNode("营业额", listOf("账户", "微信分", "微信分欠", "现金", "邮政账户")),
        FieldNode(
            "成本",
            listOf(
                "小肠", "猪肚", "昨日零钱", "肥肠", "牛肚", "牛肠", "牛碎筋", "肚带", "羊杂", "羊肠",
                "猪肺", "牛熟", "生牛副筋", "袋子", "毛肚", "运费", "水电", "代买", "龙虾",
            ),
        ),
        FieldNode("零钱", emptyList()),
        FieldNode("营业额备注", emptyList()),
    )

    const val DEFAULT_FORMULA =
        "账户 + 邮政账户 + 微信分 + 微信分欠 + 现金 - 小肠 - 猪肚 - 羊杂 - 肥肠 - 牛肚 - 牛肠 - 牛碎筋 " +
            "- 肚带 - 猪肺 - 羊肠 - 昨日零钱 - 牛熟 - 生牛副筋 - 袋子 - 毛肚 - 运费 - 水电 - 代买 - 龙虾"

    private val KEYWORDS = setOf(
        "False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue",
        "def", "del", "elif", "else", "except", "finally", "for", "from", "global", "if", "import", "in",
        "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while", "with", "yield",
    )

    /** 从 JSON 配置读取分类树，兼容旧版的字符串列表；内容无效时返回默认分类。 */
    fun loadTree(json: String?): List<FieldNode> {
        if (json.isNullOrBlank()) return DEFAULT_TREE
        val array = try {
            JSONArray(json)
        } catch (e: Exception) {
            return DEFAULT_TREE
        }
        val items = mutableListOf<FieldNode>()
        for (index in 0 until array.length()) {
            when (val item = array.opt(index)) {
                is String -> items += FieldNode(item, emptyList())
                is JSONObject -> {
                    val children = item.optJSONArray("children")
                    val names = mutableListOf<String>()
                    if (children != null) {
                        for (c in 0 until children.length()) names += children.opt(c)?.toString() ?: ""
                    }
                    items += FieldNode(item.opt("name")?.toString() ?: "", names)
                }
            }
        }
        return normalize(items).ifEmpty { DEFAULT_TREE }
    }

    /** 规范化分类树：去掉空名称和重复项。 */
    fun normalize(items: List<FieldNode>): List<FieldNode> {
        val usedParents = mutableSetOf<String>()
        val result = mutableListOf<FieldNode>()
        for (item in items) {
            val parent = item.name.trim()
            if (parent.isEmpty() || parent in usedParents) continue
            usedParents += parent
            val seen = mutableSetOf<String>()
            val children = item.children.map { it.trim() }.filter { it.isNotEmpty() && seen.add(it) }
            result += FieldNode(parent, children)
        }
        return result
    }

    fun treeToJson(tree: List<FieldNode>): String {
        val array = JSONArray()
        for (node in tree) {
            array.put(JSONObject().put("name", node.name).put("children", JSONArray(node.children)))
        }
        return array.toString()
    }

    /** 公式可直接引用的变量名：一级汇总 + 二级明细。 */
    fun flatten(tree: List<FieldNode>): List<String> {
        val seen = LinkedHashSet<String>()
        for (node in tree) {
            seen += node.name
            seen += node.children
        }
        return seen.toList()
    }

    fun fieldsToJson(tree: List<FieldNode>): String = JSONArray(flatten(tree)).toString()

    /** 把一天的原始数据转换成公式变量（与桌面端 build_formula_variables 逐行对应）。 */
    fun buildVariables(tree: List<FieldNode>, raw: RawData): Map<String, Double> {
        val variables = LinkedHashMap<String, Double>()
        for (node in tree) {
            val stored = raw[node.name]
            if (node.children.isNotEmpty()) {
                var total = 0.0
                if (stored is Map<*, *>) {
                    for (child in node.children) {
                        val value = toDouble(stored[child])
                        variables[child] = value
                        total += value
                    }
                } else if (stored is Number) {
                    total = stored.toDouble()
                }
                variables[node.name] = total
                continue
            }
            variables[node.name] = toDouble(stored)
        }
        for ((key, value) in raw) {
            if (key !in variables && value is Number) {
                variables[key] = value.toDouble()
            } else if (value is Map<*, *>) {
                for ((child, amount) in value) {
                    val childName = child.toString()
                    if (childName !in variables) variables[childName] = toDouble(amount)
                }
            }
        }
        return variables
    }

    fun isFormulaIdentifier(name: String): Boolean {
        if (name.isEmpty() || name in KEYWORDS) return false
        var index = 0
        while (index < name.length) {
            val cp = name.codePointAt(index)
            val ok = cp == '_'.code || if (index == 0) {
                Character.isUnicodeIdentifierStart(cp) && !Character.isIdentifierIgnorable(cp)
            } else {
                Character.isUnicodeIdentifierPart(cp) && !Character.isIdentifierIgnorable(cp)
            }
            if (!ok) return false
            index += Character.charCount(cp)
        }
        return true
    }

    /** 检查新分类名，返回问题说明；没有问题时返回空字符串。 */
    fun nameProblem(name: String, existing: Set<String>): String = when {
        name.isEmpty() -> "名称不能为空。"
        name.length > 100 -> "名称太长，最多 100 个字。"
        name in existing -> "分类「$name」已存在，一级和二级分类之间也不能重名。"
        !isFormulaIdentifier(name) -> "名称只能包含中文、字母、数字和下划线，且不能以数字开头，否则无法在利润公式中引用。"
        else -> ""
    }

    fun toDouble(value: Any?): Double = when (value) {
        is Number -> value.toDouble().takeIf { it.isFinite() } ?: 0.0
        is String -> value.toDoubleOrNull() ?: 0.0
        else -> 0.0
    }

    // ── 原始数据与 JSON 互转 ───────────────────

    fun parseRawData(json: String?): RawData {
        if (json.isNullOrBlank()) return emptyMap()
        val obj = try {
            JSONObject(json)
        } catch (e: Exception) {
            return emptyMap()
        }
        val result = LinkedHashMap<String, Any>()
        val keys = obj.keys()
        while (keys.hasNext()) {
            val key = keys.next()
            when (val value = obj.opt(key)) {
                is JSONObject -> {
                    val child = LinkedHashMap<String, Double>()
                    val childKeys = value.keys()
                    while (childKeys.hasNext()) {
                        val childKey = childKeys.next()
                        child[childKey] = toDouble(value.opt(childKey))
                    }
                    result[key] = child
                }
                else -> result[key] = toDouble(value)
            }
        }
        return result
    }

    fun rawDataToJson(raw: RawData): String = rawDataToJsonObject(raw).toString()

    fun rawDataToJsonObject(raw: RawData): JSONObject {
        val obj = JSONObject()
        for ((key, value) in raw) {
            if (value is Map<*, *>) {
                val child = JSONObject()
                for ((childKey, amount) in value) child.put(childKey.toString(), toDouble(amount))
                obj.put(key, child)
            } else {
                obj.put(key, toDouble(value))
            }
        }
        return obj
    }

    /**
     * 分类改名后改写一天数据里的键名（与桌面端 migrate_raw_data 规则相同）。
     * parentRenames：旧一级名 → 新一级名；childRenames：新一级名 → (旧二级名 → 新二级名)。
     */
    fun migrateRawData(raw: RawData, parentRenames: Map<String, String>, childRenames: Map<String, Map<String, String>>): RawData {
        val migrated = LinkedHashMap<String, Any>()
        for ((key, value) in raw) {
            val newKey = parentRenames[key] ?: key
            if (value is Map<*, *>) {
                val mapping = childRenames[newKey].orEmpty()
                val renamed = LinkedHashMap<String, Double>()
                (migrated[newKey] as? Map<*, *>)?.forEach { (child, amount) -> renamed[child.toString()] = toDouble(amount) }
                for ((child, amount) in value) {
                    val name = child.toString()
                    renamed[mapping[name] ?: name] = toDouble(amount)
                }
                migrated[newKey] = renamed
            } else {
                migrated[newKey] = value
            }
        }
        return migrated
    }

    /**
     * 保存录入时合并（与桌面端 merge_entry_raw 相同）：当前分类以录入框为准，
     * 当前分类里已经没有的旧数据（例如删掉的分类）原样保留，重新保存某一天也不会丢。
     */
    fun mergeEntryRaw(previous: RawData, collected: RawData, tree: List<FieldNode>): RawData {
        val merged = LinkedHashMap<String, Any>()
        for (node in tree) {
            var value: Any = collected[node.name] ?: 0.0
            val old = previous[node.name]
            if (node.children.isNotEmpty() && value is Map<*, *> && old is Map<*, *>) {
                val extra = old.filterKeys { it.toString() !in node.children }
                if (extra.isNotEmpty()) {
                    val combined = LinkedHashMap<String, Double>()
                    value.forEach { (child, amount) -> combined[child.toString()] = toDouble(amount) }
                    extra.forEach { (child, amount) -> combined[child.toString()] = toDouble(amount) }
                    value = combined
                }
            }
            merged[node.name] = value
        }
        for ((key, value) in previous) if (key !in merged) merged[key] = value
        return merged
    }

    /** 一级分类汇总，用于列表展示，例如“营业额 3,804 · 成本 1,321”。 */
    fun parentTotals(tree: List<FieldNode>, raw: RawData): List<Pair<String, Double>> {
        val variables = buildVariables(tree, raw)
        return tree.map { it.name to (variables[it.name] ?: 0.0) }
    }
}
