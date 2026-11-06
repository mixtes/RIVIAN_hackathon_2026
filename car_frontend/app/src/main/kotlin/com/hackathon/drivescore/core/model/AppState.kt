package com.hackathon.drivescore.core.model

/** Persisted active session (DataStore), so a restart mid-drive resumes it (spec §5). */
data class ActiveSession(
    val sessionId: String,
    val userId: String,
    val displayName: String,
    val startedAt: Long,
    val ending: Boolean = false,
)

/** App state machine (spec §5). Owned by SessionManager. */
sealed interface AppState {
    data object Locked : AppState
    data class PinEntry(val userId: String) : AppState
    data class SessionActive(val session: ActiveSession) : AppState
    data class Ending(val session: ActiveSession, val since: Long) : AppState

    /** RESULT / UNLOCKED_IDLE. [driveId] is null when the result has not arrived (timeout). */
    data class Result(
        val userId: String,
        val sessionId: String,
        val driveId: String?,
        val resultAt: Long,
        val resultUnavailable: Boolean = false,
        val resultInvalid: Boolean = false,
    ) : AppState

    val unlockedUserId: String?
        get() = when (this) {
            is SessionActive -> session.userId
            is Ending -> session.userId
            is Result -> userId
            else -> null
        }
}
