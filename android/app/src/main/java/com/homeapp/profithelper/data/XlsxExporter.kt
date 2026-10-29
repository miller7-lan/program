package com.homeapp.profithelper.data

import com.homeapp.profithelper.core.FieldConfig
import com.homeapp.profithelper.core.FieldNode
import java.io.OutputStream
import java.time.LocalDate
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * 导出 Excel（.xlsx）—— 与桌面端 ReportEngine.export_to_excel 的版式一致：
 * 年度标注 / 月份标注 / 每月表头 / 每天按一级、二级分类展开并合并单元格。
 * 不依赖第三方库，直接写出最小的 OOXML 文件。
 */
class XlsxExporter(private val ledger: Ledger) {

    private enum class Style(val id: Int) { DEFAULT(0), TITLE(1), SUBTITLE(2), HEADER(3), YEAR(4), MONTH(5), BODY(6) }

    private data class Cell(val value: Any?, val style: Style)

    private val rows = sortedMapOf<Int, MutableMap<Int, Cell>>()
    private val merges = mutableListOf<String>()

    private fun set(row: Int, col: Int, value: Any?, style: Style = Style.DEFAULT) {
        rows.getOrPut(row) { sortedMapOf() }[col] = Cell(value, style)
    }

    private fun styleRow(row: Int, style: Style) {
        for (col in 1..8) {
            val existing = rows[row]?.get(col)
            set(row, col, existing?.value, style)
        }
    }

    private fun merge(r1: Int, c1: Int, r2: Int, c2: Int) {
        merges += "${ref(r1, c1)}:${ref(r2, c2)}"
    }

    private fun ref(row: Int, col: Int) = "${('A' + col - 1)}$row"

    fun export(output: OutputStream, start: String, end: String) {
        rows.clear()
        merges.clear()
        val records = ledger.getRange(start, end)
        val tree = ledger.fieldTree()

        set(1, 1, "利润分级报表", Style.TITLE)
        merge(1, 1, 1, 8)
        set(2, 1, "导出区间：$start 至 $end    标注说明：每月数据之间空 1 行，每年数据之间空 3 行，并展示月利润与年利润。", Style.SUBTITLE)
        merge(2, 1, 2, 8)

        if (records.isEmpty()) {
            set(5, 1, "当前筛选区间没有数据。")
        } else {
            var row = 5
            val grouped = records.groupBy { LocalDate.parse(it.date).year }.toSortedMap()
            for ((year, yearRecords) in grouped) {
                set(row, 1, "【年度标注】$year 年")
                set(row, 7, "年利润（元）")
                set(row, 8, round2(yearRecords.sumOf { it.totalProfit }))
                styleRow(row, Style.YEAR)
                row++
                val months = yearRecords.groupBy { LocalDate.parse(it.date).monthValue }.toSortedMap()
                for ((month, monthRecords) in months) {
                    set(row, 1, "【月份标注】$year 年 ${month.toString().padStart(2, '0')} 月")
                    set(row, 7, "月利润（元）")
                    set(row, 8, round2(monthRecords.sumOf { it.totalProfit }))
                    styleRow(row, Style.MONTH)
                    row++
                    listOf("日期", "一级分类", "二级分类", "金额（元）", "一级分类汇总（元）", "当日利润（元）", "标注", "备注")
                        .forEachIndexed { index, title -> set(row, index + 1, title, Style.HEADER) }
                    row++
                    for (record in monthRecords) row = writeRecord(row, record, tree)
                    row++
                }
                row += 2
            }
        }
        writeZip(output)
    }

    private fun writeRecord(startRow: Int, record: DailyRecord, tree: List<FieldNode>): Int {
        val details = expandDetails(record, tree).ifEmpty { listOf(Triple("未分类", "", 0.0)) }
        val totals = FieldConfig.buildVariables(tree, record.rawData)
        var cursor = startRow
        var index = 0
        val parentRanges = mutableListOf<Triple<Int, Int, String>>()
        while (index < details.size) {
            val parent = details[index].first
            val blockStart = cursor
            while (index < details.size && details[index].first == parent) {
                val (_, child, amount) = details[index]
                set(cursor, 3, child, Style.BODY)
                set(cursor, 4, round2(amount), Style.BODY)
                set(cursor, 7, if (child.isNotEmpty()) "二级分类明细" else "一级分类明细", Style.BODY)
                cursor++
                index++
            }
            parentRanges += Triple(blockStart, cursor - 1, parent)
        }
        val endRow = cursor - 1
        set(startRow, 1, record.date, Style.BODY)
        set(startRow, 6, round2(record.totalProfit), Style.BODY)
        set(startRow, 8, record.note, Style.BODY)
        if (endRow > startRow) {
            merge(startRow, 1, endRow, 1)
            merge(startRow, 6, endRow, 6)
            merge(startRow, 8, endRow, 8)
        }
        for ((blockStart, blockEnd, parent) in parentRanges) {
            set(blockStart, 2, parent, Style.BODY)
            set(blockStart, 5, round2(totals[parent] ?: 0.0), Style.BODY)
            if (blockEnd > blockStart) {
                merge(blockStart, 2, blockEnd, 2)
                merge(blockStart, 5, blockEnd, 5)
            }
        }
        for (row in startRow..endRow) {
            for (col in 1..8) if (rows[row]?.get(col) == null) set(row, col, null, Style.BODY)
        }
        return endRow + 1
    }

    private fun expandDetails(record: DailyRecord, tree: List<FieldNode>): List<Triple<String, String, Double>> {
        val result = mutableListOf<Triple<String, String, Double>>()
        val seen = mutableSetOf<String>()
        for (node in tree) {
            seen += node.name
            val stored = record.rawData[node.name]
            if (node.children.isNotEmpty()) {
                val values = stored as? Map<*, *> ?: emptyMap<String, Double>()
                for (child in node.children) result += Triple(node.name, child, FieldConfig.toDouble(values[child]))
            } else {
                result += Triple(node.name, "", if (stored is Map<*, *>) 0.0 else FieldConfig.toDouble(stored))
            }
        }
        for ((key, value) in record.rawData) {
            if (key in seen) continue
            if (value is Map<*, *>) {
                for ((child, amount) in value) result += Triple(key, child.toString(), FieldConfig.toDouble(amount))
            } else {
                result += Triple(key, "", FieldConfig.toDouble(value))
            }
        }
        return result
    }

    private fun round2(value: Double) = Math.round(value * 100.0) / 100.0

    // ─────────────────────────────────────────
    // OOXML 输出
    // ─────────────────────────────────────────

    private fun escape(text: String) = buildString {
        for (ch in text) {
            when (ch) {
                '&' -> append("&amp;")
                '<' -> append("&lt;")
                '>' -> append("&gt;")
                '"' -> append("&quot;")
                else -> if (ch.code >= 0x20 || ch == '\n' || ch == '\t') append(ch)
            }
        }
    }

    private fun sheetXml(): String = buildString {
        append("""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>""")
        append("""<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">""")
        append("<cols>")
        listOf(14, 18, 18, 14, 18, 14, 16, 18).forEachIndexed { index, width ->
            append("""<col min="${index + 1}" max="${index + 1}" width="$width" customWidth="1"/>""")
        }
        append("</cols><sheetData>")
        for ((rowIndex, cells) in rows) {
            append("""<row r="$rowIndex">""")
            for ((col, cell) in cells) {
                val ref = ref(rowIndex, col)
                val style = if (cell.style.id != 0) """ s="${cell.style.id}"""" else ""
                when (val value = cell.value) {
                    null -> append("""<c r="$ref"$style/>""")
                    is Number -> append("""<c r="$ref"$style><v>${value.toDouble().let { if (it == Math.rint(it)) it.toLong().toString() else it.toString() }}</v></c>""")
                    else -> append("""<c r="$ref"$style t="inlineStr"><is><t xml:space="preserve">${escape(value.toString())}</t></is></c>""")
                }
            }
            append("</row>")
        }
        append("</sheetData>")
        if (merges.isNotEmpty()) {
            append("""<mergeCells count="${merges.size}">""")
            merges.forEach { append("""<mergeCell ref="$it"/>""") }
            append("</mergeCells>")
        }
        append("</worksheet>")
    }

    private val stylesXml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="4">
<font><sz val="11"/><name val="Calibri"/></font>
<font><b/><sz val="16"/><name val="Calibri"/></font>
<font><i/><sz val="10"/><color rgb="FF666666"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><name val="Calibri"/></font>
</fonts>
<fills count="5">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFDCE6F1"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFFCE4D6"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFE2F0D9"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="7">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>
<xf numFmtId="0" fontId="3" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
<xf numFmtId="0" fontId="3" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>
<xf numFmtId="0" fontId="3" fillId="4" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""

    private fun writeZip(output: OutputStream) {
        val files = linkedMapOf(
            "[Content_Types].xml" to """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>""",
            "_rels/.rels" to """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
            "xl/workbook.xml" to """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="利润报表" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
            "xl/_rels/workbook.xml.rels" to """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""",
            "xl/styles.xml" to stylesXml,
            "xl/worksheets/sheet1.xml" to sheetXml(),
        )
        ZipOutputStream(output).use { zip ->
            for ((name, content) in files) {
                zip.putNextEntry(ZipEntry(name))
                zip.write(content.toByteArray(Charsets.UTF_8))
                zip.closeEntry()
            }
        }
    }
}
