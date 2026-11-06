package com.hackathon.drivescore.service

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.SoundPool
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import androidx.core.app.NotificationCompat
import com.hackathon.drivescore.R
import com.hackathon.drivescore.app.MainActivity
import com.hackathon.drivescore.core.common.AppLogger
import com.hackathon.drivescore.core.model.AlertLevel
import java.util.Locale

/**
 * SoundPool chimes, TextToSpeech, transient-may-duck audio focus with navigation-guidance usage, and
 * heads-up notifications (spec §9.1–9.3).
 */
class AndroidAlertOutput(private val context: Context, private val log: AppLogger) : AlertOutput {
    private val attrs = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_ASSISTANCE_NAVIGATION_GUIDANCE)
        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
        .build()
    private val main = Handler(Looper.getMainLooper())
    private val audio = context.getSystemService(AudioManager::class.java)
    private val focus = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
        .setAudioAttributes(attrs).build()
    private val releaseFocus = Runnable { audio?.abandonAudioFocusRequest(focus) }

    private val soundPool = SoundPool.Builder().setMaxStreams(3).setAudioAttributes(attrs).build()
    private val chimeId = soundPool.load(context, R.raw.chime_warning, 1)
    private val alarmId = soundPool.load(context, R.raw.alarm_critical, 1)
    /** Recently started SoundPool streams, so [stopAudio] can cut them off. */
    private val streams = ArrayDeque<Int>()
    private val secondChime = Runnable { track(soundPool.play(chimeId, 1f, 1f, 1, 0, 1f)) }

    @Volatile private var ttsReady = false
    private val tts: TextToSpeech? = try {
        TextToSpeech(context) { status ->
            ttsReady = status == TextToSpeech.SUCCESS
            if (ttsReady) configureTts() else log.w(TAG, "TTS init failed; sound only")
        }
    } catch (t: Throwable) {
        log.w(TAG, "TTS unavailable", t); null
    }

    private val notifications = context.getSystemService(NotificationManager::class.java)

    init {
        notifications?.createNotificationChannel(
            NotificationChannel(CHANNEL_ALERTS, "Driver alerts", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Real-time driver safety alerts"
                setSound(null, null)
            },
        )
    }

    private fun configureTts() {
        val t = tts ?: return
        t.setAudioAttributes(attrs)
        val res = t.setLanguage(Locale.getDefault())
        if (res == TextToSpeech.LANG_MISSING_DATA || res == TextToSpeech.LANG_NOT_SUPPORTED) t.setLanguage(Locale.US)
    }

    private fun holdFocus(ms: Long) {
        main.removeCallbacks(releaseFocus)
        audio?.requestAudioFocus(focus)
        main.postDelayed(releaseFocus, ms)
    }

    override fun play(kind: SoundKind) {
        holdFocus(3_000)
        when (kind) {
            SoundKind.CHIME -> track(soundPool.play(chimeId, 1f, 1f, 1, 0, 1f))
            SoundKind.DOUBLE_CHIME -> {
                track(soundPool.play(chimeId, 1f, 1f, 1, 0, 1f))
                main.postDelayed(secondChime, 450)
            }
            SoundKind.ALARM -> track(soundPool.play(alarmId, 1f, 1f, 2, 0, 1f))
        }
    }

    private fun track(streamId: Int) {
        if (streamId == 0) return
        synchronized(streams) {
            streams.addLast(streamId)
            while (streams.size > 8) streams.removeFirst()
        }
    }

    override fun stopAudio() {
        main.removeCallbacks(secondChime)
        val ids = synchronized(streams) { streams.toList().also { streams.clear() } }
        ids.forEach { soundPool.stop(it) }
        tts?.stop()
        main.removeCallbacks(releaseFocus)
        audio?.abandonAudioFocusRequest(focus)
    }

    override fun speak(text: String) {
        if (!ttsReady) return
        holdFocus(6_000)
        tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "alert")
    }

    override fun notify(alert: DisplayedAlert) {
        val intent = PendingIntent.getActivity(
            context, 0, Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val critical = alert.level == AlertLevel.CRITICAL || alert.requiresAck
        val n = NotificationCompat.Builder(context, CHANNEL_ALERTS)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("${alert.icon} ${alert.text}")
            .setContentText(alert.level.name)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(if (critical) NotificationCompat.CATEGORY_ALARM else NotificationCompat.CATEGORY_NAVIGATION)
            .setOnlyAlertOnce(false)
            .setContentIntent(intent)
            .apply { if (critical) setFullScreenIntent(intent, true) }
            .apply {
                // Latched (spec §9.3): not swipe-dismissable; the "Dismiss" action does what the overlay button does.
                if (alert.requiresAck) {
                    setOngoing(true)
                    setAutoCancel(false)
                    addAction(R.drawable.ic_notification, context.getString(R.string.alert_dismiss), EngineConnectionService.dismissAlertIntent(context, alert.alertId))
                }
            }
            .build()
        try {
            notifications?.notify(alert.alertId.hashCode(), n)
        } catch (e: SecurityException) {
            log.w(TAG, "Notification permission missing", e)
        }
    }

    override fun cancel(alertId: String) {
        notifications?.cancel(alertId.hashCode())
    }

    override fun cancelAll() {
        // Only alert notifications use this channel's ids; the service notification has a fixed id.
        notifications?.activeNotifications?.filter { it.notification.channelId == CHANNEL_ALERTS }
            ?.forEach { notifications.cancel(it.id) }
    }

    companion object {
        const val CHANNEL_ALERTS = "driver_alerts"
        private const val TAG = "AlertOutput"
    }
}
