package com.hackathon.drivescore.core.designsystem

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Backspace
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.hackathon.drivescore.R
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

private val ButtonPadding = PaddingValues(horizontal = Space.L)

/** Main action: filled orange (or [color], e.g. red for destructive actions). */
@Composable
fun PrimaryButton(
    text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true,
    color: Color = CarColors.OrangeFill,
) {
    Button(
        onClick = onClick, enabled = enabled,
        modifier = modifier.heightIn(min = CarDimens.ButtonHeight).widthIn(min = CarDimens.ButtonMinWidth),
        colors = ButtonDefaults.buttonColors(
            containerColor = color, contentColor = Color.White,
            disabledContainerColor = CarColors.SurfaceHigh, disabledContentColor = CarColors.Muted,
        ),
        shape = CarShapes.Control, contentPadding = ButtonPadding,
    ) { Text(text, style = MaterialTheme.typography.labelLarge, maxLines = 1, overflow = TextOverflow.Ellipsis) }
}

/** Secondary action: tonal teal surface, same size as [PrimaryButton]. */
@Composable
fun SecondaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    Button(
        onClick = onClick, enabled = enabled,
        modifier = modifier.heightIn(min = CarDimens.ButtonHeight).widthIn(min = CarDimens.ButtonMinWidth),
        colors = ButtonDefaults.buttonColors(
            containerColor = CarColors.SurfaceHigh, contentColor = CarColors.OnDark,
            disabledContainerColor = CarColors.SurfaceHigh.copy(alpha = 0.5f), disabledContentColor = CarColors.Muted,
        ),
        border = BorderStroke(1.dp, CarColors.Outline),
        shape = CarShapes.Control, contentPadding = ButtonPadding,
    ) { Text(text, style = MaterialTheme.typography.labelLarge, maxLines = 1, overflow = TextOverflow.Ellipsis) }
}

/** Low-emphasis text action (e.g. "Forgot PIN?"). Still a full-size touch target. */
@Composable
fun GhostButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, color: Color = CarColors.TealSoft) {
    TextButton(onClick = onClick, modifier = modifier.heightIn(min = CarDimens.ButtonHeight), shape = CarShapes.Control, contentPadding = ButtonPadding) {
        Text(text, style = MaterialTheme.typography.labelLarge, color = color)
    }
}

/** Title row used at the top of every secondary screen, so all screens start at the same place. */
@Composable
fun ScreenHeader(title: String, modifier: Modifier = Modifier, onBack: (() -> Unit)? = null, trailing: @Composable RowScope.() -> Unit = {}) {
    Row(
        modifier.fillMaxWidth().heightIn(min = CarDimens.ButtonHeight),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.M),
    ) {
        if (onBack != null) {
            Box(
                Modifier.size(CarDimens.ButtonHeight).clip(CarShapes.Control).background(CarColors.SurfaceHigh)
                    .border(1.dp, CarColors.Outline, CarShapes.Control).clickable(onClick = onBack),
                contentAlignment = Alignment.Center,
            ) { Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.back), Modifier.size(36.dp), tint = CarColors.OnDark) }
        }
        Text(title, style = MaterialTheme.typography.headlineLarge, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
        trailing()
    }
}

/**
 * Standard screen frame: same outer padding everywhere, header, then content [Space.L] below it.
 * Use [scrollable] = false when the content has its own lazy list.
 */
@Composable
fun CarScreen(
    title: String? = null,
    onBack: (() -> Unit)? = null,
    scrollable: Boolean = true,
    trailing: @Composable RowScope.() -> Unit = {},
    content: @Composable ColumnScope.() -> Unit,
) {
    val scroll = if (scrollable) Modifier.verticalScroll(rememberScrollState()) else Modifier
    Column(Modifier.fillMaxSize().padding(CarDimens.ScreenPadding).then(scroll)) {
        if (title != null) {
            ScreenHeader(title, onBack = onBack, trailing = trailing)
            VSpace(Space.L)
        }
        content()
    }
}

fun scoreLabel(score: Double): String = when {
    score >= 85 -> "Excellent"
    score >= 60 -> "Good"
    else -> "Needs attention"
}

/** Circular score gauge with integer score; ≥85 green, 60–84 amber, <60 red (spec §6.4). */
@Composable
fun ScoreGauge(score: Double, size: Dp = 280.dp, modifier: Modifier = Modifier) {
    val color = CarColors.score(score)
    Box(modifier.size(size).semantics { contentDescription = "Score ${Formatters.score(score)} of 100" }, contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize()) {
            val stroke = this.size.minDimension * 0.07f
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
        modifier.heightIn(min = 56.dp).clip(CarShapes.Small).background(CarColors.score(score)).padding(horizontal = Space.M),
        contentAlignment = Alignment.Center,
    ) { Text(Formatters.score(score), color = CarColors.BackgroundDeep, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold) }
}

@Composable
fun PinDots(length: Int, filled: Int, modifier: Modifier = Modifier) {
    Row(modifier, horizontalArrangement = Arrangement.spacedBy(Space.L)) {
        repeat(length) { i ->
            val on = i < filled
            Box(
                Modifier.size(28.dp).clip(CircleShape)
                    .background(if (on) CarColors.Orange else Color.Transparent)
                    .border(3.dp, if (on) CarColors.Orange else CarColors.TealSoft, CircleShape),
            )
        }
    }
}

/** Large numeric keypad, keys ≥ 96 dp (spec §6.2). */
@Composable
fun Keypad(onDigit: (Char) -> Unit, onBackspace: () -> Unit, enabled: Boolean = true, modifier: Modifier = Modifier) {
    val rows = listOf("123", "456", "789")
    Column(modifier.alpha(if (enabled) 1f else 0.35f), verticalArrangement = Arrangement.spacedBy(Space.M)) {
        rows.forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(Space.M)) { row.forEach { KeypadKey(it.toString(), enabled) { onDigit(it) } } }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(Space.M)) {
            Spacer(Modifier.size(CarDimens.KeypadKey))
            KeypadKey("0", enabled) { onDigit('0') }
            Box(
                Modifier.size(CarDimens.KeypadKey).clip(CircleShape).clickable(enabled = enabled, onClick = onBackspace),
                contentAlignment = Alignment.Center,
            ) { Icon(Icons.AutoMirrored.Filled.Backspace, contentDescription = "Backspace", Modifier.size(40.dp), tint = CarColors.TealSoft) }
        }
    }
}

@Composable
private fun KeypadKey(label: String, enabled: Boolean, onClick: () -> Unit) {
    Box(
        Modifier.size(CarDimens.KeypadKey).clip(CircleShape).background(CarColors.SurfaceHigh)
            .border(1.dp, CarColors.Outline, CircleShape).clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) { Text(label, fontSize = 40.sp, color = CarColors.OnDark, fontWeight = FontWeight.Medium) }
}

@Composable
fun PulsingDot(color: Color, size: Dp = 20.dp) {
    val animate = rememberAnimationsEnabled()
    val t = rememberInfiniteTransition(label = "pulse")
    val a by t.animateFloat(0.35f, 1f, infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "a")
    Box(Modifier.size(size).alpha(if (animate) a else 1f).clip(CircleShape).background(color))
}

/** Read-only pill (mistake counts, status). */
@Composable
fun Chip(text: String, color: Color = CarColors.SurfaceHigh, textColor: Color = CarColors.OnDark, modifier: Modifier = Modifier) {
    Surface(color = color, shape = CarShapes.Pill, border = BorderStroke(1.dp, CarColors.Outline), modifier = modifier.heightIn(min = 56.dp)) {
        Box(Modifier.padding(horizontal = Space.L), contentAlignment = Alignment.Center) {
            Text(text, color = textColor, style = MaterialTheme.typography.bodyMedium)
        }
    }
}

/** Selectable pill for periods and filters. Selected = solid orange. */
@Composable
fun SelectChip(text: String, selected: Boolean, onClick: () -> Unit, modifier: Modifier = Modifier) {
    Box(
        modifier.heightIn(min = CarDimens.ChipHeight).clip(CarShapes.Pill)
            .background(if (selected) CarColors.OrangeFill else CarColors.SurfaceHigh)
            .border(1.dp, if (selected) CarColors.OrangeFill else CarColors.Outline, CarShapes.Pill)
            .clickable(onClick = onClick).padding(horizontal = Space.L),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, style = MaterialTheme.typography.labelLarge, color = if (selected) Color.White else CarColors.OnDark)
    }
}

/** Inline information strip (offline, last updated…). [accent] colors the leading bar. */
@Composable
fun Banner(text: String, color: Color = CarColors.SurfaceHigh, modifier: Modifier = Modifier, accent: Color = CarColors.Teal) {
    Surface(color = color, shape = CarShapes.Small, modifier = modifier.fillMaxWidth()) {
        Row(Modifier.height(IntrinsicSize.Min).heightIn(min = 64.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.width(6.dp).fillMaxHeight().background(accent))
            Text(text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(horizontal = Space.L, vertical = Space.M), color = CarColors.OnDark)
        }
    }
}

@Composable
fun EmptyState(title: String, body: String? = null, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth().padding(Space.XXL), horizontalAlignment = Alignment.CenterHorizontally) {
        Text(title, style = MaterialTheme.typography.titleLarge, textAlign = TextAlign.Center)
        if (body != null) {
            VSpace(Space.S)
            Text(body, style = MaterialTheme.typography.bodyLarge, color = CarColors.Muted, textAlign = TextAlign.Center)
        }
    }
}

/** The one card used everywhere: same radius, border and inner padding. Optional [title] and [onClick]. */
@Composable
fun SectionCard(
    modifier: Modifier = Modifier,
    title: String? = null,
    onClick: (() -> Unit)? = null,
    highlight: Boolean = false,
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(
        color = CarColors.Surface, shape = CarShapes.Card,
        border = BorderStroke(if (highlight) 2.dp else 1.dp, if (highlight) CarColors.Orange else CarColors.Outline),
        modifier = modifier.clip(CarShapes.Card).then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier),
    ) {
        Column(Modifier.padding(CarDimens.CardPadding)) {
            if (title != null) {
                CardTitle(title)
                VSpace(Space.M)
            }
            content()
        }
    }
}

/** Small muted label used as card and field titles. */
@Composable
fun CardTitle(text: String, color: Color = CarColors.Muted) {
    Text(text, style = MaterialTheme.typography.labelSmall, color = color, maxLines = 1, overflow = TextOverflow.Ellipsis)
}

@Composable
fun KpiTile(label: String, value: String, modifier: Modifier = Modifier, valueColor: Color = CarColors.OnDark, big: Boolean = false) {
    SectionCard(modifier.height(CarDimens.KpiHeight)) {
        CardTitle(label)
        Spacer(Modifier.weight(1f))
        Text(
            value, style = if (big) MaterialTheme.typography.displayMedium else MaterialTheme.typography.displaySmall,
            color = valueColor, maxLines = 1, overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
fun HSpace(w: Dp) = Spacer(Modifier.width(w))

@Composable
fun VSpace(h: Dp) = Spacer(Modifier.height(h))

/** Text-field colors shared by all forms: teal outline at rest, orange when focused. */
@Composable
fun carFieldColors() = OutlinedTextFieldDefaults.colors(
    focusedBorderColor = CarColors.Orange, unfocusedBorderColor = CarColors.Outline,
    focusedLabelColor = CarColors.Orange, unfocusedLabelColor = CarColors.Muted,
    cursorColor = CarColors.Orange,
    focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent,
    focusedTextColor = CarColors.OnDark, unfocusedTextColor = CarColors.OnDark,
)
