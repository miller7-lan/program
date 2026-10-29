package com.homeapp.profithelper.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.homeapp.profithelper.ProfitApp
import com.homeapp.profithelper.R
import com.homeapp.profithelper.core.FieldConfig
import com.homeapp.profithelper.core.FieldNode
import com.homeapp.profithelper.data.fmtMoney
import com.homeapp.profithelper.data.weekdayLabel
import com.homeapp.profithelper.ui.theme.Palette
import com.homeapp.profithelper.ui.theme.TextStyles
import java.time.LocalDate

private const val DAY_MS = 86_400_000L

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun EntryScreen(app: ProfitApp, entry: EntryController) {
    val version by app.ledger.version.collectAsState()
    LaunchedEffect(version) { entry.onDataVersion(version) }
    var showPicker by remember { mutableStateOf(false) }
    val focusManager = LocalFocusManager.current
    val raw = entry.rawData()
    val variables = FieldConfig.buildVariables(entry.tree, raw)
    val (profit, error) = entry.profit(raw)
    val dirty = entry.isDirty
    val today = LocalDate.now()

    Column(Modifier.fillMaxSize().imePadding()) {
        Column(Modifier.fillMaxWidth().background(Color.White).padding(horizontal = 12.dp, vertical = 10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("录入", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(start = 4.dp))
                HSpace(10.dp)
                when {
                    entry.hasRecord -> ToneBadge("已录入", Tone.SUCCESS)
                    entry.date.isAfter(today) -> ToneBadge("未来日期", Tone.WARNING)
                    else -> ToneBadge("新记录", Tone.NEUTRAL)
                }
                if (dirty) {
                    HSpace(6.dp)
                    ToneBadge("未保存", Tone.WARNING)
                }
                Spacer(Modifier.weight(1f))
                if (entry.date != today) TextButton(onClick = { entry.requestDate(today) }) { Text("回到今天") }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = { entry.requestDate(entry.date.minusDays(1)) }) { AppIcon(R.drawable.ic_chevron_left) }
                Row(
                    Modifier.weight(1f).clip(RoundedCornerShape(12.dp)).border(1.dp, Palette.BorderStrong, RoundedCornerShape(12.dp))
                        .clickable { showPicker = true }.padding(horizontal = 14.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    AppIcon(R.drawable.ic_calendar, Palette.Accent, 18.dp)
                    HSpace(10.dp)
                    Column(Modifier.weight(1f)) {
                        Text("${entry.date.monthValue}月${entry.date.dayOfMonth}日  ${weekdayLabel(entry.date)}", fontSize = 17.sp, fontWeight = FontWeight.Bold)
                        Text("${entry.date.year}年 · 点这里选择日期", style = TextStyles.Caption)
                    }
                    AppIcon(R.drawable.ic_chevron_down, Palette.Text3, 18.dp)
                }
                IconButton(onClick = { entry.requestDate(entry.date.plusDays(1)) }) { AppIcon(R.drawable.ic_chevron_right) }
            }
        }
        HorizontalDivider(color = Palette.Border)

        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            entry.tree.forEach { node -> CategoryCard(node, entry, variables, onNext = { focusManager.moveFocus(FocusDirection.Next) }) }
            SectionCard {
                Text("备注", style = MaterialTheme.typography.titleMedium)
                OutlinedTextField(
                    value = entry.note,
                    onValueChange = { entry.note = it },
                    placeholder = { Text("例如：租金已支付、活动日营业额偏高", color = Palette.Text3) },
                    minLines = 2,
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(12.dp),
                    colors = OutlinedTextFieldDefaults.colors(unfocusedBorderColor = Palette.BorderStrong),
                )
                Text("留空不会清除这一天已保存的备注", style = TextStyles.Caption)
            }
            Spacer(Modifier.height(8.dp))
        }

        Surface(color = Color.White) {
            Column {
                HorizontalDivider(color = Palette.Border)
                Row(Modifier.padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("当日利润（按公式计算）", style = TextStyles.Caption)
                        Text(
                            profit?.let { fmtMoney(it) } ?: "公式有误",
                            fontSize = 22.sp,
                            fontWeight = FontWeight.Bold,
                            color = when {
                                profit == null -> Palette.Warning
                                profit < 0 -> Palette.Danger
                                else -> Palette.Accent
                            },
                        )
                        if (error != null) Text(error, color = Palette.Warning, fontSize = 11.sp, maxLines = 2)
                    }
                    if (dirty) TextButton(onClick = { entry.discard() }) { Text("撤销", color = Palette.Text2) }
                    Button(onClick = { focusManager.clearFocus(); entry.saveAndReport() }, contentPadding = PaddingValues(horizontal = 20.dp, vertical = 10.dp)) {
                        AppIcon(R.drawable.ic_save, Color.White, 18.dp)
                        HSpace(6.dp)
                        Text("保存")
                    }
                }
            }
        }
    }

    if (showPicker) {
        val state = rememberDatePickerState(initialSelectedDateMillis = entry.date.toEpochDay() * DAY_MS)
        DatePickerDialog(
            onDismissRequest = { showPicker = false },
            confirmButton = {
                TextButton(onClick = {
                    state.selectedDateMillis?.let { entry.requestDate(LocalDate.ofEpochDay(it / DAY_MS)) }
                    showPicker = false
                }) { Text("确定") }
            },
            dismissButton = { TextButton(onClick = { showPicker = false }) { Text("取消") } },
        ) { DatePicker(state = state, showModeToggle = false) }
    }

    entry.pendingDate?.let {
        UnsavedChangesDialog(
            dateLabel = entry.dateTitle(),
            onSave = { entry.resolvePending(true) },
            onDiscard = { entry.resolvePending(false) },
            onCancel = { entry.resolvePending(null) },
        )
    }
}

@Composable
private fun CategoryCard(node: FieldNode, entry: EntryController, variables: Map<String, Double>, onNext: () -> Unit) {
    SectionCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Dot(Palette.Accent)
            HSpace(8.dp)
            Text(node.name, style = MaterialTheme.typography.titleMedium)
            if (node.children.isNotEmpty()) {
                HSpace(6.dp)
                Text("${node.children.size} 项", style = TextStyles.Caption)
                Spacer(Modifier.weight(1f))
                val total = variables[node.name] ?: 0.0
                Text("小计 ${fmtMoney(total)}", color = if (total < 0) Palette.Danger else Palette.Accent, fontWeight = FontWeight.Bold, fontSize = 14.sp)
            }
        }
        if (node.children.isEmpty()) {
            AmountCell("金额", entry.key(node.name, null), entry, onNext, Modifier.fillMaxWidth())
        } else {
            BoxWithConstraints {
                val columns = if (maxWidth >= 300.dp) 2 else 1
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    node.children.chunked(columns).forEach { row ->
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            row.forEach { child -> AmountCell(child, entry.key(node.name, child), entry, onNext, Modifier.weight(1f)) }
                            repeat(columns - row.size) { Spacer(Modifier.weight(1f)) }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun AmountCell(label: String, key: String, entry: EntryController, onNext: () -> Unit, modifier: Modifier) {
    val text = entry.values[key] ?: ""
    Column(modifier, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(label, style = TextStyles.Body2, maxLines = 1)
        AmountField(
            value = text,
            onValueChange = { entry.values[key] = it },
            isError = evaluateAmount(text) == null,
            modifier = Modifier.fillMaxWidth().onFocusChanged { if (!it.isFocused) entry.collapse(key) },
            onImeAction = onNext,
        )
    }
}
