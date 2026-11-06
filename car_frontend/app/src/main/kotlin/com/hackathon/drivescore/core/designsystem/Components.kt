package com.hackathon.drivescore.core.designsystem

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Backspace
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.hackathon.drivescore.core.common.Formatters

@Composable
fun Avatar(initials: String, colorHex: String, size: Dp = 96.dp, modifier: Modifier = Modifier) {
    Box(
        modifier.size(size).clip(CircleShape).background(CarColors.parse(colorHex)),
        contentAlignment = Alignment.Center,
    ) {
        Text(initials, color = Color.White, fontSize = (size.value * 0.38f).sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
fun PrimaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, color: Color = CarColors.Accent) {
    Button(
        onClick = onClick, enabled = enabled,
        modifier = modifier.heightIn(min = CarDimens.MinTouch),
        colors = ButtonDefaults.buttonColors(containerColor = color),
        shape = RoundedCornerShape(16.dp),
    ) { Text(text, style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(horizontal = 12.dp)) }
}

@Composable
fun SecondaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    OutlinedButton(
        onClick = onClick, enabled = enabled,
        modifier = modifier.heightIn(min = CarDimens.MinTouch), shape = RoundedCornerShape(16.dp),
    ) { Text(text, style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(horizontal = 12.dp)) }
}

fun scoreLabel(score: Double): String = when {
    score >= 85 -> "Excellent"
    score >= 60 -> "Good"
    else -> "Needs attention"
}

/** Circular score gauge with integer score; ≥85 green, 60–84 amber, <60 red (spec §6.4). */
@Composable
fun ScoreGauge(score: Double, size: Dp = 260.dp, modifier: Modifier = Modifier) {
    val color = CarColors.score(score)
    Box(modifier.size(size).semantics { contentDescription = "Score ${Formatters.score(score)} of 100" }, contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize()) {
            val stroke = this.size.minDimension * 0.08f
            val inset = stroke / 2
            val arcSize = Size(this.size.width - stroke, this.size.height - stroke)
            drawArc(CarColors.SurfaceHigh, 135f, 270f, false, Offset(inset, inset), arcSize, style = Stroke(stroke, cap = StrokeCap.Round))
            drawArc(color, 135f, (270f * (score / 100.0).toFloat()).coerceIn(0f, 270f), false, Offset(inset, inset), arcSize, style = Stroke(stroke, cap = StrokeCap.Round))
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(Formatters.score(score), style = MaterialTheme.typography.displayLarge, color = color)
            Text(scoreLabel(score), style = MaterialTheme.typography.titleMedium, color = CarColors.OnDark)
        }
    }
}

@Composable
fun ScoreBadge(score: Double, modifier: Modifier = Modifier) {
    Box(
        modifier.clip(RoundedCornerShape(12.dp)).background(CarColors.score(score)).padding(horizontal = 16.dp, vertical = 6.dp),
        contentAlignment = Alignment.Center,
    ) { Text(Formatters.score(score), color = Color.Black, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold) }
}

@Composable
fun PinDots(length: Int, filled: Int, modifier: Modifier = Modifier) {
    Row(modifier, horizontalArrangement = Arrangement.spacedBy(20.dp)) {
        repeat(length) { i ->
            Box(
                Modifier.size(28.dp).clip(CircleShape)
                    .background(if (i < filled) CarColors.OnDark else Color.Transparent)
                    .border(3.dp, CarColors.OnDark, CircleShape),
            )
        }
    }
}

/** Large numeric keypad, keys ≥ 96 dp (spec §6.2). */
@Composable
fun Keypad(onDigit: (Char) -> Unit, onBackspace: () -> Unit, enabled: Boolean = true, modifier: Modifier = Modifier) {
    val rows = listOf("123", "456", "789")
    Column(modifier.alpha(if (enabled) 1f else 0.35f), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        rows.forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) { row.forEach { KeypadKey(it.toString(), enabled) { onDigit(it) } } }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Spacer(Modifier.size(CarDimens.KeypadKey))
            KeypadKey("0", enabled) { onDigit('0') }
            Box(
                Modifier.size(CarDimens.KeypadKey).clip(CircleShape).clickable(enabled = enabled, onClick = onBackspace),
                contentAlignment = Alignment.Center,
            ) { Icon(Icons.AutoMirrored.Filled.Backspace, contentDescription = "Backspace", Modifier.size(40.dp), tint = CarColors.OnDark) }
        }
    }
}

@Composable
private fun KeypadKey(label: String, enabled: Boolean, onClick: () -> Unit) {
    Box(
        Modifier.size(CarDimens.KeypadKey).clip(CircleShape).background(CarColors.SurfaceHigh).clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) { Text(label, fontSize = 40.sp, color = CarColors.OnDark, fontWeight = FontWeight.Medium) }
}

@Composable
fun PulsingDot(color: Color, size: Dp = 20.dp) {
    val t = rememberInfiniteTransition(label = "pulse")
    val a by t.animateFloat(0.3f, 1f, infiniteRepeatable(tween(800), RepeatMode.Reverse), label = "a")
    Box(Modifier.size(size).alpha(a).clip(CircleShape).background(color))
}

@Composable
fun Chip(text: String, color: Color = CarColors.SurfaceHigh, textColor: Color = CarColors.OnDark, modifier: Modifier = Modifier) {
    Surface(color = color, shape = RoundedCornerShape(24.dp), modifier = modifier) {
        Text(text, color = textColor, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(horizontal = 18.dp, vertical = 8.dp))
    }
}

@Composable
fun Banner(text: String, color: Color = CarColors.SurfaceHigh, modifier: Modifier = Modifier) {
    Surface(color = color, shape = RoundedCornerShape(12.dp), modifier = modifier.fillMaxWidth()) {
        Text(text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(16.dp), color = CarColors.OnDark)
    }
}

@Composable
fun EmptyState(title: String, body: String? = null, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth().padding(48.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Text(title, style = MaterialTheme.typography.titleLarge, textAlign = TextAlign.Center)
        if (body != null) {
            Spacer(Modifier.height(12.dp))
            Text(body, style = MaterialTheme.typography.bodyLarge, color = CarColors.Muted, textAlign = TextAlign.Center)
        }
    }
}

@Composable
fun SectionCard(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Surface(color = CarColors.Surface, shape = RoundedCornerShape(20.dp), modifier = modifier) {
        Box(Modifier.padding(20.dp)) { content() }
    }
}

@Composable
fun KpiTile(label: String, value: String, modifier: Modifier = Modifier, valueColor: Color = CarColors.OnDark, big: Boolean = false) {
    SectionCard(modifier) {
        Column {
            Text(label, style = MaterialTheme.typography.bodyMedium, color = CarColors.Muted)
            Text(value, style = if (big) MaterialTheme.typography.displayMedium else MaterialTheme.typography.displaySmall, color = valueColor)
        }
    }
}

@Composable
fun HSpace(w: Dp) = Spacer(Modifier.width(w))

@Composable
fun VSpace(h: Dp) = Spacer(Modifier.height(h))
