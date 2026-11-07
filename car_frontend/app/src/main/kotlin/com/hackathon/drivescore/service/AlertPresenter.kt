package com.hackathon.drivescore.service

import com.hackathon.drivescore.core.common.Clock
import com.hackathon.drivescore.core.engine.EngineGateway
import com.hackathon.drivescore.core.model.AlertEvent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** Android side of alerts: sound, TTS, notifications (implemented by AndroidAlertOutput). */
interface AlertOutput {
    fun play(kind: SoundKind)
    fun speak(text: String)
    /** Stops a still-playing alarm/chime and TTS (after a Dismiss). */
    fun stopAudio()
    /** Heads-up notification; only called while the app is not in the foreground. Latched alerts get a Dismiss action. */
    fun notify(alert: DisplayedAlert)
    fun cancel(alertId: String)
    fun cancelAll()
}

/**
 * Drives the Compose overlay state and the sound/TTS/notification side effects (spec §9, §13).
 * All decisions are made by the pure [AlertQueue].
 */
class AlertPresenter(
    private val gateway: EngineGateway,
    private val output: AlertOutput,
    private val clock: Clock,
    private val scope: CoroutineScope,
    private val appInForeground: StateFlow<Boolean>,
    private val sessionRunning: Flow<Boolean>,
    private val tickMs: Long = 250,
) {
    private val queue = AlertQueue()
    private val mutex = Mutex()
    private val _current = MutableStateFlow<DisplayedAlert?>(null)
    val current: StateFlow<DisplayedAlert?> = _current.asStateFlow()
    private var jobs: List<Job> = emptyList()

    fun start() {
        if (jobs.isNotEmpty()) return
        jobs = listOf(
            scope.launch {
                gateway.alerts.collect { ev ->
                    when (ev) {
                        is AlertEvent.Show -> process { queue.onAlert(ev.alert, clock.nowMs()) }
                        is AlertEvent.Clear -> process { queue.onClear(ev.alertId, clock.nowMs()) }
                    }
                }
            },
            scope.launch {
                while (isActive) {
                    delay(tickMs)
                    process { queue.tick(clock.nowMs()) }
                }
            },
            scope.launch {
                sessionRunning.distinctUntilChanged().collect { running ->
                    if (!running) process { queue.clearAll(clock.nowMs()) }
                }
            },
            scope.launch {
                appInForeground.collect { fg ->
                    if (fg) output.cancelAll() else _current.value?.let { output.notify(it) }
                }
            },
        )
    }

    /**
     * The driver pressed Dismiss on a latched alert (overlay button or notification action, spec §9.1/§9.3).
     * Removes it locally at once and sends `alert.dismiss` to the engine.
     */
    fun dismiss(alertId: String) {
        scope.launch { process { queue.dismiss(alertId, clock.nowMs()) } }
    }

    fun stop() {
        jobs.forEach { it.cancel() }
        jobs = emptyList()
        output.cancelAll()
    }

    private suspend fun process(block: () -> List<AlertEffect>) {
        val effects = mutex.withLock {
            val e = block()
            _current.value = queue.displayed
            e
        }
        effects.forEach { effect ->
            when (effect) {
                is AlertEffect.PlaySound -> output.play(effect.kind)
                is AlertEffect.Speak -> output.speak(effect.text)
                is AlertEffect.Notify -> if (!appInForeground.value) output.notify(effect.alert)
                is AlertEffect.CancelNotification -> output.cancel(effect.alertId)
                AlertEffect.StopAudio -> output.stopAudio()
                is AlertEffect.SendDismiss -> gateway.dismissAlert(effect.alertId, effect.sessionId)
            }
        }
    }
}
