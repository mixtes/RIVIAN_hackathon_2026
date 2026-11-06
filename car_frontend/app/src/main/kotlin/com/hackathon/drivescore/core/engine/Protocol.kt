package com.hackathon.drivescore.core.engine

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.encodeToJsonElement
import kotlinx.serialization.json.jsonObject

/** Engine WebSocket protocol, spec §8. */
object EngineProtocol {
    const val VERSION = 1

    object Types {
        const val HELLO = "hello"
        const val SESSION_START = "session.start"
        const val SESSION_ACK = "session.ack"
        const val SESSION_END = "session.end"
        const val DRIVE_RESULT = "drive.result"
        const val DRIVE_RESULT_ACK = "drive.result.ack"
        const val ALERT = "alert"
        const val ALERT_CLEAR = "alert.clear"
        const val ERROR = "error"
    }

    val json = Json {
        ignoreUnknownKeys = true
        explicitNulls = true
        encodeDefaults = true
        isLenient = true
    }
}

@Serializable
data class Envelope(
    val v: Int = EngineProtocol.VERSION,
    val type: String,
    @SerialName("msg_id") val msgId: String,
    val ts: String,
    val payload: JsonElement = JsonObject(emptyMap()),
)

@Serializable
data class HelloOut(
    @SerialName("app_version") val appVersion: String,
    @SerialName("car_id") val carId: String?,
    val protocol: Int = EngineProtocol.VERSION,
)

@Serializable
data class HelloIn(
    @SerialName("engine_version") val engineVersion: String? = null,
    @SerialName("active_session_id") val activeSessionId: String? = null,
    @SerialName("supported_actions") val supportedActions: List<String> = emptyList(),
)

@Serializable
data class SessionStartPayload(
    @SerialName("session_id") val sessionId: String,
    @SerialName("user_id") val userId: String,
    @SerialName("display_name") val displayName: String,
    @SerialName("car_id") val carId: String?,
    @SerialName("started_at") val startedAt: String,
)

@Serializable
data class SessionRef(@SerialName("session_id") val sessionId: String)

@Serializable
data class SessionEndPayload(
    @SerialName("session_id") val sessionId: String,
    val reason: String,
)

@Serializable
data class MistakeDto(
    val type: String,
    val count: Int,
    @SerialName("total_duration_s") val totalDurationS: Double? = null,
    @SerialName("max_duration_s") val maxDurationS: Double? = null,
)

@Serializable
data class EventDto(
    val type: String,
    @SerialName("started_at") val startedAt: String,
    @SerialName("duration_s") val durationS: Double? = null,
)

/** Canonical drive.result payload (spec §8.5). */
@Serializable
data class DriveResultPayload(
    @SerialName("session_id") val sessionId: String,
    @SerialName("user_id") val userId: String,
    @SerialName("started_at") val startedAt: String,
    @SerialName("ended_at") val endedAt: String,
    @SerialName("distance_km") val distanceKm: Double? = null,
    val score: Double,
    @SerialName("engine_version") val engineVersion: String? = null,
    val mistakes: List<MistakeDto> = emptyList(),
    val events: List<EventDto> = emptyList(),
    val extra: JsonObject? = null,
)

@Serializable
data class AlertPayload(
    @SerialName("alert_id") val alertId: String,
    @SerialName("session_id") val sessionId: String? = null,
    val type: String,
    val level: String? = null,
    val message: String? = null,
    @SerialName("duration_so_far_s") val durationSoFarS: Double? = null,
    @SerialName("started_at") val startedAt: String? = null,
    @SerialName("ttl_s") val ttlS: Double? = null,
)

@Serializable
data class AlertClearPayload(@SerialName("alert_id") val alertId: String)

@Serializable
data class ErrorPayload(
    val code: String,
    val message: String? = null,
    @SerialName("ref_msg_id") val refMsgId: String? = null,
)

/** Decoded incoming message. */
sealed interface EngineMessage {
    data class Hello(val payload: HelloIn) : EngineMessage
    data class SessionAck(val sessionId: String) : EngineMessage
    /** [rawPayload] is the exact payload JSON, kept for upload (spec §11.1 rawJson). */
    data class DriveResult(val payload: DriveResultPayload, val rawPayload: JsonObject) : EngineMessage
    data class Alert(val payload: AlertPayload) : EngineMessage
    data class AlertClear(val alertId: String) : EngineMessage
    data class Error(val payload: ErrorPayload) : EngineMessage
    data class Unknown(val type: String) : EngineMessage
    data class Malformed(val reason: String) : EngineMessage
}

object EngineCodec {
    private val json = EngineProtocol.json

    inline fun <reified T> envelope(type: String, payload: T, msgId: String, ts: String): Envelope =
        Envelope(type = type, msgId = msgId, ts = ts, payload = EngineProtocol.json.encodeToJsonElement(payload))

    fun encode(envelope: Envelope): String = json.encodeToString(Envelope.serializer(), envelope)

    fun decode(text: String): EngineMessage {
        val env = try {
            json.decodeFromString(Envelope.serializer(), text)
        } catch (e: Exception) {
            return EngineMessage.Malformed(e.message ?: "invalid envelope")
        }
        return try {
            when (env.type) {
                EngineProtocol.Types.HELLO -> EngineMessage.Hello(json.decodeFromJsonElement(env.payload))
                EngineProtocol.Types.SESSION_ACK ->
                    EngineMessage.SessionAck(json.decodeFromJsonElement<SessionRef>(env.payload).sessionId)
                EngineProtocol.Types.DRIVE_RESULT ->
                    EngineMessage.DriveResult(json.decodeFromJsonElement(env.payload), env.payload.jsonObject)
                EngineProtocol.Types.ALERT -> EngineMessage.Alert(json.decodeFromJsonElement(env.payload))
                EngineProtocol.Types.ALERT_CLEAR ->
                    EngineMessage.AlertClear(json.decodeFromJsonElement<AlertClearPayload>(env.payload).alertId)
                EngineProtocol.Types.ERROR -> EngineMessage.Error(json.decodeFromJsonElement(env.payload))
                else -> EngineMessage.Unknown(env.type)
            }
        } catch (e: Exception) {
            EngineMessage.Malformed("${env.type}: ${e.message}")
        }
    }
}
