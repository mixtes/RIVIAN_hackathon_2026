package com.hackathon.drivescore.service

import com.hackathon.drivescore.core.model.AlertEvent
import com.hackathon.drivescore.core.model.AlertLevel
import com.hackathon.drivescore.core.model.EngineAlert
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Wiring test: engine alert events reach the overlay state and the sound/notification outputs. */
@OptIn(ExperimentalCoroutinesApi::class)
class AlertPresenterTest {
    private class RecordingOutput : AlertOutput {
        val calls = mutableListOf<String>()
        override fun play(kind: SoundKind) { calls += "play:$kind" }
        override fun speak(text: String) { calls += "speak:$text" }
        override fun notify(alert: DisplayedAlert) { calls += "notify:${alert.alertId}" }
        override fun cancel(alertId: String) { calls += "cancel:$alertId" }
        override fun cancelAll() { calls += "cancelAll" }
    }

    @Test fun `alerts from the gateway drive the overlay, sounds and background notifications`() = runTest {
        val gateway = FakeGateway()
        val out = RecordingOutput()
        val foreground = MutableStateFlow(false)
        val running = MutableStateFlow(true)
        val p = AlertPresenter(gateway, out, SchedulerClock(this), backgroundScope, foreground, running)
        p.start(); runCurrent()

        gateway.alertFlow.emit(AlertEvent.Show(EngineAlert("a1", "s", "EYES_OFF_ROAD", AlertLevel.CRITICAL, "DANGER", 4.0, 15.0)))
        runCurrent()
        assertEquals("a1", p.current.value!!.alertId)
        assertEquals(listOf("play:ALARM", "speak:DANGER", "notify:a1"), out.calls.filter { it != "cancelAll" })

        gateway.alertFlow.emit(AlertEvent.Clear("a1")); runCurrent()
        assertNull(p.current.value)

        gateway.alertFlow.emit(AlertEvent.Show(EngineAlert("a2", "s", "PHONE_CALL", AlertLevel.VISUAL, "x", 0.0, 8.0)))
        runCurrent()
        running.value = false; runCurrent()
        advanceTimeBy(300); runCurrent()
        assertNull(p.current.value)
        p.stop()
    }
}
