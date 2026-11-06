package com.hackathon.drivescore.service

import com.hackathon.drivescore.core.model.AlertLevel
import com.hackathon.drivescore.core.model.EngineAlert
import com.hackathon.drivescore.core.model.MistakeTypes

/** What the overlay shows. */
data class DisplayedAlert(
    val alertId: String,
    val type: String,
    val level: AlertLevel,
    val text: String,
    val icon: String,
)

enum class SoundKind { CHIME, DOUBLE_CHIME, ALARM }

/** Side effects the presenter executes (sound, TTS, notifications). */
sealed interface AlertEffect {
    data class PlaySound(val kind: SoundKind) : AlertEffect
    data class Speak(val text: String) : AlertEffect
    data class Notify(val alert: DisplayedAlert) : AlertEffect
    data class CancelNotification(val alertId: String) : AlertEffect
}

/**
 * Pure alert logic from spec §9 (no Android): one visible alert at a time, priority
 * CRITICAL > HAPTIC > AUDIO > VISUAL with preemption and resume, in-place escalation by alert_id,
 * a 10 s per-type sound cooldown after a clear, min/max display times, and CRITICAL repeats.
 */
class AlertQueue(
    private val cooldownMs: Long = 10_000,
    private val minDisplayMs: Long = 2_000,
    private val criticalSoundEveryMs: Long = 2_000,
    private val criticalSpeakEveryMs: Long = 6_000,
) {
    private data class Entry(
        val alert: EngineAlert,
        val firstShownAt: Long,
        val updatedAt: Long,
        val clearRequestedAt: Long? = null,
        val playedLevel: AlertLevel? = null,
    )

    private val entries = LinkedHashMap<String, Entry>()
    private val lastClearByType = HashMap<String, Pair<Long, AlertLevel?>>()
    private var lastCriticalSoundAt = Long.MIN_VALUE
    private var lastCriticalSpeakAt = Long.MIN_VALUE

    var displayed: DisplayedAlert? = null
        private set

    fun onAlert(alert: EngineAlert, now: Long): List<AlertEffect> {
        val effects = mutableListOf<AlertEffect>()
        val existing = entries[alert.alertId]
        if (existing != null) {
            val escalated = alert.level.rank > existing.alert.level.rank
            // Never downgrade an occurrence; keep the highest level seen.
            val merged = if (alert.level.rank < existing.alert.level.rank) alert.copy(level = existing.alert.level) else alert
            var e = existing.copy(alert = merged, updatedAt = now, clearRequestedAt = null)
            if (escalated) {
                effects += soundsFor(merged)
                e = e.copy(playedLevel = merged.level)
            }
            entries[alert.alertId] = e
        } else {
            val muted = isMuted(alert, now)
            entries[alert.alertId] = Entry(alert, now, now, playedLevel = if (muted) lastClearByType[alert.type]?.second else alert.level)
            if (!muted) effects += soundsFor(alert)
        }
        if (alert.level == AlertLevel.CRITICAL && (existing == null || existing.alert.level != AlertLevel.CRITICAL)) {
            lastCriticalSoundAt = now
            lastCriticalSpeakAt = now
        }
        effects += recompute(now)
        return effects
    }

    fun onClear(alertId: String, now: Long): List<AlertEffect> {
        val e = entries[alertId] ?: return emptyList()
        entries[alertId] = e.copy(clearRequestedAt = now)
        return tick(now)
    }

    /** Clears everything (session ended). */
    fun clearAll(now: Long): List<AlertEffect> {
        val ids = entries.keys.toList()
        ids.forEach { id -> entries[id]?.let { remember(it, now) } }
        entries.clear()
        val effects = ids.map { AlertEffect.CancelNotification(it) }.toMutableList<AlertEffect>()
        effects += recompute(now)
        return effects
    }

    /** Call periodically (e.g. every 250 ms): expiry, delayed clears, CRITICAL repeats. */
    fun tick(now: Long): List<AlertEffect> {
        val effects = mutableListOf<AlertEffect>()
        val it = entries.values.iterator()
        val removed = mutableListOf<Entry>()
        while (it.hasNext()) {
            val e = it.next()
            val clearAt = e.clearRequestedAt
            val minUntil = e.firstShownAt + minDisplayFor(e.alert.level)
            val expired = now - e.updatedAt >= maxDisplayMs(e.alert)
            val cleared = clearAt != null && now >= minUntil
            if (expired || cleared) {
                removed += e
                it.remove()
            }
        }
        removed.forEach { e ->
            remember(e, now)
            effects += AlertEffect.CancelNotification(e.alert.alertId)
        }
        effects += recompute(now)
        val d = displayed
        if (d != null && d.level == AlertLevel.CRITICAL) {
            if (now - lastCriticalSoundAt >= criticalSoundEveryMs) {
                lastCriticalSoundAt = now
                effects += AlertEffect.PlaySound(SoundKind.ALARM)
            }
            if (now - lastCriticalSpeakAt >= criticalSpeakEveryMs) {
                lastCriticalSpeakAt = now
                effects += AlertEffect.Speak(d.text)
            }
        }
        return effects
    }

    private fun remember(e: Entry, now: Long) {
        lastClearByType[e.alert.type] = now to (e.playedLevel ?: e.alert.level)
    }

    private fun isMuted(alert: EngineAlert, now: Long): Boolean {
        val (clearedAt, playedLevel) = lastClearByType[alert.type] ?: return false
        if (now - clearedAt >= cooldownMs) return false
        return playedLevel != null && alert.level.rank <= playedLevel.rank
    }

    private fun recompute(now: Long): List<AlertEffect> {
        val top = entries.values
            .filter { it.clearRequestedAt == null || now < it.firstShownAt + minDisplayFor(it.alert.level) }
            .maxWithOrNull(compareBy<Entry>({ it.alert.level.rank }, { it.updatedAt }))
        val newDisplayed = top?.let {
            val info = MistakeTypes.info(it.alert.type)
            DisplayedAlert(it.alert.alertId, it.alert.type, it.alert.level, MistakeTypes.alertText(it.alert.type, it.alert.message), info.icon)
        }
        val changed = newDisplayed != displayed
        displayed = newDisplayed
        return if (changed && newDisplayed != null) listOf(AlertEffect.Notify(newDisplayed)) else emptyList()
    }

    private fun soundsFor(alert: EngineAlert): List<AlertEffect> {
        val text = MistakeTypes.alertText(alert.type, alert.message)
        return when (alert.level) {
            AlertLevel.VISUAL -> emptyList()
            AlertLevel.AUDIO -> listOf(AlertEffect.PlaySound(SoundKind.CHIME), AlertEffect.Speak(text))
            AlertLevel.HAPTIC -> listOf(AlertEffect.PlaySound(SoundKind.DOUBLE_CHIME), AlertEffect.Speak(text))
            AlertLevel.CRITICAL -> listOf(AlertEffect.PlaySound(SoundKind.ALARM), AlertEffect.Speak(text))
        }
    }

    private fun minDisplayFor(level: AlertLevel): Long =
        if (level == AlertLevel.VISUAL || level == AlertLevel.AUDIO) minDisplayMs else 0

    /** Max display time: payload ttl_s, else the level default (spec §9.1). Restarts on every update. */
    private fun maxDisplayMs(alert: EngineAlert): Long {
        val ttl = alert.ttlS ?: when (alert.level) {
            AlertLevel.VISUAL, AlertLevel.AUDIO -> 8.0
            AlertLevel.HAPTIC -> 10.0
            AlertLevel.CRITICAL -> 15.0
        }
        return (ttl * 1000).toLong()
    }
}
