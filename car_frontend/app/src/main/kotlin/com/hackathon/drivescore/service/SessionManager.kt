package com.hackathon.drivescore.service

import com.hackathon.drivescore.core.common.AppLogger
import com.hackathon.drivescore.core.common.Clock
import com.hackathon.drivescore.core.engine.EngineGateway
import com.hackathon.drivescore.core.engine.IncomingDriveResult
import com.hackathon.drivescore.core.engine.SessionStartRequest
import com.hackathon.drivescore.core.engine.StartOutcome
import com.hackathon.drivescore.core.model.ActiveSession
import com.hackathon.drivescore.core.model.AppState
import com.hackathon.drivescore.core.model.EndReason
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.UUID

/** Persistence of the active session (DataStore in production). */
interface SessionStore {
    suspend fun load(): ActiveSession?
    suspend fun save(session: ActiveSession?)
}

sealed interface PersistOutcome {
    data class Saved(val driveId: String, val userId: String, val duplicate: Boolean) : PersistOutcome
    data class Invalid(val reason: String) : PersistOutcome
    data object UnknownUser : PersistOutcome
}

/** Validates and stores a drive.result in one Room transaction (spec §11.2). */
fun interface DriveResultSink {
    suspend fun persist(result: IncomingDriveResult): PersistOutcome
}

sealed interface SessionEvent {
    data object EngineNotResponding : SessionEvent
    data class ResultReady(val driveId: String) : SessionEvent
    data class ResultInvalid(val reason: String) : SessionEvent
    data class BackgroundResultSaved(val driveId: String, val userId: String) : SessionEvent
    /** A drive was saved; SyncDrivesWorker should be enqueued. */
    data object DriveSaved : SessionEvent
}

/**
 * The app state machine from spec §5. Single owner of [AppState]; the active session is persisted so
 * a process restart mid-drive resumes it with the same session id (spec §5, §8.4).
 */
class SessionManager(
    private val engine: EngineGateway,
    private val store: SessionStore,
    private val sink: DriveResultSink,
    private val carIdProvider: suspend () -> String?,
    private val clock: Clock,
    private val scope: CoroutineScope,
    private val log: AppLogger,
    private val endingTimeoutMs: Long = 15_000,
    private val autoLockIdleMs: Long = 5 * 60_000,
    private val quickRestartMs: Long = 2 * 60_000,
) {
    private val _state = MutableStateFlow<AppState>(AppState.Locked)
    val state: StateFlow<AppState> = _state.asStateFlow()

    private val _events = MutableSharedFlow<SessionEvent>(extraBufferCapacity = 16)
    val events: SharedFlow<SessionEvent> = _events.asSharedFlow()

    private val mutex = Mutex()
    private var endingTimer: Job? = null
    private var idleTimer: Job? = null
    private var restored = false
    /** Display names for the Result state (so "Start new drive" can restart without a PIN). */
    private var lastDisplayName: String? = null

    init {
        scope.launch { engine.results.collect { handleResult(it) } }
    }

    /** Resume a persisted session after a restart (spec §5). Safe to call more than once. */
    suspend fun restore() = mutex.withLock {
        if (restored) return@withLock
        restored = true
        val saved = store.load() ?: return@withLock
        lastDisplayName = saved.displayName
        if (saved.ending) {
            _state.value = AppState.Ending(saved, clock.nowMs())
            engine.endSession(saved.sessionId, EndReason.APP_SHUTDOWN)
            startEndingTimer(saved)
        } else {
            _state.value = AppState.SessionActive(saved)
            scope.launch { startOnEngine(saved) }
        }
    }

    fun selectDriver(userId: String) = transition {
        when (it) {
            is AppState.Locked, is AppState.PinEntry -> AppState.PinEntry(userId)
            else -> it
        }
    }

    fun cancelPinEntry() = transition { if (it is AppState.PinEntry) AppState.Locked else it }

    /** Called after the PIN was verified (or a quick restart): starts tracking (spec §5). */
    suspend fun onUnlocked(userId: String, displayName: String) {
        val session = mutex.withLock {
            val current = _state.value
            if (current is AppState.SessionActive || current is AppState.Ending) return
            cancelTimers()
            val s = ActiveSession(UUID.randomUUID().toString(), userId, displayName, clock.nowMs())
            store.save(s)
            lastDisplayName = displayName
            _state.value = AppState.SessionActive(s)
            s
        }
        startOnEngine(session)
    }

    private suspend fun startOnEngine(s: ActiveSession) {
        val outcome = engine.startSession(
            SessionStartRequest(s.sessionId, s.userId, s.displayName, carIdProvider(), s.startedAt),
        )
        if (outcome == StartOutcome.NO_RESPONSE) _events.emit(SessionEvent.EngineNotResponding)
    }

    /** "End drive" (parked only in the UI) or a vehicle signal. */
    fun endDrive(reason: EndReason = EndReason.MANUAL) {
        scope.launch {
            mutex.withLock {
                val current = _state.value as? AppState.SessionActive ?: return@withLock
                val ending = current.session.copy(ending = true)
                store.save(ending)
                _state.value = AppState.Ending(ending, clock.nowMs())
                engine.endSession(ending.sessionId, reason)
                startEndingTimer(ending)
            }
        }
    }

    private fun startEndingTimer(session: ActiveSession) {
        endingTimer?.cancel()
        endingTimer = scope.launch {
            delay(endingTimeoutMs)
            mutex.withLock {
                val cur = _state.value
                if (cur is AppState.Ending && cur.session.sessionId == session.sessionId) {
                    store.save(null)
                    _state.value = AppState.Result(
                        userId = session.userId, sessionId = session.sessionId, driveId = null,
                        resultAt = clock.nowMs(), resultUnavailable = true,
                    )
                    startIdleTimer()
                }
            }
        }
    }

    /**
     * From RESULT: within 2 min of the result, a new drive starts without a PIN; otherwise PIN entry.
     * Returns true if tracking started.
     */
    suspend fun startNewDrive(): Boolean {
        val cur = _state.value as? AppState.Result ?: return false
        val name = lastDisplayName
        return if (clock.nowMs() - cur.resultAt <= quickRestartMs && name != null) {
            onUnlocked(cur.userId, name)
            true
        } else {
            transition { AppState.PinEntry(cur.userId) }
            false
        }
    }

    /** "Lock / Switch driver", "Lock now", auto-lock. Ends a running session first. */
    fun lock(reason: EndReason = EndReason.DRIVER_SWITCH) {
        scope.launch {
            mutex.withLock {
                when (val cur = _state.value) {
                    is AppState.SessionActive -> {
                        engine.endSession(cur.session.sessionId, reason)
                        store.save(null)
                    }
                    is AppState.Ending -> store.save(null)
                    else -> Unit
                }
                cancelTimers()
                _state.value = AppState.Locked
            }
        }
    }

    fun onIgnitionOff() = lock(EndReason.IGNITION_OFF)

    /** Any touch while unlocked restarts the 5 min auto-lock timer (RESULT state only). */
    fun onUserInteraction() {
        if (_state.value is AppState.Result) startIdleTimer()
    }

    /** Re-lock after removing a driver etc. */
    fun forceLocked() = lock()

    private fun startIdleTimer() {
        idleTimer?.cancel()
        idleTimer = scope.launch {
            delay(autoLockIdleMs)
            mutex.withLock {
                if (_state.value is AppState.Result) _state.value = AppState.Locked
            }
        }
    }

    private fun cancelTimers() {
        endingTimer?.cancel(); endingTimer = null
        idleTimer?.cancel(); idleTimer = null
    }

    internal suspend fun handleResult(result: IncomingDriveResult) {
        val outcome = try {
            sink.persist(result)
        } catch (e: Exception) {
            log.e(TAG, "Persisting drive.result failed; not acking so the engine re-delivers", e)
            return
        }
        // Ack only after the result is persisted (or deliberately rejected), spec §8.3/§8.5.
        engine.ackResult(result.payload.sessionId)
        val sid = result.payload.sessionId
        mutex.withLock {
            val cur = _state.value
            val isCurrent = when (cur) {
                is AppState.SessionActive -> cur.session.sessionId == sid
                is AppState.Ending -> cur.session.sessionId == sid
                is AppState.Result -> cur.sessionId == sid && cur.driveId == null
                else -> false
            }
            when (outcome) {
                is PersistOutcome.Saved -> {
                    if (!outcome.duplicate) _events.emit(SessionEvent.DriveSaved)
                    if (isCurrent) {
                        cancelTimers()
                        store.save(null)
                        _state.value = AppState.Result(outcome.userId, sid, outcome.driveId, clock.nowMs())
                        startIdleTimer()
                        _events.emit(SessionEvent.ResultReady(outcome.driveId))
                    } else if (!outcome.duplicate) {
                        _events.emit(SessionEvent.BackgroundResultSaved(outcome.driveId, outcome.userId))
                    }
                }
                is PersistOutcome.Invalid -> {
                    log.w(TAG, "Invalid drive.result $sid: ${outcome.reason}")
                    if (isCurrent) {
                        cancelTimers()
                        store.save(null)
                        _state.value = AppState.Result(
                            result.payload.userId, sid, null, clock.nowMs(), resultInvalid = true,
                        )
                        startIdleTimer()
                    }
                    _events.emit(SessionEvent.ResultInvalid(outcome.reason))
                }
                PersistOutcome.UnknownUser -> log.e(TAG, "drive.result $sid for a user without a profile on this car; dropped")
            }
        }
    }

    private fun transition(f: (AppState) -> AppState) {
        scope.launch { mutex.withLock { _state.value = f(_state.value) } }
    }

    private companion object { const val TAG = "SessionManager" }
}
