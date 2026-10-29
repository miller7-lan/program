package com.homeapp.profithelper.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/** 与桌面端 ui/theme.py 相同的配色。 */
object Palette {
    val Bg = Color(0xFFF3F4F8)
    val SurfaceAlt = Color(0xFFF8F9FC)
    val Border = Color(0xFFE4E7EF)
    val BorderStrong = Color(0xFFD5D9E5)
    val Text = Color(0xFF1D2130)
    val Text2 = Color(0xFF5B6175)
    val Text3 = Color(0xFF8C92A6)
    val Accent = Color(0xFF5B4FE0)
    val AccentDark = Color(0xFF4C40D0)
    val AccentSoft = Color(0xFFEEECFD)
    val Danger = Color(0xFFD9363E)
    val DangerSoft = Color(0xFFFDECEC)
    val Success = Color(0xFF1A9E5C)
    val SuccessSoft = Color(0xFFE6F6EE)
    val Warning = Color(0xFFC77700)
    val WarningSoft = Color(0xFFFFF4E0)
    val Amber = Color(0xFFFFC24B)
}

private val Scheme = lightColorScheme(
    primary = Palette.Accent,
    onPrimary = Color.White,
    primaryContainer = Palette.AccentSoft,
    onPrimaryContainer = Palette.AccentDark,
    secondary = Palette.Accent,
    onSecondary = Color.White,
    secondaryContainer = Palette.AccentSoft,
    onSecondaryContainer = Palette.AccentDark,
    tertiary = Palette.Amber,
    background = Palette.Bg,
    onBackground = Palette.Text,
    surface = Color.White,
    onSurface = Palette.Text,
    surfaceVariant = Color(0xFFF0F1F5),
    onSurfaceVariant = Palette.Text2,
    surfaceTint = Color.Transparent,
    outline = Palette.BorderStrong,
    outlineVariant = Palette.Border,
    error = Palette.Danger,
    onError = Color.White,
    errorContainer = Palette.DangerSoft,
    onErrorContainer = Palette.Danger,
    surfaceContainerLowest = Color.White,
    surfaceContainerLow = Color.White,
    surfaceContainer = Color.White,
    surfaceContainerHigh = Color.White,
    surfaceContainerHighest = Color(0xFFF0F1F5),
)

private val AppTypography = Typography().let { base ->
    base.copy(
        headlineSmall = base.headlineSmall.copy(fontWeight = FontWeight.Bold, fontSize = 22.sp),
        titleLarge = base.titleLarge.copy(fontWeight = FontWeight.Bold, fontSize = 20.sp),
        titleMedium = base.titleMedium.copy(fontWeight = FontWeight.Bold, fontSize = 16.sp),
        bodyMedium = base.bodyMedium.copy(fontSize = 14.sp),
        labelLarge = base.labelLarge.copy(fontWeight = FontWeight.SemiBold, fontSize = 14.sp),
    )
}

object TextStyles {
    val Caption = TextStyle(fontSize = 12.sp, color = Palette.Text3)
    val Body2 = TextStyle(fontSize = 13.sp, color = Palette.Text2)
    val Money = TextStyle(fontSize = 15.sp, fontWeight = FontWeight.Bold, color = Palette.Text)
    val BigMoney = TextStyle(fontSize = 22.sp, fontWeight = FontWeight.Bold, color = Palette.Text)
}

@Composable
fun ProfitTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = Scheme, typography = AppTypography, content = content)
}
