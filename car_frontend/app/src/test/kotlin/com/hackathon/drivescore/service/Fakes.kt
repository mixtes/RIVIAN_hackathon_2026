package com.hackathon.drivescore.service

import com.hackathon.drivescore.core.common.Clock
import com.hackathon.drivescore.core.engine.DriveResultPayload
import com.hackathon.drivescore.core.engine.EngineGateway
import com.hackathon.drivescore.core.engine.EngineInfo
import com.hackathon.drivescore.core.engine.EngineProtocol
import com.hackathon.drivescore.core.engine.ErrorPayload
import com.hackathon.drivescore.core.engine.IncomingDriveResult
import com.hackathon.drivescore.core.engine.SessionStartRequest
import com.hackathon.drivescore.core.engine.StartOutcome
import com.hackathon.drivescore.core.model.ActiveSession
import com.hackathon.drivescore.core.model.AlertEvent
import com.hackathon.drivescore.core.model.ConnectionStatus
import com.hackathon.drivescore.core.model.EndReason
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.test.TestScope
import kotlinx.serialization.json.jsonObject

class FakeClock(var now: Long = 1_700_000_000_000L, var elapsed: Long = 1_000_000L) : Clock {
    override fun nowMs() = now
    override fun elapsedRealtimeMs() = elapsed
}

/** Clock that follows the coroutine test scheduler's virtual time. */
class SchedulerClock(private val scope: TestScope, private val base: Long = 1_700_000_000_000L) : Clock {
    override fun nowMs() = base + scope.testScheduler.currentTime
    override fun elapsedRealtimeMs() = scope.testScheduler.currentTime
}

class FakeGateway(private val log: MutableList<String> = mutableListOf()) : EngineGateway {
    override val status: StateFlow<ConnectionStatus> = MutableStateFlow(ConnectionStatus.CONNECTED)
    override val info: StateFlow<EngineInfo?> = MutableStateFlow(null)
    val alertFlow = MutableSharedFlow<AlertEvent>(extraBufferCapacity = 16)
    override val alerts: Flow<AlertEvent> = alertFlow
    val resultFlow = MutableSharedFlow<IncomingDriveResult>(extraBufferCapacity = 16)
    override val results: Flow<IncomingDriveResult> = resultFlow
    override val errors: Flow<ErrorPayload> = MutableSharedFlow()
    val started = mutableListOf<SessionStartRequest>()
    val ended = mutableListOf<Pair<String, EndReason>>()
    val acks = mutableListOf<String>()
    var startOutcome = StartOutcome.ACKED

    override fun start(carId: String?) = Unit
    override fun stop() = Unit
    override suspend fun startSession(request: SessionStartRequest): StartOutcome { started += request; return startOutcome }
    override fun endSession(sessionId: String, reason: EndReason) { ended += sessionId to reason }
    override fun ackResult(sessionId: String) { acks += sessionId; log += "ack:$sessionId" }
    override fun dropConnection() = Unit
}

class FakeStore(var saved: ActiveSession? = null) : SessionStore {
    override suspend fun load() = saved
    override suspend fun save(session: ActiveSession?) { saved = session }
}

class FakeSink(private val log: MutableList<String>) : DriveResultSink {
    var outcome: (IncomingDriveResult) -> PersistOutcome = { PersistOutcome.Saved(it.payload.sessionId, it.payload.userId, false) }
    var fail = false
    override suspend fun persist(result: IncomingDriveResult): PersistOutcome {
        if (fail) throw IllegalStateException("db down")
        log += "persist:${result.payload.sessionId}"
        return outcome(result)
    }
}

fun result(sessionId: String, userId: String = "u1", score: Double = 90.0): IncomingDriveResult {
    val p = DriveResultPayload(sessionId, userId, "2026-11-06T08:00:00Z", "2026-11-06T08:30:00Z", score = score)
    return IncomingDriveResult(p, EngineProtocol.json.encodeToJsonElement(DriveResultPayload.serializer(), p).jsonObject)
}
