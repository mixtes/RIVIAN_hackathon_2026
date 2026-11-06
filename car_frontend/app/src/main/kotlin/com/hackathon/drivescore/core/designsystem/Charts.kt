package com.hackathon.drivescore.core.designsystem

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.hackathon.drivescore.core.common.Formatters
import com.hackathon.drivescore.core.model.DriveEvent
import com.hackathon.drivescore.core.model.MistakeTypes

data class TrendPoint(val score: Double?, val driveCount: Int)

/** Weighted score per bucket as a line, drive count as faint bars (spec §6.7). Canvas only. */
@Composable
fun TrendChart(points: List<TrendPoint>, modifier: Modifier = Modifier) {
    val maxCount = (points.maxOfOrNull { it.driveCount } ?: 0).coerceAtLeast(1)
    Canvas(modifier.fillMaxWidth().height(220.dp).semantics { contentDescription = "Score trend chart" }) {
        if (points.isEmpty()) return@Canvas
        val w = size.width
        val h = size.height
        val step = w / points.size
        // grid lines at 60 / 85
        listOf(60.0, 85.0).forEach { v ->
            val y = h - (v / 100.0 * h).toFloat()
            drawLine(CarColors.SurfaceHigh, Offset(0f, y), Offset(w, y), strokeWidth = 2f)
        }
        points.forEachIndexed { i, p ->
            if (p.driveCount > 0) {
                val bh = h * 0.35f * p.driveCount / maxCount
                drawRect(CarColors.Teal.copy(alpha = 0.30f), Offset(i * step + step * 0.2f, h - bh), Size(step * 0.6f, bh))
            }
        }
        val path = Path()
        var started = false
        points.forEachIndexed { i, p ->
            val s = p.score ?: run { started = false; return@forEachIndexed }
            val x = i * step + step / 2
            val y = h - (s / 100.0 * h).toFloat()
            if (!started) { path.moveTo(x, y); started = true } else path.lineTo(x, y)
            drawCircle(CarColors.score(s), 9f, Offset(x, y))
        }
        drawPath(path, CarColors.TealSoft, style = Stroke(5f))
    }
}

/** Horizontal bars per mistake type (count) with a per-hour figure (spec §6.7). */
@Composable
fun MistakeBars(rows: List<Triple<String, Int, Double?>>, modifier: Modifier = Modifier) {
    val max = (rows.maxOfOrNull { it.second } ?: 0).coerceAtLeast(1)
    Column(modifier) {
        rows.forEach { (type, count, perHour) ->
            val info = MistakeTypes.info(type)
            Row(Modifier.fillMaxWidth().padding(vertical = Space.XS), verticalAlignment = Alignment.CenterVertically) {
                Text("${info.icon} ${info.label}", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.width(300.dp))
                Box(Modifier.weight(1f).height(28.dp).clip(CarShapes.Pill).background(CarColors.SurfaceHigh)) {
                    Box(
                        Modifier.fillMaxWidth(count.toFloat() / max).height(28.dp).clip(CarShapes.Pill)
                            .background(CarColors.Orange),
                    )
                }
                Text(
                    "$count" + (perHour?.let { " · %.1f/h".format(it) } ?: ""),
                    style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(start = Space.M).width(180.dp),
                )
            }
        }
    }
}

private val timelineColors = listOf(
    Color(0xFFE08A67), Color(0xFFE5534B), Color(0xFF5FB3E8), Color(0xFFB79CF0),
    Color(0xFF45C08A), Color(0xFFF2B33D), Color(0xFF8FD3D1), Color(0xFFF0A5C0),
)

fun timelineColor(type: String): Color {
    val idx = MistakeTypes.known.indexOfFirst { it.type == type }
    return timelineColors[(if (idx >= 0) idx else type.hashCode().mod(timelineColors.size))]
}

/** Horizontal bar across the drive with colored marks; tap a mark for details (spec §6.5). */
@Composable
fun DriveTimeline(startedAt: Long, endedAt: Long, events: List<DriveEvent>, modifier: Modifier = Modifier) {
    var selected by remember { mutableStateOf<DriveEvent?>(null) }
    val total = (endedAt - startedAt).coerceAtLeast(1)
    Column(modifier) {
        Canvas(
            Modifier.fillMaxWidth().height(56.dp).pointerInput(events) {
                detectTapGestures { pos ->
                    val t = startedAt + (pos.x / size.width * total).toLong()
                    selected = events.minByOrNull { kotlin.math.abs(it.startedAt - t) }
                }
            }.semantics { contentDescription = "Drive timeline with ${events.size} events" },
        ) {
            drawRoundRect(CarColors.SurfaceHigh, Offset(0f, size.height * 0.35f), Size(size.width, size.height * 0.3f), CornerRadius(size.height * 0.15f))
            events.forEach { e ->
                val x = ((e.startedAt - startedAt).toFloat() / total) * size.width
                val w = (((e.durationS ?: 0.5) * 1000) / total * size.width).toFloat().coerceAtLeast(6f)
                drawRoundRect(timelineColor(MistakeTypes.normalize(e.type)), Offset(x, 0f), Size(w, size.height), CornerRadius(4f))
            }
        }
        selected?.let { e ->
            val info = MistakeTypes.info(e.type)
            Text(
                "${info.icon} ${info.label} · at ${Formatters.elapsed(e.startedAt - startedAt)} · ${Formatters.shortDuration(e.durationS)}",
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(top = Space.S).clickable { selected = null },
            )
        }
    }
}
