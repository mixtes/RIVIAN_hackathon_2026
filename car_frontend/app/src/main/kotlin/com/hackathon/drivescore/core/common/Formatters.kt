package com.hackathon.drivescore.core.common

import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.roundToInt

/** Formatting rules from spec §14. */
object Formatters {
    /** Score is always shown as an integer. */
    fun score(score: Double): String = score.roundToInt().toString()

    /** Mistake durations: `s` below 60 s, `m:ss` above (spec §6.5). */
    fun shortDuration(seconds: Double?): String {
        if (seconds == null) return "–"
        if (seconds < 60) {
            val rounded = (seconds * 10).roundToInt() / 10.0
            return if (rounded % 1.0 == 0.0) "${rounded.toInt()} s" else "$rounded s"
        }
        val total = seconds.roundToInt()
        return "%d:%02d".format(total / 60, total % 60)
    }

    /** Driving time as h:mm. */
    fun drivingTime(seconds: Long): String {
        val minutes = seconds / 60
        return "%d:%02d".format(minutes / 60, minutes % 60)
    }

    /** Elapsed session time as h:mm:ss. */
    fun elapsed(ms: Long): String {
        val s = (ms / 1000).coerceAtLeast(0)
        return "%d:%02d:%02d".format(s / 3600, (s / 60) % 60, s % 60)
    }

    private val timeFmt = DateTimeFormatter.ofPattern("HH:mm")
    private val dayFmt = DateTimeFormatter.ofPattern("dd MMM", Locale.getDefault())

    /** Relative dates: "Today 08:34", "Yesterday 08:34", then "dd MMM 08:34". */
    fun relativeDateTime(epochMs: Long, nowMs: Long = System.currentTimeMillis(), zone: ZoneId = ZoneId.systemDefault()): String {
        val dt = Instant.ofEpochMilli(epochMs).atZone(zone)
        val today = Instant.ofEpochMilli(nowMs).atZone(zone).toLocalDate()
        val date: LocalDate = dt.toLocalDate()
        val prefix = when (date) {
            today -> "Today"
            today.minusDays(1) -> "Yesterday"
            else -> dt.format(dayFmt)
        }
        return "$prefix ${dt.format(timeFmt)}"
    }

    fun minutesAgo(fetchedAt: Long, nowMs: Long = System.currentTimeMillis()): String {
        val minutes = ((nowMs - fetchedAt) / 60_000).coerceAtLeast(0)
        return when {
            minutes < 1 -> "just now"
            minutes < 60 -> "$minutes min ago"
            minutes < 48 * 60 -> "${minutes / 60} h ago"
            else -> "${minutes / (24 * 60)} days ago"
        }
    }

    fun flagEmoji(regionCode: String): String {
        if (regionCode.length != 2 || !regionCode.all { it.isLetter() }) return "🌍"
        val up = regionCode.uppercase()
        return String(Character.toChars(0x1F1E6 + (up[0] - 'A'))) + String(Character.toChars(0x1F1E6 + (up[1] - 'A')))
    }
}
