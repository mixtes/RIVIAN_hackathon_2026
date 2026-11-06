package com.hackathon.drivescore.core.engine

import com.hackathon.drivescore.core.common.AppLogger
import com.hackathon.drivescore.core.model.ConnectionStatus
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import kotlin.random.Random

data class TrafficLine(val atMs: Long, val outgoing: Boolean, val text: String)

/**
 * Generic engine WebSocket client (spec §8.1, §13): connect, reconnect with backoff + jitter forever,
 * send, and expose incoming messages and the connection status. The OkHttpClient must be configured
 * with pingInterval = 10 s, so a missing pong fails the socket and triggers a reconnect.
 */
class EngineClient(
    private val okHttp: OkHttpClient,
    private val scope: CoroutineScope,
    private val log: AppLogger,
    private val backoff: Backoff = Backoff(),
    private val random: Random = Random.Default,
    private val nowMs: () -> Long = System::currentTimeMillis,
) {
    private val _status = MutableStateFlow(ConnectionStatus.DISCONNECTED)
    val status: StateFlow<ConnectionStatus> = _status.asStateFlow()

    private val _incoming = MutableSharedFlow<EngineMessage>(extraBufferCapacity = 256)
    val incoming: SharedFlow<EngineMessage> = _incoming.asSharedFlow()

    /** Emits once each time a socket opens (callers send `hello` + resume). */
    private val _opened = MutableSharedFlow<Unit>(extraBufferCapacity = 8)
    val opened: SharedFlow<Unit> = _opened.asSharedFlow()

    private val _traffic = MutableStateFlow<List<TrafficLine>>(emptyList())
    val traffic: StateFlow<List<TrafficLine>> = _traffic.asStateFlow()

    @Volatile private var socket: WebSocket? = null
    private var loop: Job? = null
    @Volatile var url: String? = null
        private set

    fun start(url: String) {
        if (loop?.isActive == true && this.url == url) return
        stop()
        this.url = url
        loop = scope.launch {
            var attempt = 0
            while (isActive) {
                _status.value = if (attempt == 0) ConnectionStatus.CONNECTING else ConnectionStatus.RECONNECTING
                val closed = CompletableDeferred<Unit>()
                var wasOpen = false
                val request = try {
                    Request.Builder().url(url).build()
                } catch (e: IllegalArgumentException) {
                    log.e(TAG, "Invalid engine URL $url", e)
                    _status.value = ConnectionStatus.DISCONNECTED
                    return@launch
                }
                val ws = okHttp.newWebSocket(request, object : WebSocketListener() {
                    override fun onOpen(webSocket: WebSocket, response: Response) {
                        socket = webSocket
                        wasOpen = true
                        attempt = 0
                        _status.value = ConnectionStatus.CONNECTED
                        _opened.tryEmit(Unit)
                    }

                    override fun onMessage(webSocket: WebSocket, text: String) {
                        record(false, text)
                        val msg = EngineCodec.decode(text)
                        when (msg) {
                            is EngineMessage.Unknown -> log.w(TAG, "Ignoring unknown message type ${msg.type}")
                            is EngineMessage.Malformed -> log.w(TAG, "Malformed engine message: ${msg.reason}")
                            else -> Unit
                        }
                        _incoming.tryEmit(msg)
                    }

                    override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                        webSocket.close(1000, null)
                    }

                    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                        closed.complete(Unit)
                    }

                    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                        log.w(TAG, "Engine socket failure: ${t.message}")
                        closed.complete(Unit)
                    }
                })
                try {
                    closed.await()
                } finally {
                    if (socket === ws) socket = null
                    ws.cancel()
                }
                if (!wasOpen) attempt++ else attempt = 1
                _status.value = ConnectionStatus.RECONNECTING
                delay(backoff.delayMs(attempt - 1, random))
            }
        }
    }

    fun stop() {
        loop?.cancel()
        loop = null
        socket?.close(1000, "stop")
        socket = null
        _status.value = ConnectionStatus.DISCONNECTED
    }

    /** Debug: drop the current connection; the loop reconnects. */
    fun dropConnection() {
        socket?.cancel()
    }

    fun send(envelope: Envelope): Boolean {
        val text = EngineCodec.encode(envelope)
        val ok = socket?.send(text) ?: false
        record(true, if (ok) text else "(not sent) $text")
        return ok
    }

    private fun record(outgoing: Boolean, text: String) {
        _traffic.update { (it + TrafficLine(nowMs(), outgoing, text)).takeLast(200) }
    }

    private companion object { const val TAG = "EngineClient" }
}
