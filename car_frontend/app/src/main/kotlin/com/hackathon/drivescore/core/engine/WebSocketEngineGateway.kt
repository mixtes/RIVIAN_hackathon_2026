package com.hackathon.drivescore.core.engine

import com.hackathon.drivescore.core.common.AppLogger
import com.hackathon.drivescore.core.common.IsoTime
import com.hackathon.drivescore.core.model.AlertEvent
import com.hackathon.drivescore.core.model.AlertLevel
import com.hackathon.drivescore.core.model.ConnectionStatus
import com.hackathon.drivescore.core.model.EndReason
import com.hackathon.drivescore.core.model.EngineAlert
import com.hackathon.drivescore.core.model.MistakeTypes
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import java.util.UUID

/**
 * Real engine gateway over [EngineClient] implementing the §8 protocol and the §8.4 reliability rules:
 * hello on every (re)connect, session mismatch handling, resume with the same session id,
 * session.start ack timeout + retries.
 */
class WebSocketEngineGateway(
    private val client: EngineClient,
    private val scope: CoroutineScope,
    private val log: AppLogger,
    private val urlProvider: () -> String,
    private val appVersion: String,
    private val nowMs: () -> Long = System::currentTimeMillis,
    private val ackTimeoutMs: Long = 5_000,
    private val maxStartSends: Int = 3,
    private val helloWaitMs: Long = 2_000,
) : EngineGateway {

    override val status: StateFlow<ConnectionStatus> get() = client.status
    private val _info = MutableStateFlow<EngineInfo?>(null)
    override val info: StateFlow<EngineInfo?> = _info.asStateFlow()
    private val _alerts = MutableSharedFlow<AlertEvent>(extraBufferCapacity = 64)
    override val alerts: Flow<AlertEvent> = _alerts.asSharedFlow()
    private val _results = MutableSharedFlow<IncomingDriveResult>(replay = 0, extraBufferCapacity = 32)
    override val results: Flow<IncomingDriveResult> = _results.asSharedFlow()
    private val _errors = MutableSharedFlow<ErrorPayload>(extraBufferCapacity = 16)
    override val errors: Flow<ErrorPayload> = _errors.asSharedFlow()

    @Volatile private var carId: String? = null
    /** The session the app wants the engine to run (null = none). */
    @Volatile private var desired: SessionStartRequest? = null
    private var jobs: List<Job> = emptyList()
    /** Session ids acked by the engine (state, so an ack racing the waiter is never lost). */
    private val ackedSessions = MutableStateFlow<Set<String>>(emptySet())
    private var helloFallback: Job? = null

    override fun start(carId: String?) {
        this.carId = carId
        if (jobs.isNotEmpty()) {
            client.start(urlProvider())
            return
        }
        jobs = listOf(
            scope.launch { client.opened.collect { onOpened() } },
            scope.launch { client.incoming.collect { onMessage(it) } },
        )
        client.start(urlProvider())
    }

    override fun stop() {
        jobs.forEach { it.cancel() }
        jobs = emptyList()
        client.stop()
    }

    override fun dropConnection() = client.dropConnection()

    private fun onOpened() {
        send(EngineProtocol.Types.HELLO, HelloOut(appVersion = appVersion, carId = carId))
        // If the engine doesn't answer hello promptly, resume anyway.
        helloFallback?.cancel()
        helloFallback = scope.launch {
            delay(helloWaitMs)
            desired?.let { sendStart(it) }
        }
    }

    private fun onMessage(msg: EngineMessage) {
        when (msg) {
            is EngineMessage.Hello -> onHello(msg.payload)
            is EngineMessage.SessionAck -> ackedSessions.value = ackedSessions.value + msg.sessionId
            is EngineMessage.DriveResult -> _results.tryEmit(IncomingDriveResult(msg.payload, msg.rawPayload))
            is EngineMessage.Alert -> {
                val p = msg.payload
                _alerts.tryEmit(
                    AlertEvent.Show(
                        EngineAlert(
                            alertId = p.alertId,
                            sessionId = p.sessionId,
                            type = MistakeTypes.normalize(p.type),
                            level = AlertLevel.parse(p.level),
                            message = p.message,
                            durationSoFarS = p.durationSoFarS,
                            ttlS = p.ttlS,
                            requiresAck = p.requiresAck == true,
                        ),
                    ),
                )
            }
            is EngineMessage.AlertClear -> _alerts.tryEmit(AlertEvent.Clear(msg.alertId))
            is EngineMessage.Error -> {
                log.w(TAG, "Engine error ${msg.payload.code}: ${msg.payload.message}")
                _errors.tryEmit(msg.payload)
            }
            is EngineMessage.Unknown, is EngineMessage.Malformed -> Unit
        }
    }

    private fun onHello(hello: HelloIn) {
        helloFallback?.cancel()
        _info.value = EngineInfo(hello.engineVersion, hello.activeSessionId, hello.supportedActions)
        val mine = desired
        val theirs = hello.activeSessionId
        // §8.4 rule 2: end the engine's foreign session first.
        if (theirs != null && theirs != mine?.sessionId) {
            send(EngineProtocol.Types.SESSION_END, SessionEndPayload(theirs, EndReason.DRIVER_SWITCH.name))
        }
        // §8.4 rule 1: resume our session with the same id.
        if (mine != null) sendStart(mine)
    }

    private fun sendStart(req: SessionStartRequest): Boolean = send(
        EngineProtocol.Types.SESSION_START,
        SessionStartPayload(req.sessionId, req.userId, req.displayName, req.carId, IsoTime.format(req.startedAtMs)),
    )

    override suspend fun startSession(request: SessionStartRequest): StartOutcome {
        desired = request
        ackedSessions.value = ackedSessions.value - request.sessionId
        repeat(maxStartSends) {
            sendStart(request)
            val acked = withTimeoutOrNull(ackTimeoutMs) {
                ackedSessions.first { request.sessionId in it }
            }
            if (acked != null) return StartOutcome.ACKED
        }
        return StartOutcome.NO_RESPONSE
    }

    override fun endSession(sessionId: String, reason: EndReason) {
        if (desired?.sessionId == sessionId) desired = null
        send(EngineProtocol.Types.SESSION_END, SessionEndPayload(sessionId, reason.name))
    }

    override fun ackResult(sessionId: String) {
        send(EngineProtocol.Types.DRIVE_RESULT_ACK, SessionRef(sessionId))
    }

    override fun dismissAlert(alertId: String, sessionId: String?) {
        // Not queued: if the socket is down, client.send drops it (the engine keeps ringing → new alert).
        if (!send(EngineProtocol.Types.ALERT_DISMISS, AlertDismissPayload(alertId, sessionId))) {
            log.w(TAG, "alert.dismiss for $alertId dropped (engine not connected)")
        }
    }

    private inline fun <reified T> send(type: String, payload: T): Boolean =
        client.send(EngineCodec.envelope(type, payload, UUID.randomUUID().toString(), IsoTime.format(nowMs())))

    private companion object { const val TAG = "EngineGateway" }
}
