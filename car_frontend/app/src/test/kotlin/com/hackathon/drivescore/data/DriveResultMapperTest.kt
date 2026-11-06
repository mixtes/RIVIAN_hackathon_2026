package com.hackathon.drivescore.data

import com.hackathon.drivescore.core.engine.DriveResultPayload
import com.hackathon.drivescore.core.engine.EngineProtocol
import com.hackathon.drivescore.core.engine.MistakeDto
import com.hackathon.drivescore.core.model.DriveSource
import com.hackathon.drivescore.core.model.MistakeTypes
import com.hackathon.drivescore.core.model.SyncState
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DriveResultMapperTest {
    private val raw = EngineProtocol.json.parseToJsonElement(
        """{"session_id":"s1","user_id":"u1","started_at":"2026-11-06T08:01:12Z","ended_at":"2026-11-06T08:34:40Z",
           "distance_km":null,"score":87.5,"engine_version":"rivian-hackathon-engine/0.1.0",
           "mistakes":[{"type":"phone_call","count":1,"total_duration_s":8.0,"max_duration_s":8.0},
                       {"type":"eyes_off_road","count":1,"total_duration_s":3.0,"max_duration_s":3.0},
                       {"type":"EYES_OFF_ROAD","count":2,"total_duration_s":1.0,"max_duration_s":0.7},
                       {"type":"bad type!","count":1}],
           "events":[{"type":"phone_call","started_at":"2026-11-06T08:01:22Z","duration_s":8.0}],
           "extra":{"penalties":{"PHONE_CALL":9.0}}}""",
    ).jsonObject

    private fun payload(score: Double = 50.0, start: String = "2026-11-06T08:00:00Z", end: String = "2026-11-06T08:10:00Z", count: Int = 0) =
        DriveResultPayload("s", "u", start, end, score = score, mistakes = listOf(MistakeDto("YAWN", count)))

    @Test fun `validation rules from spec 8_5`() {
        assertNull(DriveResultMapper.validate(payload()))
        assertNotNull(DriveResultMapper.validate(payload(score = 100.1)))
        assertNotNull(DriveResultMapper.validate(payload(score = -1.0)))
        assertNotNull(DriveResultMapper.validate(payload(start = "2026-11-06T09:00:00Z")))
        assertNotNull(DriveResultMapper.validate(payload(count = -1)))
        assertNotNull(DriveResultMapper.validate(payload(end = "yesterday")))
    }

    @Test fun `normalize upper-cases types, merges duplicates, drops invalid, keeps extra`() {
        val n = DriveResultMapper.normalizeRaw(raw)
        val mistakes = n["mistakes"]!!.jsonArray.map { it.jsonObject }
        assertEquals(listOf("PHONE_CALL", "EYES_OFF_ROAD"), mistakes.map { it["type"]!!.jsonPrimitive.content })
        val eyes = mistakes[1]
        assertEquals(3, eyes["count"]!!.jsonPrimitive.content.toInt())
        assertEquals(4.0, eyes["total_duration_s"]!!.jsonPrimitive.content.toDouble(), 1e-9)
        assertEquals(3.0, eyes["max_duration_s"]!!.jsonPrimitive.content.toDouble(), 1e-9)
        assertEquals("PHONE_CALL", n["events"]!!.jsonArray[0].jsonObject["type"]!!.jsonPrimitive.content)
        assertTrue(n.containsKey("extra"))
        assertTrue(n["mistakes"]!!.jsonArray.all { MistakeTypes.isValid(it.jsonObject["type"]!!.jsonPrimitive.content) })
    }

    @Test fun `entities carry duration from timestamps and start PENDING`() {
        val m = DriveResultMapper.toEntities(DriveResultMapper.normalizeRaw(raw), "car-1", 42L)
        assertEquals("s1", m.drive.id)
        assertEquals("u1", m.drive.userId)
        assertEquals(2008, m.drive.durationS)
        assertEquals(SyncState.PENDING, m.drive.syncState)
        assertEquals(DriveSource.LOCAL, m.drive.source)
        assertEquals(2, m.mistakes.size)
        assertEquals(1, m.events.size)
    }

    @Test fun `upload body drops routing fields and adds id`() {
        val body = DriveResultMapper.uploadBody(DriveResultMapper.normalizeRaw(raw).toString(), "s1")
        assertEquals("s1", body["id"]!!.jsonPrimitive.content)
        assertFalse(body.containsKey("session_id"))
        assertFalse(body.containsKey("user_id"))
        assertEquals(JsonNull, body["distance_km"])
        assertEquals(87.5, body["score"]!!.jsonPrimitive.content.toDouble(), 0.0)
    }

    @Test fun `upload body truncates engine_version to the backend's 40 chars`() {
        val body = DriveResultMapper.uploadBody(
            """{"session_id":"s1","user_id":"u1","score":1.0,"engine_version":"rivian-hackathon-engine/unknown+bridge-0.1.0"}""", "s1",
        )
        assertEquals("rivian-hackathon-engine/unknown+bridge-0", body["engine_version"]!!.jsonPrimitive.content)
    }

    @Test fun `detail rows list all 8 known types then unknown title-cased`() {
        val rows = MistakeTypes.detailRows(
            listOf(
                com.hackathon.drivescore.core.model.Mistake("YAWN", 2, null, null),
                com.hackathon.drivescore.core.model.Mistake("SEATBELT_OFF", 1, 3.0, 3.0),
            ),
        )
        assertEquals(9, rows.size)
        assertEquals("Seatbelt off", rows.last().first.label)
        assertEquals(2, rows.first { it.first.type == "YAWN" }.second!!.count)
    }
}
