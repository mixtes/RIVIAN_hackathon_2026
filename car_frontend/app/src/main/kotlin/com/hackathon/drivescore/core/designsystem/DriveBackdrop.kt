package com.hackathon.drivescore.core.designsystem

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke

/**
 * Calm background for the Drive screen: a teal glow behind the content, concentric "monitoring"
 * rings with one slow ripple, and a sea-green horizon at the bottom. Static when animations are off.
 */
@Composable
fun DriveBackdrop(modifier: Modifier = Modifier) {
    val animate = rememberAnimationsEnabled()
    val t = rememberInfiniteTransition(label = "backdrop")
    val ripple by t.animateFloat(0f, 1f, infiniteRepeatable(tween(7_000, easing = LinearEasing), RepeatMode.Restart), label = "ripple")
    val breathe by t.animateFloat(0.85f, 1f, infiniteRepeatable(tween(4_000), RepeatMode.Reverse), label = "breathe")

    Canvas(modifier.fillMaxSize().clipToBounds()) {
        val c = Offset(size.width / 2, size.height * 0.46f)
        val unit = size.minDimension

        // Base glow behind the content.
        drawRect(
            Brush.radialGradient(
                listOf(CarColors.Teal.copy(alpha = 0.28f * (if (animate) breathe else 1f)), Color.Transparent),
                center = c, radius = unit * 0.85f,
            ),
        )
        // Sea-green horizon.
        drawRect(
            Brush.radialGradient(
                listOf(CarColors.SeaGreen.copy(alpha = 0.30f), Color.Transparent),
                center = Offset(size.width / 2, size.height * 1.2f), radius = size.width * 0.6f,
            ),
        )
        // Static rings, fading outward.
        val stroke = Stroke(width = 2f)
        listOf(0.30f, 0.46f, 0.62f, 0.80f).forEachIndexed { i, r ->
            drawCircle(CarColors.TealSoft.copy(alpha = 0.13f - i * 0.025f), radius = unit * r, center = c, style = stroke)
        }
        // One slow ripple travelling outward.
        if (animate) {
            val r = unit * (0.30f + 0.55f * ripple)
            drawCircle(CarColors.TealSoft.copy(alpha = 0.22f * (1f - ripple)), radius = r, center = c, style = Stroke(width = 3f))
        }
    }
}
