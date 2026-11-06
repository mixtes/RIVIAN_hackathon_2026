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
    private fun latched(id: String, level: AlertLevel = AlertLevel.CRITICAL, ttl: Double? = null) =
        EngineAlert(id, "s", "EYES_CLOSED", level, "Wake up! Pull over and rest.", 0.5, ttl, requiresAck = true)

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

    @Test fun `latched alert never expires past its ttl, so it survives a lost connection`() {
        q.onAlert(latched("L", ttl = 10.0), 0)
        // No keep-alives arrive (connection lost): a normal alert would expire at 10 s.
        listOf(10_000L, 15_000L, 60_000L, 600_000L).forEach { t -> q.tick(t); assertEquals("L", q.displayed!!.alertId) }
        assertTrue(q.displayed!!.requiresAck)
    }

    @Test fun `dismiss removes a latched alert and emits stop, cancel and send effects`() {
        q.onAlert(latched("L"), 0)
        val e = q.dismiss("L", 1_000)
        assertNull(q.displayed)
        assertTrue(AlertEffect.StopAudio in e)
        assertTrue(AlertEffect.CancelNotification("L") in e)
        assertTrue(AlertEffect.SendDismiss("L", "s") in e)
        // no alarm repeats after the dismiss
        assertTrue(q.tick(3_000).sounds().isEmpty())
    }

    @Test fun `dismiss ignores unknown and non-latched alerts`() {
        q.onAlert(a("1", "EYES_OFF_ROAD", AlertLevel.CRITICAL), 0)
        assertTrue(q.dismiss("1", 100).isEmpty())
        assertTrue(q.dismiss("nope", 100).isEmpty())
        assertEquals("1", q.displayed!!.alertId)
    }

    @Test fun `clear after dismiss is a no-op, and late keep-alives for the dismissed id are ignored`() {
        q.onAlert(a("v", "PHONE_CALL", AlertLevel.VISUAL), 0)
        q.onAlert(latched("L"), 100)
        q.dismiss("L", 1_000)
        assertEquals("v", q.displayed!!.alertId)
        assertTrue(q.onClear("L", 1_200).isEmpty())
        assertTrue(q.onAlert(latched("L"), 1_300).isEmpty())
        assertEquals("v", q.displayed!!.alertId)
    }

    @Test fun `a new occurrence after dismiss sounds again despite the same-type cooldown`() {
        assertEquals(listOf(SoundKind.ALARM), q.onAlert(latched("L1"), 0).sounds())
        q.dismiss("L1", 2_000)
        val again = q.onAlert(latched("L2"), 2_300) // eyes still closed → new alert_id
        assertEquals(listOf(SoundKind.ALARM), again.sounds())
        assertEquals(listOf("Wake up! Pull over and rest."), again.speaks())
        assertEquals("L2", q.displayed!!.alertId)
        // and it repeats like CRITICAL
        assertEquals(listOf(SoundKind.ALARM), q.tick(4_300).sounds())
    }

    @Test fun `latched outranks a non-latched critical, which resumes after the dismiss`() {
        q.onAlert(latched("L"), 0)
        q.onAlert(a("c", "EYES_OFF_ROAD", AlertLevel.CRITICAL), 500) // newer, same level
        assertEquals("L", q.displayed!!.alertId)
        q.onAlert(a("c", "EYES_OFF_ROAD", AlertLevel.CRITICAL), 5_500) // keep-alive doesn't steal it
        assertEquals("L", q.displayed!!.alertId)
        q.dismiss("L", 6_000)
        assertEquals("c", q.displayed!!.alertId)
    }

    @Test fun `latched alert leaves on alert clear and on clearAll`() {
        q.onAlert(latched("L"), 0)
        q.onClear("L", 100)
        assertNull(q.displayed)
        q.onAlert(latched("L2"), 200)
        val e = q.clearAll(300)
        assertNull(q.displayed)
        assertTrue(AlertEffect.CancelNotification("L2") in e)
    }

    @Test fun `clearAll removes everything and cancels notifications`() {
        q.onAlert(a("1", "PHONE_CALL", AlertLevel.AUDIO), 0)
        q.onAlert(a("2", "EYES_OFF_ROAD", AlertLevel.HAPTIC), 0)
        val e = q.clearAll(100)
        assertNull(q.displayed)
        assertEquals(2, e.filterIsInstance<AlertEffect.CancelNotification>().size)
    }
}
