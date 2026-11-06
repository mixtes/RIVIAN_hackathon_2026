package com.hackathon.drivescore.data

import com.hackathon.drivescore.core.common.IsoTime
import com.hackathon.drivescore.core.database.DriveEntity
import com.hackathon.drivescore.core.database.DriveEventEntity
import com.hackathon.drivescore.core.database.DriveMistakeEntity
import com.hackathon.drivescore.core.engine.DriveResultPayload
import com.hackathon.drivescore.core.engine.EngineProtocol
import com.hackathon.drivescore.core.model.DriveSource
import com.hackathon.drivescore.core.model.MistakeTypes
import com.hackathon.drivescore.core.model.SyncState
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

data class MappedResult(
    val drive: DriveEntity,
    val mistakes: List<DriveMistakeEntity>,
    val events: List<DriveEventEntity>,
)

/** drive.result → Room entities, plus the upload body (spec §8.5, §11.2, backend §8.6). */
object DriveResultMapper {
    const val MAX_ENGINE_VERSION = 40

    /** App-side validation before persisting (spec §8.5). Returns the reason, or null if valid. */
    fun validate(p: DriveResultPayload): String? {
        if (p.score.isNaN() || p.score < 0.0 || p.score > 100.0) return "score out of range: ${p.score}"
        val start = IsoTime.parse(p.startedAt) ?: return "invalid started_at"
        val end = IsoTime.parse(p.endedAt) ?: return "invalid ended_at"
        if (end < start) return "ended_at before started_at"
        if (p.mistakes.any { it.count < 0 }) return "negative mistake count"
        return null
    }

    /**
     * Normalizes the raw payload for storage/upload: every mistake/event `type` is upper-cased
     * (engine keys are lower-case), entries with an invalid type name are dropped, duplicate mistake
     * types are merged. Unknown fields (e.g. `extra`) are preserved.
     */
    fun normalizeRaw(raw: JsonObject): JsonObject {
        fun normType(el: JsonElement): JsonObject? {
            val o = el as? JsonObject ?: return null
            val t = (o["type"] as? JsonPrimitive)?.content?.let(MistakeTypes::normalize) ?: return null
            if (!MistakeTypes.isValid(t)) return null
            return JsonObject(o + ("type" to JsonPrimitive(t)))
        }
        val mistakes = (raw["mistakes"] as? JsonArray).orEmpty().mapNotNull(::normType)
            .groupBy { it["type"]!!.jsonPrimitive.content }
            .map { (_, list) -> if (list.size == 1) list.first() else mergeMistakes(list) }
        val events = (raw["events"] as? JsonArray).orEmpty().mapNotNull(::normType)
        return JsonObject(raw + mapOf("mistakes" to JsonArray(mistakes), "events" to JsonArray(events)))
    }

    private fun mergeMistakes(list: List<JsonObject>): JsonObject {
        fun d(o: JsonObject, k: String) = (o[k] as? JsonPrimitive)?.content?.toDoubleOrNull()
        val count = list.sumOf { (it["count"] as? JsonPrimitive)?.content?.toIntOrNull() ?: 0 }
        val totals = list.mapNotNull { d(it, "total_duration_s") }
        val maxes = list.mapNotNull { d(it, "max_duration_s") }
        return JsonObject(
            list.first() + mapOf(
                "count" to JsonPrimitive(count),
                "total_duration_s" to (if (totals.isEmpty()) JsonNull else JsonPrimitive(totals.sum())),
                "max_duration_s" to (if (maxes.isEmpty()) JsonNull else JsonPrimitive(maxes.max())),
            ),
        )
    }

    fun toEntities(normalizedRaw: JsonObject, carId: String, nowMs: Long): MappedResult {
        val p = EngineProtocol.json.decodeFromJsonElement(DriveResultPayload.serializer(), normalizedRaw)
        val start = IsoTime.parse(p.startedAt)!!
        val end = IsoTime.parse(p.endedAt)!!
        val drive = DriveEntity(
            id = p.sessionId,
            userId = p.userId,
            carId = carId,
            startedAt = start,
            endedAt = end,
            durationS = ((end - start) / 1000).toInt(),
            distanceKm = p.distanceKm,
            score = p.score,
            engineVersion = p.engineVersion,
            rawJson = normalizedRaw.toString(),
            source = DriveSource.LOCAL,
            syncState = SyncState.PENDING,
            createdAt = nowMs,
        )
        val mistakes = p.mistakes.map {
            DriveMistakeEntity(p.sessionId, it.type, it.count, it.totalDurationS, it.maxDurationS)
        }
        val events = p.events.mapNotNull { e ->
            IsoTime.parse(e.startedAt)?.let { DriveEventEntity(driveId = p.sessionId, type = e.type, startedAt = it, durationS = e.durationS) }
        }
        return MappedResult(drive, mistakes, events)
    }

    /** Upload body = drive.result payload minus routing fields, plus `id` = session_id (backend §8.6). */
    fun uploadBody(rawJson: String, id: String): JsonObject {
        val raw = EngineProtocol.json.parseToJsonElement(rawJson).jsonObject
        val body = raw - setOf("session_id", "user_id") + ("id" to JsonPrimitive(id))
        // Backend column is VARCHAR(40); a longer version string would make the upload fail with 422.
        val version = (raw["engine_version"] as? JsonPrimitive)?.takeIf { it.isString && it.content.length > MAX_ENGINE_VERSION }
        return JsonObject(if (version == null) body else body + ("engine_version" to JsonPrimitive(version.content.take(MAX_ENGINE_VERSION))))
    }
}
