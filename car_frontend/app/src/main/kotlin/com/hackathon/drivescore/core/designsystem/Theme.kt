package com.hackathon.drivescore.core.designsystem

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Car theme (spec §14): dark by default; large type ramp; ≥ 76 dp targets. */
object CarColors {
    val Background = Color(0xFF0E1116)
    val Surface = Color(0xFF1A1F27)
    val SurfaceHigh = Color(0xFF252B35)
    val Accent = Color(0xFF4F7DF3)
    val Green = Color(0xFF2BB673)
    val Amber = Color(0xFFF5A524)
    val Red = Color(0xFFE5484D)
    val OnDark = Color(0xFFF2F4F8)
    val Muted = Color(0xFF9AA4B2)

    fun score(score: Double): Color = when {
        score >= 85 -> Green
        score >= 60 -> Amber
        else -> Red
    }

    fun parse(hex: String, fallback: Color = Accent): Color = try {
        Color(android.graphics.Color.parseColor(hex))
    } catch (_: Exception) {
        fallback
    }
}

object CarDimens {
    val MinTouch = 76.dp
    val KeypadKey = 96.dp
    val Gutter = 24.dp
}

private val CarTypography = Typography(
    displayLarge = TextStyle(fontSize = 96.sp, fontWeight = FontWeight.Bold),
    displayMedium = TextStyle(fontSize = 64.sp, fontWeight = FontWeight.Bold),
    displaySmall = TextStyle(fontSize = 48.sp, fontWeight = FontWeight.SemiBold),
    headlineLarge = TextStyle(fontSize = 40.sp, fontWeight = FontWeight.SemiBold),
    headlineMedium = TextStyle(fontSize = 32.sp, fontWeight = FontWeight.SemiBold),
    titleLarge = TextStyle(fontSize = 32.sp, fontWeight = FontWeight.SemiBold),
    titleMedium = TextStyle(fontSize = 26.sp, fontWeight = FontWeight.Medium),
    bodyLarge = TextStyle(fontSize = 24.sp),
    bodyMedium = TextStyle(fontSize = 22.sp),
    bodySmall = TextStyle(fontSize = 18.sp),
    labelLarge = TextStyle(fontSize = 24.sp, fontWeight = FontWeight.Medium),
    labelMedium = TextStyle(fontSize = 20.sp),
)

@Composable
fun CarTheme(content: @Composable () -> Unit) {
    // MVP: dark theme only (a light day-mode variant is future work, see README).
    val scheme = darkColorScheme(
        primary = CarColors.Accent, onPrimary = Color.White, background = CarColors.Background, surface = CarColors.Surface,
        surfaceVariant = CarColors.SurfaceHigh, onBackground = CarColors.OnDark, onSurface = CarColors.OnDark,
        onSurfaceVariant = CarColors.Muted, error = CarColors.Red,
    )
    MaterialTheme(colorScheme = scheme, typography = CarTypography, content = content)
}
