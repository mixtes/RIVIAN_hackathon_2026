package com.hackathon.drivescore.core.model

/** A user who has been added to this car (spec §4). */
data class Driver(
    val userId: String,
    val username: String,
    val displayName: String,
    val avatarColor: String,
    val regionCode: String,
    val failedAttempts: Int,
    val pinDisabled: Boolean,
    val needsReauth: Boolean,
    val hasPendingUploads: Boolean = false,
) {
    val initials: String
        get() = displayName.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }
            .take(2).joinToString("") { it.first().uppercase() }.ifEmpty { "?" }
}

enum class DrivingState { PARKED, MOVING }

enum class ConnectionStatus { DISCONNECTED, CONNECTING, CONNECTED, RECONNECTING }

enum class SyncState { PENDING, SYNCING, SYNCED, FAILED_PERMANENT }

enum class DriveSource { LOCAL, REMOTE }

/** Engine alert levels, straight from RivianHackatonEngine's AlertLevel (spec §8.3). */
enum class AlertLevel(val rank: Int) {
    VISUAL(1), AUDIO(2), HAPTIC(3), CRITICAL(4);

    companion object {
        /** Unknown levels are treated as AUDIO (spec §8.3). */
        fun parse(raw: String?): AlertLevel =
            entries.firstOrNull { it.name.equals(raw?.trim(), ignoreCase = true) } ?: AUDIO
    }
}

/**
 * One alert occurrence as delivered by the engine. Escalations reuse [alertId]. [requiresAck] = latched:
 * stays (no ttl) until the driver presses Dismiss, the engine clears it, or the session ends (spec §9.1).
 */
data class EngineAlert(
    val alertId: String,
    val sessionId: String?,
    val type: String,
    val level: AlertLevel,
    val message: String?,
    val durationSoFarS: Double?,
    val ttlS: Double?,
    val requiresAck: Boolean = false,
)

sealed interface AlertEvent {
    data class Show(val alert: EngineAlert) : AlertEvent
    data class Clear(val alertId: String) : AlertEvent
}

enum class Period(val wire: String) {
    DAY("day"), WEEK("week"), MONTH("month"), YEAR("year"), ALL("all");

    /** Rolling window length in ms; null for ALL (backend spec §5.4). */
    val windowMs: Long?
        get() = when (this) {
            DAY -> 24L * 3600_000
            WEEK -> 7L * 24 * 3600_000
            MONTH -> 30L * 24 * 3600_000
            YEAR -> 365L * 24 * 3600_000
            ALL -> null
        }
}

enum class EndReason { MANUAL, DRIVER_SWITCH, IGNITION_OFF, APP_SHUTDOWN }

data class Mistake(
    val type: String,
    val count: Int,
    val totalDurationS: Double?,
    val maxDurationS: Double?,
)

data class DriveEvent(val type: String, val startedAt: Long, val durationS: Double?)

data class Drive(
    val id: String,
    val userId: String,
    val carId: String,
    val carName: String?,
    val startedAt: Long,
    val endedAt: Long,
    val durationS: Int,
    val score: Double,
    val engineVersion: String?,
    val source: DriveSource,
    val syncState: SyncState,
    val lastSyncError: String?,
    val mistakes: List<Mistake>,
    val events: List<DriveEvent>,
    val mistakeTotal: Int = mistakes.sumOf { it.count },
)

data class Region(val code: String, val name: String)
