package com.hackathon.drivescore.core.designsystem

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.hackathon.drivescore.core.model.AlertLevel
import com.hackathon.drivescore.service.DisplayedAlert

/**
 * VISUAL/AUDIO: amber top banner; HAPTIC: red pulsing banner; CRITICAL: full-screen red pulsing overlay
 * (spec §6.9). Never needs a tap to dismiss. Pulsing respects "remove animations" (spec §14).
 */
@Composable
fun AlertOverlay(alert: DisplayedAlert?) {
    if (alert == null) return
    val animationsOff = !rememberAnimationsEnabled()
    val t = rememberInfiniteTransition(label = "alert")
    val pulse by t.animateFloat(0.55f, 1f, infiniteRepeatable(tween(500), RepeatMode.Reverse), label = "p")
    val p = if (animationsOff) 1f else pulse
    when (alert.level) {
        AlertLevel.VISUAL, AlertLevel.AUDIO -> AlertBanner(alert, CarColors.Amber, Color.Black)
        AlertLevel.HAPTIC -> AlertBanner(alert, lerp(Color(0xFF8E1B1F), CarColors.Red, p), Color.White) // opaque pulse
        AlertLevel.CRITICAL -> Box(
            Modifier.fillMaxSize().background(CarColors.Red.copy(alpha = 0.55f))
                .border((12 * p).dp, CarColors.Red)
                .semantics { liveRegion = LiveRegionMode.Assertive },
            contentAlignment = Alignment.Center,
        ) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(alert.icon, fontSize = 140.sp)
                Text(
                    alert.text, fontSize = 64.sp, fontWeight = FontWeight.Bold, color = Color.White,
                    textAlign = TextAlign.Center, modifier = Modifier.padding(horizontal = 48.dp),
                )
            }
        }
    }
}

@Composable
private fun AlertBanner(alert: DisplayedAlert, bg: Color, fg: Color) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 120.dp).background(bg).padding(horizontal = 32.dp, vertical = 16.dp)
            .semantics { liveRegion = LiveRegionMode.Assertive },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(alert.icon, fontSize = 56.sp)
        Text(alert.text, fontSize = 40.sp, fontWeight = FontWeight.Bold, color = fg, modifier = Modifier.padding(start = 24.dp))
    }
}
