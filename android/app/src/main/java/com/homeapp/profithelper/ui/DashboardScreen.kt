package com.homeapp.profithelper.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.homeapp.profithelper.ProfitApp
import com.homeapp.profithelper.R
import com.homeapp.profithelper.core.FieldConfig
import com.homeapp.profithelper.data.Report
import com.homeapp.profithelper.data.fmtMoney
import com.homeapp.profithelper.data.fmtWhole
import com.homeapp.profithelper.data.weekdayLabel
import com.homeapp.profithelper.ui.theme.Palette
import com.homeapp.profithelper.ui.theme.TextStyles
import java.time.LocalDate

@Composable
fun DashboardScreen(app: ProfitApp, onOpenEntry: (LocalDate) -> Unit) {
    val version by app.ledger.version.collectAsState()
    val syncStatus by app.sync.status.collectAsState()
    var range by rememberSaveable { mutableIntStateOf(0) }
    val today = LocalDate.now()
    val report = remember { Report(app.ledger) }
    val summary = remember(version, today) { report.summary(today) }
    val recent = remember(version) { app.ledger.getRecent(6) }
    val tree = remember(version) { app.ledger.fieldTree() }
    val daily = remember(version, today) { report.dailySeries(30, today) }
    val monthly = remember(version, today) { report.monthlySeries(12, today) }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                LogoBadge(40.dp)
                HSpace(10.dp)
                Column(Modifier.weight(1f)) {
                    Text("利润助手", fontSize = 20.sp, fontWeight = FontWeight.Bold, color = Palette.Text)
                    Text("${today.year}年${today.monthValue}月${today.dayOfMonth}日  ${weekdayLabel(today)}", style = TextStyles.Caption)
                }
                SyncChip(syncStatus) { app.sync.syncNow() }
            }
        }

        item {
            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(Palette.Accent).padding(18.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text("今日利润", color = Color.White.copy(alpha = 0.8f), fontSize = 13.sp)
                Text(
                    summary.today?.let { fmtMoney(it) } ?: "还没有录入",
                    color = Color.White,
                    fontSize = if (summary.today != null) 30.sp else 22.sp,
                    fontWeight = FontWeight.Bold,
                )
                val compare = when {
                    summary.today == null -> "录入后会自动按公式计算利润"
                    summary.yesterday == null -> "昨天没有记录"
                    else -> "较昨天 ${fmtMoney(summary.today - summary.yesterday, signed = true)}"
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(compare, color = Color.White.copy(alpha = 0.85f), fontSize = 13.sp, modifier = Modifier.weight(1f))
                    Button(
                        onClick = { onOpenEntry(today) },
                        colors = ButtonDefaults.buttonColors(containerColor = Color.White, contentColor = Palette.Accent),
                        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 6.dp),
                    ) {
                        AppIcon(R.drawable.ic_entry, Palette.Accent, 16.dp)
                        HSpace(6.dp)
                        Text(if (summary.today == null) "录入今天" else "修改今天")
                    }
                }
            }
        }

        item {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                StatTile(
                    "本月累计", summary.month,
                    "已记录 ${summary.monthDays} 天 · 日均 ${fmtMoney(summary.monthAvg)}",
                    R.drawable.ic_calendar, Color(0xFF0E9F8E), Modifier.weight(1f),
                )
                StatTile(
                    "今年累计", summary.year,
                    "已记录 ${summary.yearDays} 天 · 日均 ${fmtMoney(summary.yearAvg)}",
                    R.drawable.ic_trend, Color(0xFFE8743B), Modifier.weight(1f),
                )
            }
        }

        item {
            SectionCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("利润趋势", style = androidx.compose.material3.MaterialTheme.typography.titleMedium)
                        val caption = if (range == 0) {
                            val recorded = daily.mapNotNull { it.second }
                            "近 30 天记录 ${recorded.size} 天，合计 ${fmtMoney(recorded.sum())}"
                        } else {
                            "近 12 个月合计 ${fmtMoney(monthly.filter { it.days > 0 }.sumOf { it.total })}"
                        }
                        Text(caption, style = TextStyles.Caption)
                    }
                    SegmentedTabs(listOf("30 天", "12 个月"), range) { range = it }
                }
                if (range == 0) {
                    TrendBars(
                        points = daily.map { (day, value) ->
                            ChartPoint("${day.monthValue}-${day.dayOfMonth}", value, "${day.monthValue}月${day.dayOfMonth}日")
                        },
                        emptyText = "近 30 天暂无记录",
                        onOpen = { onOpenEntry(daily[it].first) },
                    )
                    Text("点一下柱子查看金额，再点一次打开这一天", style = TextStyles.Caption)
                } else {
                    TrendBars(
                        points = monthly.map { ChartPoint("${it.month}月", if (it.days > 0) it.total else null, "${it.year}年${it.month}月") },
                        emptyText = "近 12 个月暂无记录",
                    )
                }
            }
        }

        item {
            SectionCard(padding = PaddingValues(vertical = 12.dp)) {
                Row(Modifier.padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("最近记录", style = androidx.compose.material3.MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                    Text("点击可修改", style = TextStyles.Caption)
                }
                if (recent.isEmpty()) {
                    Text(
                        "还没有任何记录，点上方“录入今天”开始记账。",
                        style = TextStyles.Body2,
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
                    )
                }
                recent.forEachIndexed { index, record ->
                    if (index > 0) HorizontalDivider(color = Palette.Border, modifier = Modifier.padding(horizontal = 16.dp))
                    val day = LocalDate.parse(record.date)
                    val detail = record.note.ifBlank {
                        FieldConfig.parentTotals(tree, record.rawData).filter { (name, _) ->
                            tree.firstOrNull { it.name == name }?.children?.isNotEmpty() == true
                        }.take(2).joinToString(" · ") { (name, total) -> "$name ${fmtWhole(total)}" }
                    }
                    Row(
                        Modifier.fillMaxWidth().clickable { onOpenEntry(day) }.padding(horizontal = 16.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text("${day.monthValue}月${day.dayOfMonth}日  ${weekdayLabel(day)}", fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                            Text(detail, style = TextStyles.Caption, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                        Text(fmtMoney(record.totalProfit), style = TextStyles.Money, color = moneyColor(record.totalProfit))
                    }
                }
            }
        }
        item { Spacer(Modifier.height(4.dp)) }
    }
}
