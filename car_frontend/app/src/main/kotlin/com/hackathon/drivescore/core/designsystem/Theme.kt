package com.hackathon.drivescore.core.designsystem

import android.provider.Settings
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.hackathon.drivescore.core.model.MistakeSeverity

/**
 * Car theme (spec §14): dark "deep teal" surfaces (blue with a green undertone) and a warm
 * Claude-like orange as the single action accent. Green / amber / red are reserved for scores and alerts.
 */
object CarColors {
    // Surfaces: dark blue-teal, from deepest to most raised.
    val BackgroundDeep = Color(0xFF06141A)
    val Background = Color(0xFF0A1C22)
    val Surface = Color(0xFF10272E)
    val SurfaceHigh = Color(0xFF17343C)
    val Outline = Color(0xFF264A52)

    // Cool brand tones.
    val Teal = Color(0xFF3A9FAE)
    val TealSoft = Color(0xFF8FD3D1)
    val TealDeep = Color(0xFF1C4B54)
    val SeaGreen = Color(0xFF2E9C86)

    // Warm action accent (Claude-like orange). `OrangeFill` is the button fill: white text stays readable on it.
    val Orange = Color(0xFFE08A67)
    val OrangeFill = Color(0xFFC96442)

    // Text.
    val OnDark = Color(0xFFECF4F3)
    val Muted = Color(0xFF93AEB0)

    // Scores and alerts only.
    val Green = Color(0xFF45C08A)
    val Amber = Color(0xFFF2B33D)
    val Red = Color(0xFFE5534B)

    /** Kept for existing call sites: the accent is the orange. */
    val Accent = Orange

    /** App-wide background: a gentle top-to-bottom fade. */
    val backgroundBrush: Brush get() = Brush.verticalGradient(listOf(Background, BackgroundDeep))

    fun score(score: Double): Color = when {
        score >= 85 -> Green
        score >= 60 -> Amber
        else -> Red
    }

    fun severity(s: MistakeSeverity): Color = when (s) {
        MistakeSeverity.GOOD -> Green
        MistakeSeverity.WARN -> Amber
        MistakeSeverity.BAD -> Red
    }

    fun parse(hex: String, fallback: Color = Teal): Color = try {
        Color(android.graphics.Color.parseColor(hex))
    } catch (_: Exception) {
        fallback
    }
}

/** Spacing scale. Every margin, padding and gap in the app uses one of these. */
object Space {
    val XS = 8.dp
    val S = 12.dp
    val M = 16.dp
    val L = 24.dp
    val XL = 32.dp
    val XXL = 48.dp
}

object CarDimens {
    val MinTouch = 76.dp
    val ButtonHeight = 80.dp
    val ButtonMinWidth = 220.dp
    val KeypadKey = 96.dp
    val Gutter = Space.L
    val ScreenPadding = Space.XL
    val CardPadding = Space.L
    val StatusBarHeight = 72.dp
    val ChipHeight = 64.dp
    val ProfileTileWidth = 240.dp
    val ProfileTileHeight = 248.dp
    val KpiHeight = 156.dp
}

object CarShapes {
    val Card = RoundedCornerShape(24.dp)
    val Control = RoundedCornerShape(20.dp)
    val Small = RoundedCornerShape(12.dp)
    val Pill = RoundedCornerShape(percent = 50)
}

private val CarTypography = Typography(
    displayLarge = TextStyle(fontSize = 96.sp, fontWeight = FontWeight.Bold, letterSpacing = (-1).sp),
    displayMedium = TextStyle(fontSize = 64.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.5).sp),
    displaySmall = TextStyle(fontSize = 48.sp, fontWeight = FontWeight.SemiBold),
    headlineLarge = TextStyle(fontSize = 40.sp, fontWeight = FontWeight.SemiBold),
    headlineMedium = TextStyle(fontSize = 32.sp, fontWeight = FontWeight.SemiBold),
    titleLarge = TextStyle(fontSize = 32.sp, fontWeight = FontWeight.SemiBold),
    titleMedium = TextStyle(fontSize = 26.sp, fontWeight = FontWeight.Medium),
    bodyLarge = TextStyle(fontSize = 24.sp, lineHeight = 32.sp),
    bodyMedium = TextStyle(fontSize = 22.sp, lineHeight = 30.sp),
    bodySmall = TextStyle(fontSize = 18.sp, lineHeight = 24.sp),
    labelLarge = TextStyle(fontSize = 24.sp, fontWeight = FontWeight.SemiBold),
    labelMedium = TextStyle(fontSize = 20.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 3.sp),
    labelSmall = TextStyle(fontSize = 20.sp, fontWeight = FontWeight.Medium),
)

private val CarMaterialShapes = Shapes(
    extraSmall = CarShapes.Small, small = CarShapes.Small, medium = CarShapes.Control,
    large = CarShapes.Card, extraLarge = CarShapes.Card,
)

/** True unless the system "remove animations" setting is on (spec §14). */
@Composable
fun rememberAnimationsEnabled(): Boolean {
    val context = LocalContext.current
    return remember(context) {
        Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) != 0f
    }
}

@Composable
fun CarTheme(content: @Composable () -> Unit) {
    // MVP: dark theme only (a light day-mode variant is future work, see README).
    val scheme = darkColorScheme(
        primary = CarColors.OrangeFill, onPrimary = Color.White,
        primaryContainer = CarColors.OrangeFill, onPrimaryContainer = Color.White,
        secondary = CarColors.Teal, onSecondary = CarColors.BackgroundDeep,
        secondaryContainer = CarColors.TealDeep, onSecondaryContainer = CarColors.OnDark,
        tertiary = CarColors.TealSoft, onTertiary = CarColors.BackgroundDeep,
        background = CarColors.Background, onBackground = CarColors.OnDark,
        surface = CarColors.Surface, onSurface = CarColors.OnDark,
        surfaceVariant = CarColors.SurfaceHigh, onSurfaceVariant = CarColors.Muted,
        surfaceContainerLowest = CarColors.BackgroundDeep, surfaceContainerLow = CarColors.Background,
        surfaceContainer = CarColors.Surface, surfaceContainerHigh = CarColors.Surface,
        surfaceContainerHighest = CarColors.SurfaceHigh,
        outline = CarColors.Outline, outlineVariant = CarColors.Outline,
        error = CarColors.Red, onError = Color.White,
    )
    MaterialTheme(colorScheme = scheme, typography = CarTypography, shapes = CarMaterialShapes, content = content)
}
