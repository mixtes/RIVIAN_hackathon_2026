package com.hackathon.drivescore.core.engine

import com.hackathon.drivescore.core.common.IsoTime
import com.hackathon.drivescore.core.model.AlertEvent
import com.hackathon.drivescore.core.model.AlertLevel
import com.hackathon.drivescore.core.model.ConnectionStatus
import com.hackathon.drivescore.core.model.EndReason
import com.hackathon.drivescore.core.model.EngineAlert
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import java.util.UUID
import kotlin.random.Random

/**
 * In-app engine with no network (spec §15.2): random alerts every 20–60 s during a session (with
 * escalation and clear), and a randomized drive.result on session.end, re-delivered until acked.
 */
class FakeEngineGateway(
    private val scope: CoroutineScope,
    private val random: Random = Random.Default,
    private val nowMs: () -> Long = System::currentTimeMillis,
) : EngineGateway {
    private val _status = MutableStateFlow(ConnectionStatus.DISCONNECTED)
    override val status: StateFlow<ConnectionStatus> = _status.asStateFlow()
    private val _info = MutableStateFlow<EngineInfo?>(null)
    override val info: StateFlow<EngineInfo?> = _info.asStateFlow()
    private val _alerts = MutableSharedFlow<AlertEvent>(extraBufferCapacity = 64)
    override val alerts: Flow<AlertEvent> = _alerts.asSharedFlow()
    private val _results = MutableSharedFlow<IncomingDriveResult>(extraBufferCapacity = 16)
    override val results: Flow<IncomingDriveResult> = _results.asSharedFlow()
    override val errors: Flow<ErrorPayload> = MutableSharedFlow()

    private var session: SessionStartRequest? = null
    private var alertLoop: Job? = null
    private val unacked = linkedMapOf<String, IncomingDriveResult>()
    private val occurrences = mutableListOf<Triple<String, Long, Double>>()

    override fun start(carId: String?) {
        _status.value = ConnectionStatus.CONNECTED
        _info.value = EngineInfo("fake-engine/1.0", session?.sessionId, ACTIONS)
        unacked.values.forEach { _results.tryEmit(it) }
    }

    override fun stop() {
        alertLoop?.cancel()
        _status.value = ConnectionStatus.DISCONNECTED
    }

    override fun dropConnection() {
        _status.value = ConnectionStatus.RECONNECTING
        scope.launch { delay(1_000); start(null) }
    }

    override suspend fun startSession(request: SessionStartRequest): StartOutcome {
        if (session?.sessionId != request.sessionId) {
            session = request
            occurrences.clear()
            alertLoop?.cancel()
            alertLoop = scope.launch { randomAlerts(request.sessionId) }
        }
        _info.value = EngineInfo("fake-engine/1.0", request.sessionId, ACTIONS)
        return StartOutcome.ACKED
    }

    private suspend fun randomAlerts(sessionId: String) {
        while (isActive()) {
            delay(random.nextLong(20_000, 60_001))
            val type = ACTIONS.random(random)
            val id = UUID.randomUUID().toString()
            val holdS = random.nextDouble(0.5, 7.0)
            val start = nowMs()
            val steps = listOf(0.0 to AlertLevel.VISUAL, 1.5 to AlertLevel.AUDIO, 3.0 to AlertLevel.HAPTIC, 5.0 to AlertLevel.CRITICAL)
            var elapsed = 0.0
            for ((at, level) in steps) {
                if (at > holdS) break
                delay(((at - elapsed) * 1000).toLong())
                elapsed = at
                _alerts.tryEmit(AlertEvent.Show(EngineAlert(id, sessionId, type, level, null, at, null)))
            }
            delay(((holdS - elapsed) * 1000).toLong().coerceAtLeast(0))
            _alerts.tryEmit(AlertEvent.Clear(id))
            occurrences += Triple(type, start, holdS)
        }
    }

    private suspend fun isActive(): Boolean = kotlin.coroutines.coroutineContext.isActive

    override fun endSession(sessionId: String, reason: EndReason) {
        val s = session?.takeIf { it.sessionId == sessionId } ?: return
        alertLoop?.cancel()
        session = null
        _info.value = EngineInfo("fake-engine/1.0", null, ACTIONS)
        val result = buildResult(s, nowMs())
        unacked[sessionId] = result
        scope.launch { delay(800); _results.tryEmit(result) }
    }

    fun buildResult(s: SessionStartRequest, endedAt: Long): IncomingDriveResult {
        val penalized = occurrences.filter { it.third >= 1.0 }
        val mistakes = penalized.groupBy { it.first }.map { (type, occ) ->
            MistakeDto(type, occ.size, occ.sumOf { it.third }, occ.maxOf { it.third })
        }
        val score = (100.0 - penalized.sumOf { 3.0 + it.third }).coerceIn(0.0, 100.0)
        val payload = DriveResultPayload(
            sessionId = s.sessionId,
            userId = s.userId,
            startedAt = IsoTime.format(s.startedAtMs),
            endedAt = IsoTime.format(endedAt.coerceAtLeast(s.startedAtMs)),
            score = Math.round(score * 100) / 100.0,
            engineVersion = "fake-engine/1.0",
            mistakes = mistakes,
            events = penalized.map { EventDto(it.first, IsoTime.format(it.second), it.third) },
        )
        val raw: JsonObject = EngineProtocol.json.encodeToJsonElement(DriveResultPayload.serializer(), payload).jsonObject
        return IncomingDriveResult(payload, raw)
    }

    override fun ackResult(sessionId: String) {
        unacked.remove(sessionId)
    }

    companion object {
        val ACTIONS = listOf("PHONE_CALL", "EYES_OFF_ROAD")
    }
}
