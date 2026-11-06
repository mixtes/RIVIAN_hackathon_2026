package com.hackathon.drivescore.core.engine

import com.hackathon.drivescore.core.model.AlertEvent
import com.hackathon.drivescore.core.model.ConnectionStatus
import com.hackathon.drivescore.core.model.EndReason
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.merge
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/**
 * Delegates to the real WebSocket gateway or the in-app FakeEngine, depending on the debug toggle
 * (spec §15.1), and merges debug-injected alerts/results so the whole app pipeline can be exercised.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class SwitchingEngineGateway(
    private val real: EngineGateway,
    val fake: FakeEngineGateway,
    private val useFake: StateFlow<Boolean>,
    private val scope: CoroutineScope,
) : EngineGateway {
    private val active: EngineGateway get() = if (useFake.value) fake else real
    private var carId: String? = null
    private var started = false
    private var lastRequest: SessionStartRequest? = null

    val debugAlerts = MutableSharedFlow<AlertEvent>(extraBufferCapacity = 16)
    val debugResults = MutableSharedFlow<IncomingDriveResult>(extraBufferCapacity = 4)

    override val status: StateFlow<ConnectionStatus> =
        useFake.flatMapLatest { if (it) fake.status else real.status }
            .stateIn(scope, SharingStarted.Eagerly, ConnectionStatus.DISCONNECTED)
    override val info: StateFlow<EngineInfo?> =
        useFake.flatMapLatest { if (it) fake.info else real.info }.stateIn(scope, SharingStarted.Eagerly, null)
    override val alerts: Flow<AlertEvent> = merge(real.alerts, fake.alerts, debugAlerts)
    override val results: Flow<IncomingDriveResult> = merge(real.results, fake.results, debugResults)
    override val errors = merge(real.errors, fake.errors)

    init {
        scope.launch {
            useFake.drop(1).collect { fakeNow ->
                if (!started) return@collect
                (if (fakeNow) real else fake).stop()
                active.start(carId)
                lastRequest?.let { active.startSession(it) }
            }
        }
    }

    override fun start(carId: String?) {
        this.carId = carId
        started = true
        active.start(carId)
    }

    override fun stop() {
        started = false
        real.stop(); fake.stop()
    }

    override suspend fun startSession(request: SessionStartRequest): StartOutcome {
        lastRequest = request
        return active.startSession(request)
    }

    override fun endSession(sessionId: String, reason: EndReason) {
        if (lastRequest?.sessionId == sessionId) lastRequest = null
        active.endSession(sessionId, reason)
    }

    override fun ackResult(sessionId: String) {
        real.ackResult(sessionId)
        fake.ackResult(sessionId)
    }

    override fun dismissAlert(alertId: String, sessionId: String?) {
        // Debug-injected alerts (§15.1) exist only in the app; the engine never saw them.
        if (alertId.startsWith(DEBUG_ALERT_PREFIX)) return
        active.dismissAlert(alertId, sessionId)
    }

    override fun dropConnection() = active.dropConnection()

    companion object {
        /** Prefix for alert ids injected through [debugAlerts]; their dismiss is not sent to the engine. */
        const val DEBUG_ALERT_PREFIX = "debug-"
    }
}
