package com.homeapp.profithelper

import com.homeapp.profithelper.core.FieldConfig
import com.homeapp.profithelper.core.FieldNode
import com.homeapp.profithelper.core.FormulaEngine
import com.homeapp.profithelper.core.FormulaException
import com.homeapp.profithelper.core.RawData
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 与桌面端（Python）逐条比对：测试向量由 tools/make_formula_cases.py 生成。
 * 两端对同一公式必须给出相同的结果或相同的错误说明、相同的自动整理格式；
 * 分类改名后改写的数据、保存录入时合并的数据也必须相同。
 */
class FormulaParityTest {

    private val data: JSONObject by lazy {
        val text = javaClass.classLoader!!.getResource("formula_cases.json")!!.readText(Charsets.UTF_8)
        JSONObject(text)
    }

    private fun variables(): Map<String, Double> {
        val obj = data.getJSONObject("variables")
        return obj.keys().asSequence().associateWith { obj.getDouble(it) }
    }

    @Test
    fun everyCaseMatchesDesktop() {
        val vars = variables()
        val cases = data.getJSONArray("cases")
        val failures = mutableListOf<String>()
        for (index in 0 until cases.length()) {
            val case = cases.getJSONObject(index)
            val formula = case.getString("formula")
            val formatted = FormulaEngine.format(formula)
            if (formatted != case.getString("formatted")) {
                failures += "#$index $formula: 整理格式应为「${case.getString("formatted")}」，实际「$formatted」"
            }
            if (FormulaEngine.format(formatted) != formatted) failures += "#$index $formula: 再次整理后格式又变了"
            val expectedValid = case.getBoolean("valid")
            val validation = FormulaEngine.validate(formula)
            if ((validation == null) != expectedValid) {
                failures += "#$index $formula: valid 应为 $expectedValid，实际 ${validation ?: "通过"}"
                continue
            }
            if (!expectedValid) {
                if (validation != case.getString("error")) {
                    failures += "#$index $formula: 错误应为「${case.getString("error")}」，实际「$validation」"
                }
                continue
            }
            val expectedNames = case.getJSONArray("variables_used").let { a -> (0 until a.length()).map { a.getString(it) } }
            val actualNames = FormulaEngine.extractVariables(formula).toSortedSet().toList()
            if (expectedNames.sorted() != actualNames) {
                failures += "#$index $formula: 变量应为 $expectedNames，实际 $actualNames"
            }
            try {
                val result = FormulaEngine.evaluate(formula, vars)
                if (!case.has("result")) {
                    failures += "#$index $formula: 应报错「${case.optString("error")}」，实际得到 $result"
                } else if (result != case.getDouble("result")) {
                    failures += "#$index $formula: 结果应为 ${case.getDouble("result")}，实际 $result"
                }
            } catch (e: FormulaException) {
                if (e.message != case.optString("error")) {
                    failures += "#$index $formula: 计算错误应为「${case.optString("error")}」，实际「${e.message}」"
                }
            }
        }
        assertTrue("与桌面端不一致：\n" + failures.joinToString("\n"), failures.isEmpty())
    }

    private fun rawOf(json: JSONObject): RawData {
        val raw = LinkedHashMap<String, Any>()
        for (key in json.keys()) {
            val value = json.get(key)
            raw[key] = if (value is JSONObject) {
                value.keys().asSequence().associateWith { value.getDouble(it) }
            } else {
                (value as Number).toDouble()
            }
        }
        return raw
    }

    private fun stringMap(json: JSONObject): Map<String, String> = json.keys().asSequence().associateWith { json.getString(it) }

    private fun treeOf(json: JSONArray): List<FieldNode> = (0 until json.length()).map { index ->
        val node = json.getJSONObject(index)
        val children = node.getJSONArray("children")
        FieldNode(node.getString("name"), (0 until children.length()).map { children.getString(it) })
    }

    @Test
    fun renameMigrationMatchesDesktop() {
        val cases = data.getJSONArray("migrate_cases")
        assertTrue(cases.length() > 0)
        for (index in 0 until cases.length()) {
            val case = cases.getJSONObject(index)
            val childJson = case.getJSONObject("child_renames")
            val children = childJson.keys().asSequence().associateWith { stringMap(childJson.getJSONObject(it)) }
            val actual = FieldConfig.migrateRawData(rawOf(case.getJSONObject("raw")), stringMap(case.getJSONObject("parent_renames")), children)
            assertEquals("改名用例 #$index", rawOf(case.getJSONObject("expected")), actual)
        }
    }

    @Test
    fun entryMergeMatchesDesktop() {
        val cases = data.getJSONArray("merge_cases")
        assertTrue(cases.length() > 0)
        for (index in 0 until cases.length()) {
            val case = cases.getJSONObject(index)
            val actual = FieldConfig.mergeEntryRaw(
                rawOf(case.getJSONObject("previous")),
                rawOf(case.getJSONObject("collected")),
                treeOf(case.getJSONArray("tree")),
            )
            assertEquals("合并用例 #$index", rawOf(case.getJSONObject("expected")), actual)
        }
    }

    @Test
    fun defaultTreeVariablesAndProfitMatchDesktop() {
        val case = data.getJSONObject("default_tree_case")
        val raw = rawOf(case.getJSONObject("raw_data"))
        val expected = case.getJSONObject("variables")
        val actual = FieldConfig.buildVariables(FieldConfig.DEFAULT_TREE, raw)
        assertEquals(expected.keys().asSequence().toSet(), actual.keys)
        for (key in expected.keys()) assertEquals(key, expected.getDouble(key), actual.getValue(key), 0.0)
        assertEquals(case.getDouble("result"), FormulaEngine.evaluate(case.getString("formula"), actual), 0.0)
        assertEquals(FieldConfig.DEFAULT_FORMULA, case.getString("formula"))
    }

    @Test
    fun nameRulesMatchDesktop() {
        val existing = setOf("营业额", "现金")
        assertEquals("", FieldConfig.nameProblem("微信_2", existing))
        assertTrue(FieldConfig.nameProblem("现金", existing).contains("已存在"))
        assertTrue(FieldConfig.nameProblem("2号", existing).contains("不能以数字开头"))
        assertTrue(FieldConfig.nameProblem("微信-欠", existing).contains("只能包含"))
        assertTrue(FieldConfig.nameProblem("if", existing).isNotEmpty())
        assertEquals("名称不能为空。", FieldConfig.nameProblem("", existing))
    }
}
