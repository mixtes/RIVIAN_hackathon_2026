package com.hackathon.drivescore.service

import com.hackathon.drivescore.core.model.AlertLevel
import com.hackathon.drivescore.core.model.EngineAlert
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AlertQueueTest {
    private val q = AlertQueue()
    private fun a(id: String, type: String, level: AlertLevel, msg: String? = "m-$id", ttl: Double? = null) =
        EngineAlert(id, "s", type, level, msg, 0.0, ttl)

    private fun List<AlertEffect>.sounds() = filterIsInstance<AlertEffect.PlaySound>().map { it.kind }
    private fun List<AlertEffect>.speaks() = filterIsInstance<AlertEffect.Speak>().map { it.text }

    @Test fun `visual is silent, audio chimes and speaks`() {
        val e1 = q.onAlert(a("1", "PHONE_CALL", AlertLevel.VISUAL), 0)
        assertTrue(e1.sounds().isEmpty() && e1.speaks().isEmpty())
        val e2 = q.onAlert(a("2", "EYES_OFF_ROAD", AlertLevel.AUDIO, "Eyes on the road."), 0)
        assertEquals(listOf(SoundKind.CHIME), e2.sounds())
        assertEquals(listOf("Eyes on the road."), e2.speaks())
    }

    @Test fun `escalation with the same alert id updates in place and plays the new level`() {
        q.onAlert(a("1", "EYES_OFF_ROAD", AlertLevel.AUDIO), 0)
        val e = q.onAlert(a("1", "EYES_OFF_ROAD", AlertLevel.HAPTIC), 1_500)
        assertEquals(listOf(SoundKind.DOUBLE_CHIME), e.sounds())
        assertEquals(AlertLevel.HAPTIC, q.displayed!!.level)
        val c = q.onAlert(a("1", "EYES_OFF_ROAD", AlertLevel.CRITICAL), 3_000)
        assertEquals(listOf(SoundKind.ALARM), c.sounds())
        // a repeat at the same level plays nothing
        assertTrue(q.onAlert(a("1", "EYES_OFF_ROAD", AlertLevel.CRITICAL), 3_100).sounds().isEmpty())
    }

    @Test fun `higher level preempts and the preempted alert resumes`() {
        q.onAlert(a("v", "PHONE_CALL", AlertLevel.VISUAL), 0)
        q.onAlert(a("c", "EYES_OFF_ROAD", AlertLevel.CRITICAL), 100)
        assertEquals("c", q.displayed!!.alertId)
        q.onClear("c", 500)
        assertEquals("v", q.displayed!!.alertId)
    }

    @Test fun `visual and audio stay at least 2 s after an early clear`() {
        q.onAlert(a("1", "PHONE_CALL", AlertLevel.VISUAL), 0)
        q.onClear("1", 500)
        assertEquals("1", q.displayed!!.alertId)
        q.tick(1_999)
        assertEquals("1", q.displayed!!.alertId)
        q.tick(2_000)
        assertNull(q.displayed)
    }

    @Test fun `alerts expire after ttl even without a clear`() {
        q.onAlert(a("1", "PHONE_CALL", AlertLevel.AUDIO, ttl = 10.0), 0)
        q.tick(9_999); assertEquals("1", q.displayed!!.alertId)
        q.tick(10_000); assertNull(q.displayed)
        q.onAlert(a("2", "PHONE_CALL", AlertLevel.HAPTIC), 20_000) // default HAPTIC ttl 10 s
        q.tick(30_000); assertNull(q.displayed)
    }

    @Test fun `same type within 10 s cooldown is shown but not re-sounded at the same level`() {
        q.onAlert(a("1", "PHONE_CALL", AlertLevel.AUDIO), 0)
        q.onClear("1", 3_000)
        q.tick(3_000)
        val again = q.onAlert(a("2", "PHONE_CALL", AlertLevel.AUDIO), 8_000)
        assertTrue(again.sounds().isEmpty() && again.speaks().isEmpty())
        assertEquals("2", q.displayed!!.alertId)
        // escalating beyond the played level does sound
        assertEquals(listOf(SoundKind.DOUBLE_CHIME), q.onAlert(a("2", "PHONE_CALL", AlertLevel.HAPTIC), 9_000).sounds())
        // after the cooldown it sounds again
        q.onClear("2", 9_500); q.tick(9_500)
        assertEquals(listOf(SoundKind.CHIME), q.onAlert(a("3", "PHONE_CALL", AlertLevel.AUDIO), 20_000).sounds())
    }

    @Test fun `critical repeats alarm every 2 s and speech every 6 s`() {
        q.onAlert(a("1", "EYES_OFF_ROAD", AlertLevel.CRITICAL), 0)
        val sounds = mutableListOf<SoundKind>()
        val speaks = mutableListOf<String>()
        var t = 250L
        while (t <= 6_000) { val e = q.tick(t); sounds += e.sounds(); speaks += e.speaks(); t += 250 }
        assertEquals(3, sounds.size)   // at 2, 4, 6 s
        assertEquals(1, speaks.size)   // at 6 s
    }

    @Test fun `missing message falls back to default text`() {
        q.onAlert(a("1", "HANDS_OFF_WHEEL", AlertLevel.VISUAL, msg = " "), 0)
        assertEquals("Hands on the wheel", q.displayed!!.text)
        q.onAlert(a("2", "WEIRD_THING", AlertLevel.AUDIO, msg = null), 0)
        assertEquals("Drive carefully", q.displayed!!.text)
    }

    @Test fun `clearAll removes everything and cancels notifications`() {
        q.onAlert(a("1", "PHONE_CALL", AlertLevel.AUDIO), 0)
        q.onAlert(a("2", "EYES_OFF_ROAD", AlertLevel.HAPTIC), 0)
        val e = q.clearAll(100)
        assertNull(q.displayed)
        assertEquals(2, e.filterIsInstance<AlertEffect.CancelNotification>().size)
    }
}
