package com.hackathon.drivescore.core.engine

import com.hackathon.drivescore.core.common.NoopLogger
import com.hackathon.drivescore.core.model.AlertEvent
import com.hackathon.drivescore.core.model.AlertLevel
import com.hackathon.drivescore.core.model.ConnectionStatus
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/** EngineClient + WebSocketEngineGateway against a MockWebServer WebSocket (spec §8, §16). */
class EngineClientTest {
    private lateinit var server: MockWebServer
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    /** One fake engine connection: records what the app sends, lets the test send. */
    private inner class EngineSide : WebSocketListener() {
        init { sides += this }
        val received = LinkedBlockingQueue<Map<String, Any?>>()
        @Volatile var socket: WebSocket? = null
        override fun onOpen(webSocket: WebSocket, response: Response) { socket = webSocket }
        override fun onMessage(webSocket: WebSocket, text: String) {
            val o = Json.parseToJsonElement(text).jsonObject
            received += mapOf("type" to o["type"]!!.jsonPrimitive.content, "payload" to o["payload"]!!.jsonObject)
        }
        fun next(): Map<String, Any?> = received.poll(5, TimeUnit.SECONDS) ?: error("nothing received")
        fun nextOfType(type: String): Map<String, Any?> { while (true) { val m = next(); if (m["type"] == type) return m } }
        fun send(type: String, payload: String) {
            socket!!.send("""{"v":1,"type":"$type","msg_id":"m","ts":"2026-11-06T08:00:00Z","payload":$payload}""")
        }
    }

    @Before fun setUp() { server = MockWebServer().apply { start() } }
    private val sides = mutableListOf<EngineSide>()

    @After fun tearDown() {
        scope.cancel()
        sides.forEach { it.socket?.close(1000, "test over") }
        // MockWebServer waits for open WebSockets to finish; a slow close must not fail a passing test.
        try { server.shutdown() } catch (_: AssertionError) { }
    }

    private fun url() = server.url("/ws").toString().replace("http", "ws")

    private fun gateway(client: EngineClient) = WebSocketEngineGateway(
        client, scope, NoopLogger, { url() }, "1.0.0", ackTimeoutMs = 1_000, helloWaitMs = 300,
    )

    private fun req(id: String = "sess-1") = SessionStartRequest(id, "u1", "Mihajlo", "car-1", 1_762_416_072_000)

    @Test fun `hello on connect, session start acked, alerts and results flow`() = runBlocking {
        val engine = EngineSide()
        server.enqueue(MockResponse().withWebSocketUpgrade(engine))
        val client = EngineClient(OkHttpClient(), scope, NoopLogger)
        val gw = gateway(client)
        gw.start("car-1")
        val hello = engine.nextOfType("hello")
        assertEquals("car-1", (hello["payload"] as Map<*, *>)["car_id"].toString().trim('"'))
        engine.send("hello", """{"engine_version":"0.1.0","active_session_id":null,"supported_actions":["PHONE_CALL"]}""")

        val outcome = async(Dispatchers.Default) { gw.startSession(req()) }
        val start = engine.nextOfType("session.start")
        assertEquals("\"sess-1\"", (start["payload"] as Map<*, *>)["session_id"].toString())
        engine.send("session.ack", """{"session_id":"sess-1"}""")
        assertEquals(StartOutcome.ACKED, withTimeout(5_000) { outcome.await() })

        val alert = async(Dispatchers.Default) { gw.alerts.first() }
        engine.send("alert", """{"alert_id":"a1","session_id":"sess-1","type":"eyes_off_road","level":"HAPTIC","message":"Look!","duration_so_far_s":2.5,"ttl_s":10}""")
        val ev = withTimeout(5_000) { alert.await() } as AlertEvent.Show
        assertEquals("EYES_OFF_ROAD", ev.alert.type)
        assertEquals(AlertLevel.HAPTIC, ev.alert.level)

        val result = async(Dispatchers.Default) { gw.results.first() }
        engine.send("drive.result", """{"session_id":"sess-1","user_id":"u1","started_at":"2026-11-06T08:00:00Z","ended_at":"2026-11-06T08:30:00Z","score":91.0}""")
        assertEquals(91.0, withTimeout(5_000) { result.await() }.payload.score, 0.0)

        gw.ackResult("sess-1")
        assertEquals("\"sess-1\"", (engine.nextOfType("drive.result.ack")["payload"] as Map<*, *>)["session_id"].toString())
        gw.stop()
    }

    @Test fun `no ack retries session start 3 times then reports no response`() = runBlocking {
        val engine = EngineSide()
        server.enqueue(MockResponse().withWebSocketUpgrade(engine))
        val gw = gateway(EngineClient(OkHttpClient(), scope, NoopLogger))
        gw.start("car-1")
        engine.nextOfType("hello")
        assertEquals(StartOutcome.NO_RESPONSE, withTimeout(10_000) { gw.startSession(req()) })
        repeat(3) { engine.nextOfType("session.start") }
        gw.stop()
    }

    @Test fun `after a reconnect the app re-sends hello and the same session, ending a foreign session first`() = runBlocking {
        val first = EngineSide()
        val second = EngineSide()
        server.enqueue(MockResponse().withWebSocketUpgrade(first))
        server.enqueue(MockResponse().withWebSocketUpgrade(second))
        val client = EngineClient(OkHttpClient(), scope, NoopLogger)
        val gw = gateway(client)
        gw.start("car-1")
        first.nextOfType("hello")
        val outcome = async(Dispatchers.Default) { gw.startSession(req("mine")) }
        first.nextOfType("session.start")
        first.send("session.ack", """{"session_id":"mine"}""")
        withTimeout(5_000) { outcome.await() }

        first.socket!!.close(1001, "engine restart") // engine drops the connection
        withTimeout(10_000) { client.status.first { it == ConnectionStatus.RECONNECTING } }
        second.nextOfType("hello")
        second.send("hello", """{"engine_version":"0.1.0","active_session_id":"foreign","supported_actions":[]}""")
        val end = second.nextOfType("session.end")
        assertEquals("\"foreign\"", (end["payload"] as Map<*, *>)["session_id"].toString())
        assertEquals("\"DRIVER_SWITCH\"", (end["payload"] as Map<*, *>)["reason"].toString())
        val resumed = second.nextOfType("session.start")
        assertEquals("\"mine\"", (resumed["payload"] as Map<*, *>)["session_id"].toString())
        assertTrue(client.status.value == ConnectionStatus.CONNECTED)
        gw.stop()
    }
}
