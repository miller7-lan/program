package com.homeapp.profithelper.ui

import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.DateRangePicker
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDateRangePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.homeapp.profithelper.ProfitApp
import com.homeapp.profithelper.R
import com.homeapp.profithelper.core.FieldConfig
import com.homeapp.profithelper.data.ISO
import com.homeapp.profithelper.data.Report
import com.homeapp.profithelper.data.XlsxExporter
import com.homeapp.profithelper.data.fmtMoney
import com.homeapp.profithelper.data.fmtWhole
import com.homeapp.profithelper.data.weekdayLabel
import com.homeapp.profithelper.ui.theme.Palette
import com.homeapp.profithelper.ui.theme.TextStyles
import java.time.LocalDate
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private val PRESETS = listOf("本月", "上月", "近 30 天", "今年", "全部", "自定义")
private const val DAY_MS = 86_400_000L

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun HistoryScreen(app: ProfitApp, onOpenEntry: (LocalDate) -> Unit) {
    val ledger = app.ledger
    val version by ledger.version.collectAsState()
    var preset by rememberSaveable { mutableIntStateOf(0) }
    var customStart by rememberSaveable { mutableStateOf(LocalDate.now().minusDays(29).toString()) }
    var customEnd by rememberSaveable { mutableStateOf(LocalDate.now().toString()) }
    var query by rememberSaveable { mutableStateOf("") }
    var selected by remember { mutableStateOf(setOf<String>()) }
    var showRangePicker by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val today = LocalDate.now()

    val (start, end) = remember(preset, customStart, customEnd, version, today) {
        when (preset) {
            0 -> today.withDayOfMonth(1) to today
            1 -> today.withDayOfMonth(1).minusMonths(1).let { it to it.plusMonths(1).minusDays(1) }
            2 -> today.minusDays(29) to today
            3 -> today.withDayOfYear(1) to today
            4 -> ledger.dateBounds().let { (first, last) ->
                (first?.let(LocalDate::parse) ?: today) to maxOf(today, last?.let(LocalDate::parse) ?: today)
            }
            else -> LocalDate.parse(customStart) to LocalDate.parse(customEnd)
        }
    }
    val records = remember(version, start, end) { ledger.getRange(start.format(ISO), end.format(ISO)).reversed() }
    val tree = remember(version) { ledger.fieldTree() }
    val filtered = records.filter { query.isBlank() || query in it.note || query in it.date }
    val summary = Report.rangeSummary(filtered)

    BackHandler(enabled = selected.isNotEmpty()) { selected = emptySet() }  // 多选时返回键先取消选择

    val exportLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ) { uri ->
        if (uri != null) {
            scope.launch(Dispatchers.IO) {
                runCatching {
                    context.contentResolver.openOutputStream(uri)!!.use { XlsxExporter(ledger).export(it, start.format(ISO), end.format(ISO)) }
                }.onSuccess { UiEvents.show("已导出 Excel：${start.format(ISO)} 至 ${end.format(ISO)}") }
                    .onFailure { error -> withContext(Dispatchers.Main) { UiEvents.alert("导出失败", error.message ?: "写入文件时出错") } }
            }
        }
    }

    Column(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxWidth().background(Color.White).padding(top = 10.dp, bottom = 10.dp)) {
            Row(Modifier.padding(horizontal = 16.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("历史记录", style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f))
            }
            LazyRow(contentPadding = PaddingValues(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                items(PRESETS.size) { index ->
                    FilterChip(
                        selected = preset == index,
                        onClick = {
                            if (index == 5) showRangePicker = true else preset = index
                            selected = emptySet()
                        },
                        label = { Text(PRESETS[index]) },
                        colors = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = Palette.Accent,
                            selectedLabelColor = Color.White,
                        ),
                    )
                }
            }
            Row(Modifier.padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("${start.format(ISO)} 至 ${end.format(ISO)}", style = TextStyles.Body2, modifier = Modifier.weight(1f))
            }
            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                placeholder = { Text("搜索备注或日期", color = Palette.Text3) },
                leadingIcon = { AppIcon(R.drawable.ic_search, Palette.Text3, 18.dp) },
                singleLine = true,
                shape = RoundedCornerShape(12.dp),
                colors = OutlinedTextFieldDefaults.colors(unfocusedBorderColor = Palette.BorderStrong),
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
            )
        }
        HorizontalDivider(color = Palette.Border)

        LazyColumn(
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
            modifier = Modifier.weight(1f),
        ) {
            item {
                SectionCard {
                    Row {
                        SummaryCell("记录", "${summary.days} 天", null, Modifier.weight(1f))
                        SummaryCell("合计", fmtMoney(summary.total), summary.total, Modifier.weight(1.4f))
                        SummaryCell("日均", fmtMoney(summary.avg), summary.avg, Modifier.weight(1.3f))
                    }
                    Row {
                        SummaryCell("最高一天", if (summary.days > 0) fmtMoney(summary.max) else "—", summary.max, Modifier.weight(1f))
                        SummaryCell("最低一天", if (summary.days > 0) fmtMoney(summary.min) else "—", summary.min, Modifier.weight(1f))
                    }
                }
            }
            if (filtered.isEmpty()) {
                item { Text("这个区间没有记录", style = TextStyles.Body2, modifier = Modifier.padding(8.dp)) }
            }
            items(filtered, key = { it.date }) { record ->
                val day = LocalDate.parse(record.date)
                val isSelected = record.date in selected
                val totals = FieldConfig.parentTotals(tree, record.rawData)
                    .filter { (name, value) -> value != 0.0 && tree.firstOrNull { it.name == name }?.children?.isNotEmpty() == true }
                    .joinToString(" · ") { (name, value) -> "$name ${fmtWhole(value)}" }
                Surface(
                    shape = RoundedCornerShape(14.dp),
                    color = if (isSelected) Palette.AccentSoft else Color.White,
                    border = BorderStroke(1.dp, if (isSelected) Palette.Accent else Palette.Border),
                    modifier = Modifier.fillMaxWidth().combinedClickable(
                        onClick = {
                            if (selected.isNotEmpty()) {
                                selected = if (isSelected) selected - record.date else selected + record.date
                            } else {
                                onOpenEntry(day)
                            }
                        },
                        onLongClick = { selected = if (isSelected) selected - record.date else selected + record.date },
                    ),
                ) {
                    Row(Modifier.padding(horizontal = 14.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                        if (selected.isNotEmpty()) {
                            AppIcon(if (isSelected) R.drawable.ic_check else R.drawable.ic_x, if (isSelected) Palette.Accent else Palette.BorderStrong, 18.dp)
                            HSpace(10.dp)
                        }
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                            Text("${day.monthValue}月${day.dayOfMonth}日  ${weekdayLabel(day)}", fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                            if (totals.isNotEmpty()) Text(totals, style = TextStyles.Caption, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            if (record.note.isNotBlank()) Text(record.note, style = TextStyles.Body2, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                        Text(fmtMoney(record.totalProfit), style = TextStyles.Money, color = moneyColor(record.totalProfit))
                    }
                }
            }
        }

        // 底部固定操作栏：未选择时导出 Excel，长按选择记录后可删除
        Surface(color = Color.White) {
            Column {
                HorizontalDivider(color = Palette.Border)
                Row(Modifier.padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                    if (selected.isEmpty()) {
                        Text("点一条记录可修改，长按可多选删除", style = TextStyles.Caption, modifier = Modifier.weight(1f))
                        Button(
                            onClick = { exportLauncher.launch("利润报表_${start.format(ISO)}_${end.format(ISO)}.xlsx") },
                            contentPadding = PaddingValues(horizontal = 18.dp, vertical = 10.dp),
                        ) {
                            AppIcon(R.drawable.ic_sheet, Color.White, 18.dp)
                            HSpace(6.dp)
                            Text("导出 Excel")
                        }
                    } else {
                        Text("已选 ${selected.size} 天", fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                        TextButton(onClick = { selected = emptySet() }) { Text("取消", color = Palette.Text2) }
                        Button(
                            onClick = { confirmDelete = true },
                            colors = ButtonDefaults.buttonColors(containerColor = Palette.Danger),
                            contentPadding = PaddingValues(horizontal = 18.dp, vertical = 10.dp),
                        ) {
                            AppIcon(R.drawable.ic_trash, Color.White, 18.dp)
                            HSpace(6.dp)
                            Text("删除")
                        }
                    }
                }
            }
        }
    }

    if (showRangePicker) {
        val state = rememberDateRangePickerState(
            initialSelectedStartDateMillis = LocalDate.parse(customStart).toEpochDay() * DAY_MS,
            initialSelectedEndDateMillis = LocalDate.parse(customEnd).toEpochDay() * DAY_MS,
        )
        DatePickerDialog(
            onDismissRequest = { showRangePicker = false },
            confirmButton = {
                TextButton(
                    enabled = state.selectedStartDateMillis != null,
                    onClick = {
                        val s = state.selectedStartDateMillis!!
                        val e = state.selectedEndDateMillis ?: s
                        customStart = LocalDate.ofEpochDay(s / DAY_MS).toString()
                        customEnd = LocalDate.ofEpochDay(e / DAY_MS).toString()
                        preset = 5
                        showRangePicker = false
                    },
                ) { Text("确定") }
            },
            dismissButton = { TextButton(onClick = { showRangePicker = false }) { Text("取消") } },
        ) {
            DateRangePicker(state = state, showModeToggle = false, modifier = Modifier.height(520.dp))
        }
    }

    if (confirmDelete) {
        val dates = selected.sorted()
        AlertDialog(
            onDismissRequest = { confirmDelete = false },
            title = { Text("确认删除") },
            text = {
                Text("确定删除 ${dates.take(5).joinToString("、")}${if (dates.size > 5) " 等 ${dates.size} 天" else ""} 的记录吗？\n开启云同步时，其他设备上的这些记录也会被删除。")
            },
            confirmButton = {
                TextButton(onClick = {
                    val count = ledger.deleteRecords(dates)
                    UiEvents.show("已删除 $count 条记录")
                    selected = emptySet()
                    confirmDelete = false
                }) { Text("删除", color = Palette.Danger) }
            },
            dismissButton = { TextButton(onClick = { confirmDelete = false }) { Text("取消") } },
        )
    }
}

@Composable
private fun SummaryCell(caption: String, value: String, number: Double?, modifier: Modifier) {
    Column(modifier) {
        Text(caption, style = TextStyles.Caption)
        Text(value, fontWeight = FontWeight.Bold, fontSize = 15.sp, color = moneyColor(number), maxLines = 1)
    }
}
