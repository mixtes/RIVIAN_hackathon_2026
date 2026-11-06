package com.hackathon.drivescore.service

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import com.hackathon.drivescore.R
import com.hackathon.drivescore.app.MainActivity
import com.hackathon.drivescore.core.car.VehicleSignals
import com.hackathon.drivescore.core.datastore.AppPreferences
import com.hackathon.drivescore.core.engine.EngineClient
import com.hackathon.drivescore.core.engine.EngineGateway
import com.hackathon.drivescore.data.SyncScheduler
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Foreground service owning the engine connection, the session and alert presentation, so they keep
 * running while the app is in the background (spec §8.1, §9.3).
 */
@AndroidEntryPoint
class EngineConnectionService : LifecycleService() {
    @Inject lateinit var gateway: EngineGateway
    @Inject lateinit var engineClient: EngineClient
    @Inject lateinit var sessionManager: SessionManager
    @Inject lateinit var alertPresenter: AlertPresenter
    @Inject lateinit var prefs: AppPreferences
    @Inject lateinit var sync: SyncScheduler
    @Inject lateinit var vehicleSignals: VehicleSignals

    override fun onCreate() {
        super.onCreate()
        startInForeground()
        gateway.start(prefs.carId.value)
        alertPresenter.start()
        lifecycleScope.launch { sessionManager.restore() }
        lifecycleScope.launch {
            sessionManager.events.collect { if (it is SessionEvent.DriveSaved) sync.syncNow() }
        }
        lifecycleScope.launch {
            prefs.carId.drop(1).distinctUntilChanged().collect { gateway.start(it) }
        }
        lifecycleScope.launch {
            prefs.engineUrl.drop(1).distinctUntilChanged().collect { url -> engineClient.start(url) }
        }
        lifecycleScope.launch {
            prefs.vehicleSignalsEnabled.distinctUntilChanged().collect { on ->
                if (on) vehicleSignals.start { sessionManager.onIgnitionOff() } else vehicleSignals.stop()
            }
        }
    }

    private fun startInForeground() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Driver monitoring", NotificationManager.IMPORTANCE_LOW))
        val pi = PendingIntent.getActivity(this, 1, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val n = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(R.string.service_running))
            .setOngoing(true)
            .setContentIntent(pi)
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIFICATION_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIFICATION_ID, n)
        }
    }

    override fun onDestroy() {
        vehicleSignals.stop()
        alertPresenter.stop()
        gateway.stop()
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL = "engine_service"
        private const val NOTIFICATION_ID = 1001

        fun start(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, EngineConnectionService::class.java))
        }
    }
}
