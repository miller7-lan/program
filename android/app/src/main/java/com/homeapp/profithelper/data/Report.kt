package com.homeapp.profithelper.data

import java.text.DecimalFormat
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import kotlin.math.abs

val WEEKDAYS = listOf("周一", "周二", "周三", "周四", "周五", "周六", "周日")

fun weekdayLabel(date: LocalDate): String = WEEKDAYS[date.dayOfWeek.value - 1]

fun weekdayLabel(date: String): String = runCatching { weekdayLabel(LocalDate.parse(date)) }.getOrDefault("")

private val MONEY = DecimalFormat("#,##0.00")
private val WHOLE = DecimalFormat("#,##0")

/** ¥1,234.56；负数为 -¥1,234.56；signed=true 时正数带 + 号。 */
fun fmtMoney(value: Double, signed: Boolean = false): String {
    val sign = when {
        value < 0 -> "-"
        signed && value > 0 -> "+"
        else -> ""
    }
    return "$sign¥${MONEY.format(abs(value))}"
}

fun fmtNumber(value: Double): String = MONEY.format(value)

fun fmtWhole(value: Double): String = WHOLE.format(value)

/** 坐标轴用的紧凑格式：12,000 → 1.2万。 */
fun fmtCompact(value: Double): String {
    val magnitude = abs(value)
    return when {
        magnitude >= 100_000_000 -> String.format("%.1f亿", value / 100_000_000).replace(".0亿", "亿")
        magnitude >= 10_000 -> String.format("%.1f万", value / 10_000).replace(".0万", "万")
        else -> WHOLE.format(value)
    }
}

/** 录入框中显示的金额：0 显示为空，整数不带小数位。 */
fun fmtAmountInput(value: Double): String {
    if (value == 0.0) return ""
    if (value == Math.rint(value) && abs(value) < 1e15) return value.toLong().toString()
    return String.format(java.util.Locale.US, "%.4f", value).trimEnd('0').trimEnd('.')
}

val ISO: DateTimeFormatter = DateTimeFormatter.ISO_LOCAL_DATE

data class Summary(
    val today: Double?,
    val yesterday: Double?,
    val month: Double,
    val monthDays: Int,
    val monthAvg: Double,
    val monthBest: Pair<String, Double>?,
    val year: Double,
    val yearDays: Int,
    val yearAvg: Double,
)

data class RangeSummary(val days: Int, val total: Double, val avg: Double, val max: Double, val min: Double)

/** 看板统计（与桌面端 ReportEngine 对应）。 */
class Report(private val ledger: Ledger) {

    fun summary(today: LocalDate = LocalDate.now()): Summary {
        val yearRecords = ledger.getRange(today.withDayOfYear(1).format(ISO), today.format(ISO))
        val byDate = yearRecords.associateBy { it.date }
        val yesterdayKey = today.minusDays(1).format(ISO)
        val yesterday = byDate[yesterdayKey] ?: ledger.getRecord(yesterdayKey)
        val monthPrefix = today.format(ISO).substring(0, 7)
        val monthRecords = yearRecords.filter { it.date.startsWith(monthPrefix) }
        val monthTotal = monthRecords.sumOf { it.totalProfit }
        val yearTotal = yearRecords.sumOf { it.totalProfit }
        val best = monthRecords.maxByOrNull { it.totalProfit }
        return Summary(
            today = byDate[today.format(ISO)]?.totalProfit,
            yesterday = yesterday?.totalProfit,
            month = monthTotal,
            monthDays = monthRecords.size,
            monthAvg = if (monthRecords.isEmpty()) 0.0 else monthTotal / monthRecords.size,
            monthBest = best?.let { it.date to it.totalProfit },
            year = yearTotal,
            yearDays = yearRecords.size,
            yearAvg = if (yearRecords.isEmpty()) 0.0 else yearTotal / yearRecords.size,
        )
    }

    fun dailySeries(days: Int = 30, today: LocalDate = LocalDate.now()): List<Pair<LocalDate, Double?>> {
        val start = today.minusDays((days - 1).toLong())
        val records = ledger.getRange(start.format(ISO), today.format(ISO)).associateBy { it.date }
        return (0 until days).map { offset ->
            val day = start.plusDays(offset.toLong())
            day to records[day.format(ISO)]?.totalProfit
        }
    }

    /** 最近 months 个月：(年, 月, 合计, 记录天数) */
    fun monthlySeries(months: Int = 12, today: LocalDate = LocalDate.now()): List<MonthBucket> {
        val first = today.withDayOfMonth(1).minusMonths((months - 1).toLong())
        val buckets = (0 until months).map {
            val month = first.plusMonths(it.toLong())
            MonthBucket(month.year, month.monthValue, 0.0, 0)
        }.associateBy { it.year to it.month }.toMutableMap()
        for (record in ledger.getRange(first.format(ISO), today.format(ISO))) {
            val day = LocalDate.parse(record.date)
            val key = day.year to day.monthValue
            buckets[key]?.let { buckets[key] = it.copy(total = it.total + record.totalProfit, days = it.days + 1) }
        }
        return buckets.values.sortedWith(compareBy({ it.year }, { it.month }))
    }

    companion object {
        fun rangeSummary(records: List<DailyRecord>): RangeSummary {
            if (records.isEmpty()) return RangeSummary(0, 0.0, 0.0, 0.0, 0.0)
            val profits = records.map { it.totalProfit }
            val total = profits.sum()
            return RangeSummary(profits.size, total, total / profits.size, profits.max(), profits.min())
        }
    }
}

data class MonthBucket(val year: Int, val month: Int, val total: Double, val days: Int)
