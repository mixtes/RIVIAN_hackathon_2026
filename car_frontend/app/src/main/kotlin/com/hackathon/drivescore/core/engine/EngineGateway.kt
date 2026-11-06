package com.hackathon.drivescore.core.engine

import com.hackathon.drivescore.core.model.AlertEvent
import com.hackathon.drivescore.core.model.ConnectionStatus
import com.hackathon.drivescore.core.model.EndReason
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.StateFlow

data class EngineInfo(val engineVersion: String?, val activeSessionId: String?, val supportedActions: List<String>)

data class SessionStartRequest(
    val sessionId: String,
    val userId: String,
    val displayName: String,
    val carId: String?,
    val startedAtMs: Long,
)

enum class StartOutcome { ACKED, NO_RESPONSE }

data class IncomingDriveResult(val payload: DriveResultPayload, val rawPayload: kotlinx.serialization.json.JsonObject)

/** Typed facade over the engine connection (spec §13). Implemented by the real WS gateway and FakeEngine. */
interface EngineGateway {
    val status: StateFlow<ConnectionStatus>
    val info: StateFlow<EngineInfo?>
    val alerts: Flow<AlertEvent>
    val results: Flow<IncomingDriveResult>
    val errors: Flow<ErrorPayload>

    fun start(carId: String?)
    fun stop()

    /**
     * Sends `session.start` and waits for `session.ack` (5 s, max 3 sends, spec §8.3). The session is
     * remembered and re-sent with the same id after every reconnect (spec §8.4 rule 1).
     */
    suspend fun startSession(request: SessionStartRequest): StartOutcome

    fun endSession(sessionId: String, reason: EndReason)

    /** Sent after the result is persisted (spec §8.3). */
    fun ackResult(sessionId: String)

    fun dropConnection()
}
