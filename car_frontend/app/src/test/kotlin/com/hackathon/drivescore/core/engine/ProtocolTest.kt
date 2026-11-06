package com.hackathon.drivescore.core.engine

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolTest {
    @Test fun `encodes session start envelope with snake_case payload`() {
        val env = EngineCodec.envelope(
            EngineProtocol.Types.SESSION_START,
            SessionStartPayload("s1", "u1", "Mihajlo", "c1", "2026-11-06T08:01:12Z"),
            "m1", "2026-11-06T08:01:12.345Z",
        )
        val o = Json.parseToJsonElement(EngineCodec.encode(env)).jsonObject
        assertEquals("1", o["v"]!!.jsonPrimitive.content)
        assertEquals("session.start", o["type"]!!.jsonPrimitive.content)
        assertEquals("m1", o["msg_id"]!!.jsonPrimitive.content)
        val p = o["payload"]!!.jsonObject
        assertEquals("s1", p["session_id"]!!.jsonPrimitive.content)
        assertEquals("Mihajlo", p["display_name"]!!.jsonPrimitive.content)
    }

    @Test fun `decodes hello, ack, alert, clear, error`() {
        val hello = EngineCodec.decode("""{"v":1,"type":"hello","msg_id":"a","ts":"t","payload":{"engine_version":"0.1.0","active_session_id":null,"supported_actions":["PHONE_CALL","EYES_OFF_ROAD"],"future":1}}""")
        assertEquals(listOf("PHONE_CALL", "EYES_OFF_ROAD"), (hello as EngineMessage.Hello).payload.supportedActions)
        assertEquals(EngineMessage.SessionAck("s1"), EngineCodec.decode("""{"v":1,"type":"session.ack","msg_id":"a","ts":"t","payload":{"session_id":"s1"}}"""))
        val alert = EngineCodec.decode("""{"v":1,"type":"alert","msg_id":"a","ts":"t","payload":{"alert_id":"x","session_id":"s1","type":"EYES_OFF_ROAD","level":"AUDIO","message":"Eyes on the road.","duration_so_far_s":1.2,"started_at":"t","ttl_s":10}}""")
        assertEquals("AUDIO", (alert as EngineMessage.Alert).payload.level)
        assertEquals(10.0, alert.payload.ttlS!!, 0.0)
        assertEquals(EngineMessage.AlertClear("x"), EngineCodec.decode("""{"v":1,"type":"alert.clear","msg_id":"a","ts":"t","payload":{"alert_id":"x"}}"""))
        val err = EngineCodec.decode("""{"v":1,"type":"error","msg_id":"a","ts":"t","payload":{"code":"NO_ACTIVE_SESSION","message":"m","ref_msg_id":"r"}}""")
        assertEquals("NO_ACTIVE_SESSION", (err as EngineMessage.Error).payload.code)
    }

    @Test fun `decodes drive result and keeps the raw payload`() {
        val text = """{"v":1,"type":"drive.result","msg_id":"a","ts":"t","payload":{"session_id":"s1","user_id":"u1","started_at":"2026-11-06T08:01:12Z","ended_at":"2026-11-06T08:34:40Z","distance_km":null,"score":87.5,"engine_version":"rivian-hackathon-engine/0.1.0","mistakes":[{"type":"PHONE_CALL","count":1,"total_duration_s":8.0,"max_duration_s":8.0}],"events":[{"type":"PHONE_CALL","started_at":"2026-11-06T08:01:22Z","duration_s":8.0}],"extra":{"penalties":{"PHONE_CALL":9.0}}}}"""
        val r = EngineCodec.decode(text) as EngineMessage.DriveResult
        assertEquals(87.5, r.payload.score, 0.0)
        assertEquals(1, r.payload.mistakes.single().count)
        assertTrue(r.rawPayload.containsKey("extra"))
    }

    @Test fun `unknown types and malformed text are reported, not thrown`() {
        assertEquals(EngineMessage.Unknown("telemetry"), EngineCodec.decode("""{"v":1,"type":"telemetry","msg_id":"a","ts":"t","payload":{}}"""))
        assertTrue(EngineCodec.decode("not json") is EngineMessage.Malformed)
        assertTrue(EngineCodec.decode("""{"v":1,"type":"alert","msg_id":"a","ts":"t","payload":{}}""") is EngineMessage.Malformed)
    }

    @Test fun `alert level parsing treats unknown as AUDIO`() {
        assertEquals(com.hackathon.drivescore.core.model.AlertLevel.CRITICAL, com.hackathon.drivescore.core.model.AlertLevel.parse("CRITICAL"))
        assertEquals(com.hackathon.drivescore.core.model.AlertLevel.AUDIO, com.hackathon.drivescore.core.model.AlertLevel.parse("LOUD"))
        assertEquals(com.hackathon.drivescore.core.model.AlertLevel.AUDIO, com.hackathon.drivescore.core.model.AlertLevel.parse(null))
    }

    @Test fun `backoff follows 0_5 1 2 4 8 capped at 15 with 20 percent jitter`() {
        val b = Backoff()
        assertEquals(listOf(500L, 1_000L, 2_000L, 4_000L, 8_000L, 15_000L, 15_000L), (0..6).map { b.nominalMs(it) })
        val r = kotlin.random.Random(1)
        repeat(200) {
            val d = b.delayMs(3, r)
            assertTrue(d in 3_200..4_800)
        }
    }
}
