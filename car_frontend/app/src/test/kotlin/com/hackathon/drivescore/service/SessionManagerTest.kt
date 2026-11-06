package com.hackathon.drivescore.service

import com.hackathon.drivescore.core.common.NoopLogger
import com.hackathon.drivescore.core.engine.StartOutcome
import com.hackathon.drivescore.core.model.ActiveSession
import com.hackathon.drivescore.core.model.AppState
import com.hackathon.drivescore.core.model.EndReason
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class SessionManagerTest {
    private val log = mutableListOf<String>()
    private val gateway = FakeGateway(log)
    private val store = FakeStore()
    private val sink = FakeSink(log)

    private fun TestScope.manager() = SessionManager(
        gateway, store, sink, { "car-1" }, SchedulerClock(this), backgroundScope, NoopLogger,
    ).also { runCurrent() }

    private fun TestScope.unlock(sm: SessionManager): ActiveSession {
        sm.selectDriver("u1"); runCurrent()
        assertEquals(AppState.PinEntry("u1"), sm.state.value)
        backgroundScope.launch { sm.onUnlocked("u1", "Mihajlo") }
        runCurrent()
        return (sm.state.value as AppState.SessionActive).session
    }

    @Test fun `pin entry can be cancelled back to locked`() = runTest {
        val sm = manager()
        sm.selectDriver("u1"); runCurrent()
        sm.cancelPinEntry(); runCurrent()
        assertEquals(AppState.Locked, sm.state.value)
    }

    @Test fun `unlock starts a persisted session on the engine`() = runTest {
        val sm = manager()
        val s = unlock(sm)
        assertEquals(s, store.saved)
        assertEquals(s.sessionId, gateway.started.single().sessionId)
        assertEquals("car-1", gateway.started.single().carId)
    }

    @Test fun `engine not responding emits an event but keeps tracking`() = runTest {
        gateway.startOutcome = StartOutcome.NO_RESPONSE
        val sm = manager()
        val events = mutableListOf<SessionEvent>()
        backgroundScope.launch { sm.events.toList(events) }
        runCurrent()
        unlock(sm)
        assertTrue(events.contains(SessionEvent.EngineNotResponding))
        assertTrue(sm.state.value is AppState.SessionActive)
    }

    @Test fun `end drive waits for the result, persists, then acks`() = runTest {
        val sm = manager()
        val s = unlock(sm)
        sm.endDrive(); runCurrent()
        assertTrue(sm.state.value is AppState.Ending)
        assertEquals(s.sessionId to EndReason.MANUAL, gateway.ended.single())
        assertTrue(store.saved!!.ending)

        gateway.resultFlow.emit(result(s.sessionId)); runCurrent()
        val st = sm.state.value as AppState.Result
        assertEquals(s.sessionId, st.driveId)
        assertEquals(listOf("persist:${s.sessionId}", "ack:${s.sessionId}"), log)
        assertNull(store.saved)
    }

    @Test fun `result timeout shows result unavailable and a late result still lands`() = runTest {
        val sm = manager()
        val s = unlock(sm)
        sm.endDrive(); runCurrent()
        advanceTimeBy(15_001); runCurrent()
        val st = sm.state.value as AppState.Result
        assertTrue(st.resultUnavailable)
        assertNull(st.driveId)

        gateway.resultFlow.emit(result(s.sessionId)); runCurrent()
        assertEquals(s.sessionId, (sm.state.value as AppState.Result).driveId)
    }

    @Test fun `spontaneous result during the session ends it`() = runTest {
        val sm = manager()
        val s = unlock(sm)
        gateway.resultFlow.emit(result(s.sessionId)); runCurrent()
        assertTrue(sm.state.value is AppState.Result)
    }

    @Test fun `result for another session is persisted and acked without changing state`() = runTest {
        val sm = manager()
        unlock(sm)
        gateway.resultFlow.emit(result("old-session")); runCurrent()
        assertTrue(sm.state.value is AppState.SessionActive)
        assertEquals(listOf("old-session"), gateway.acks)
    }

    @Test fun `invalid and unknown-user results are acked anyway`() = runTest {
        val sm = manager()
        val s = unlock(sm)
        sink.outcome = { PersistOutcome.UnknownUser }
        gateway.resultFlow.emit(result("x")); runCurrent()
        sink.outcome = { PersistOutcome.Invalid("score") }
        gateway.resultFlow.emit(result(s.sessionId, score = 120.0)); runCurrent()
        assertEquals(listOf("x", s.sessionId), gateway.acks)
        assertTrue((sm.state.value as AppState.Result).resultInvalid)
    }

    @Test fun `a persistence failure is not acked so the engine re-delivers`() = runTest {
        val sm = manager()
        val s = unlock(sm)
        sink.fail = true
        gateway.resultFlow.emit(result(s.sessionId)); runCurrent()
        assertTrue(gateway.acks.isEmpty())
        assertTrue(sm.state.value is AppState.SessionActive)
    }

    @Test fun `start new drive within 2 minutes needs no PIN, later it does`() = runTest {
        val sm = manager()
        val s = unlock(sm)
        gateway.resultFlow.emit(result(s.sessionId)); runCurrent()
        backgroundScope.launch { sm.startNewDrive() }; runCurrent()
        val second = (sm.state.value as AppState.SessionActive).session
        assertTrue(second.sessionId != s.sessionId)

        gateway.resultFlow.emit(result(second.sessionId)); runCurrent()
        advanceTimeBy(2 * 60_000 + 1); runCurrent()
        backgroundScope.launch { sm.startNewDrive() }; runCurrent()
        assertEquals(AppState.PinEntry("u1"), sm.state.value)
    }

    @Test fun `auto-locks after 5 minutes idle on the result screen, interaction resets it`() = runTest {
        val sm = manager()
        val s = unlock(sm)
        gateway.resultFlow.emit(result(s.sessionId)); runCurrent()
        advanceTimeBy(4 * 60_000); runCurrent()
        sm.onUserInteraction(); runCurrent()
        advanceTimeBy(4 * 60_000); runCurrent()
        assertTrue(sm.state.value is AppState.Result)
        advanceTimeBy(60_001); runCurrent()
        assertEquals(AppState.Locked, sm.state.value)
    }

    @Test fun `lock during a session ends it with DRIVER_SWITCH`() = runTest {
        val sm = manager()
        val s = unlock(sm)
        sm.lock(); runCurrent()
        assertEquals(AppState.Locked, sm.state.value)
        assertEquals(s.sessionId to EndReason.DRIVER_SWITCH, gateway.ended.single())
        assertNull(store.saved)
    }

    @Test fun `ignition off ends the session`() = runTest {
        val sm = manager()
        val s = unlock(sm)
        sm.onIgnitionOff(); runCurrent()
        assertEquals(s.sessionId to EndReason.IGNITION_OFF, gateway.ended.single())
    }

    @Test fun `restart resumes the persisted session with the same id`() = runTest {
        val saved = ActiveSession("sess-42", "u1", "Mihajlo", 1_700_000_000_000L)
        store.saved = saved
        val sm = manager()
        sm.restore(); runCurrent()
        assertEquals(AppState.SessionActive(saved), sm.state.value)
        assertEquals("sess-42", gateway.started.single().sessionId)
    }

    @Test fun `restart while ending re-sends session end and waits`() = runTest {
        store.saved = ActiveSession("sess-7", "u1", "M", 0, ending = true)
        val sm = manager()
        sm.restore(); runCurrent()
        assertTrue(sm.state.value is AppState.Ending)
        assertEquals("sess-7", gateway.ended.single().first)
    }
}
