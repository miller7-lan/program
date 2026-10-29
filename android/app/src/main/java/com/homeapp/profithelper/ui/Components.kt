package com.homeapp.profithelper.ui

import androidx.annotation.DrawableRes
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Icon
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.homeapp.profithelper.R
import com.homeapp.profithelper.data.fmtCompact
import com.homeapp.profithelper.data.fmtMoney
import com.homeapp.profithelper.sync.SyncStatus
import com.homeapp.profithelper.sync.SyncTone
import com.homeapp.profithelper.ui.theme.Palette
import com.homeapp.profithelper.ui.theme.TextStyles
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow

@Composable
fun AppIcon(@DrawableRes id: Int, tint: Color = Palette.Text2, size: Dp = 20.dp, modifier: Modifier = Modifier) {
    Icon(painterResource(id), contentDescription = null, tint = tint, modifier = modifier.size(size))
}

@Composable
fun SectionCard(
    modifier: Modifier = Modifier,
    padding: PaddingValues = PaddingValues(16.dp),
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        color = Color.White,
        border = BorderStroke(1.dp, Palette.Border),
    ) {
        Column(Modifier.padding(padding), verticalArrangement = Arrangement.spacedBy(10.dp), content = content)
    }
}

enum class Tone { NEUTRAL, SUCCESS, WARNING, DANGER, ACCENT }

@Composable
fun ToneBadge(text: String, tone: Tone) {
    val (bg, fg) = when (tone) {
        Tone.SUCCESS -> Palette.SuccessSoft to Palette.Success
        Tone.WARNING -> Palette.WarningSoft to Palette.Warning
        Tone.DANGER -> Palette.DangerSoft to Palette.Danger
        Tone.ACCENT -> Palette.AccentSoft to Palette.Accent
        Tone.NEUTRAL -> Color(0xFFF0F1F5) to Palette.Text2
    }
    Text(
        text,
        color = fg,
        fontSize = 12.sp,
        fontWeight = FontWeight.SemiBold,
        modifier = Modifier.clip(RoundedCornerShape(10.dp)).background(bg).padding(horizontal = 8.dp, vertical = 3.dp),
    )
}

fun moneyColor(value: Double?): Color = if (value != null && value < 0) Palette.Danger else Palette.Text

@Composable
fun StatTile(title: String, value: Double?, sub: String, @DrawableRes icon: Int, accent: Color, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(16.dp),
        color = Color.White,
        border = BorderStroke(1.dp, Palette.Border),
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(title, style = TextStyles.Body2, modifier = Modifier.weight(1f))
                Box(
                    Modifier.size(28.dp).clip(RoundedCornerShape(8.dp)).background(accent.copy(alpha = 0.12f)),
                    contentAlignment = Alignment.Center,
                ) { AppIcon(icon, accent, 16.dp) }
            }
            Text(
                if (value == null) "—" else fmtMoney(value),
                fontSize = 19.sp,
                fontWeight = FontWeight.Bold,
                color = moneyColor(value),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(sub, style = TextStyles.Caption, maxLines = 2)
        }
    }
}

@Composable
fun SegmentedTabs(options: List<String>, selected: Int, onSelect: (Int) -> Unit) {
    Row(
        Modifier.clip(RoundedCornerShape(10.dp)).background(Color(0xFFF0F1F5)).padding(3.dp),
        horizontalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        options.forEachIndexed { index, text ->
            val active = index == selected
            Text(
                text,
                fontSize = 13.sp,
                fontWeight = if (active) FontWeight.SemiBold else FontWeight.Normal,
                color = if (active) Palette.Text else Palette.Text2,
                modifier = Modifier
                    .clip(RoundedCornerShape(8.dp))
                    .background(if (active) Color.White else Color.Transparent)
                    .clickable { onSelect(index) }
                    .padding(horizontal = 12.dp, vertical = 5.dp),
            )
        }
    }
}

@Composable
fun SyncChip(status: SyncStatus, onClick: () -> Unit) {
    val (icon, color) = when (status.tone) {
        SyncTone.OK -> R.drawable.ic_cloud_check to Palette.Success
        SyncTone.ERROR -> R.drawable.ic_alert to Palette.Danger
        SyncTone.BUSY -> R.drawable.ic_sync to Palette.Accent
        SyncTone.OFF -> R.drawable.ic_cloud_off to Palette.Text3
    }
    Row(
        Modifier.clip(RoundedCornerShape(16.dp)).background(Color.White).border(1.dp, Palette.Border, RoundedCornerShape(16.dp))
            .clickable(onClick = onClick).padding(horizontal = 10.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        AppIcon(icon, color, 16.dp)
        Text(status.title, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = color)
    }
}

/** 金额输入框：右对齐，非法时红框；支持算式（失去焦点时由调用方换算）。 */
@Composable
fun AmountField(
    value: String,
    onValueChange: (String) -> Unit,
    isError: Boolean,
    modifier: Modifier = Modifier,
    imeAction: ImeAction = ImeAction.Next,
    onImeAction: () -> Unit = {},
) {
    BasicTextField(
        value = value,
        onValueChange = onValueChange,
        singleLine = true,
        textStyle = TextStyle(fontSize = 15.sp, color = Palette.Text, textAlign = TextAlign.End),
        cursorBrush = SolidColor(Palette.Accent),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal, imeAction = imeAction),
        keyboardActions = KeyboardActions(onNext = { onImeAction() }, onDone = { onImeAction() }),
        modifier = modifier,
        decorationBox = { inner ->
            Box(
                Modifier
                    .fillMaxWidth()
                    .height(42.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .background(if (isError) Color(0xFFFFF7F7) else Color.White)
                    .border(1.dp, if (isError) Palette.Danger else Palette.BorderStrong, RoundedCornerShape(10.dp))
                    .padding(horizontal = 10.dp),
                contentAlignment = Alignment.CenterEnd,
            ) {
                if (value.isEmpty()) Text("0", color = Color(0xFFC3C8D6), fontSize = 15.sp)
                inner()
            }
        },
    )
}

data class ChartPoint(val label: String, val value: Double?, val title: String)

/** 柱状趋势图：正值主题色、负值红色、未录入灰点；点一下显示数值，再点一次回调 onOpen。 */
@Composable
fun TrendBars(points: List<ChartPoint>, emptyText: String, modifier: Modifier = Modifier, onOpen: (Int) -> Unit = {}) {
    val measurer = rememberTextMeasurer()
    var selected by remember(points) { mutableIntStateOf(-1) }
    if (points.none { it.value != null }) {
        Box(modifier.fillMaxWidth().height(180.dp), contentAlignment = Alignment.Center) {
            Text(emptyText, style = TextStyles.Caption)
        }
        return
    }
    val values = points.mapNotNull { it.value }
    var low = min(values.min(), 0.0)
    var high = max(values.max(), 0.0)
    if (high - low < 1e-9) high = low + 100
    val rawStep = (high - low) / 4
    val magnitude = 10.0.pow(floor(log10(rawStep)))
    val step = magnitude * (listOf(1.0, 2.0, 2.5, 5.0, 10.0).first { rawStep / magnitude <= it })
    low = floor(low / step) * step
    high = ceil(high / step) * step

    Canvas(
        modifier
            .fillMaxWidth()
            .height(190.dp)
            .pointerInput(points) {
                detectTapGestures { offset ->
                    val left = 44.dp.toPx()
                    val slot = (size.width - left) / points.size
                    val index = ((offset.x - left) / slot).toInt()
                    if (index in points.indices) {
                        if (index == selected) onOpen(index) else selected = index
                    }
                }
            },
    ) {
        val left = 44.dp.toPx()
        val top = 8.dp.toPx()
        val bottom = size.height - 22.dp.toPx()
        val plotHeight = bottom - top
        val slot = (size.width - left) / points.size
        fun yOf(v: Double) = (bottom - (v - low) / (high - low) * plotHeight).toFloat()
        val axisStyle = TextStyle(fontSize = 10.sp, color = Palette.Text3)

        var tick = low
        while (tick <= high + step / 2) {
            val y = yOf(tick)
            drawLine(if (kotlin.math.abs(tick) < 1e-9) Palette.BorderStrong else Color(0xFFEDEFF4), Offset(left, y), Offset(size.width, y), 1.dp.toPx())
            val layout = measurer.measure(fmtCompact(tick), axisStyle)
            drawText(layout, topLeft = Offset(left - layout.size.width - 6.dp.toPx(), y - layout.size.height / 2f))
            tick += step
        }
        val barWidth = max(3.dp.toPx(), min(18.dp.toPx(), slot * 0.62f))
        val zeroY = yOf(0.0)
        val labelEvery = max(1, ceil(34.dp.toPx() / slot).toInt())
        points.forEachIndexed { index, point ->
            val center = left + slot * index + slot / 2
            if (index == selected) {
                drawRect(Palette.AccentSoft, Offset(center - slot / 2, top), Size(slot, plotHeight))
            }
            val value = point.value
            if (value == null) {
                drawCircle(Color(0xFFCDD1DC), 2.dp.toPx(), Offset(center, zeroY))
            } else {
                val y1 = yOf(max(value, 0.0))
                val y2 = yOf(min(value, 0.0))
                val color = if (value < 0) Palette.Danger else Palette.Accent
                drawRoundRect(
                    color.copy(alpha = if (selected >= 0 && index != selected) 0.55f else 1f),
                    Offset(center - barWidth / 2, y1),
                    Size(barWidth, max(1.5f, y2 - y1)),
                    CornerRadius(min(4.dp.toPx(), barWidth / 2)),
                )
            }
            if (index % labelEvery == 0) {
                val layout = measurer.measure(point.label, axisStyle)
                drawText(layout, topLeft = Offset(center - layout.size.width / 2f, bottom + 5.dp.toPx()))
            }
        }
        if (selected in points.indices) {
            val point = points[selected]
            val text = "${point.title}  ${point.value?.let { fmtMoney(it) } ?: "未录入"}"
            val layout = measurer.measure(text, TextStyle(fontSize = 12.sp, color = Color.White, fontWeight = FontWeight.SemiBold))
            val w = layout.size.width + 16.dp.toPx()
            val h = layout.size.height + 10.dp.toPx()
            val center = left + slot * selected + slot / 2
            val x = (center - w / 2).coerceIn(left, size.width - w)
            drawRoundRect(Color(0xEB1D2130), Offset(x, top), Size(w, h), CornerRadius(8.dp.toPx()))
            drawText(layout, topLeft = Offset(x + 8.dp.toPx(), top + 5.dp.toPx()))
        }
    }
}

@Composable
fun LogoBadge(size: Dp = 40.dp) {
    Box(
        Modifier.size(size).clip(RoundedCornerShape(size * 0.24f)).background(Palette.Accent),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            painterResource(R.drawable.ic_launcher_foreground),
            contentDescription = null,
            tint = Color.Unspecified,
            modifier = Modifier.size(size * 1.5f),
        )
    }
}

@Composable
fun Dot(color: Color, size: Dp = 8.dp) {
    Box(Modifier.size(size).clip(CircleShape).background(color))
}

@Composable
fun HSpace(width: Dp) = Spacer(Modifier.width(width))
