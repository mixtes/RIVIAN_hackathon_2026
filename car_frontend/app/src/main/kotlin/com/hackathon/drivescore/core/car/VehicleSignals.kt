package com.hackathon.drivescore.core.car

import android.car.Car
import android.car.VehiclePropertyIds
import android.car.hardware.CarPropertyValue
import android.car.hardware.property.CarPropertyManager
import android.content.Context
import android.content.pm.PackageManager
import com.hackathon.drivescore.core.common.AppLogger

/**
 * Optional ignition signal (spec §7.4), behind a feature flag. Ignition OFF/LOCK → callback.
 * Hints only: the engine remains the authority for when a drive ends.
 */
class VehicleSignals(
    private val context: Context,
    private val monitor: DrivingStateMonitor,
    private val log: AppLogger,
) {
    private var callback: CarPropertyManager.CarPropertyEventCallback? = null
    private var manager: CarPropertyManager? = null

    fun start(onIgnitionOff: () -> Unit): Boolean {
        if (callback != null) return true
        if (context.checkSelfPermission(Car.PERMISSION_POWERTRAIN) != PackageManager.PERMISSION_GRANTED) {
            log.w(TAG, "CAR_POWERTRAIN not granted; vehicle signals disabled")
            return false
        }
        return try {
            val mgr = monitor.carOrNull()?.getCarManager(Car.PROPERTY_SERVICE) as? CarPropertyManager ?: return false
            val cb = object : CarPropertyManager.CarPropertyEventCallback {
                override fun onChangeEvent(value: CarPropertyValue<*>) {
                    val v = value.value as? Int ?: return
                    // VehicleIgnitionState: 1 = LOCK, 2 = OFF
                    if (v == 1 || v == 2) onIgnitionOff()
                }

                override fun onErrorEvent(propId: Int, zone: Int) = Unit
            }
            mgr.registerCallback(cb, VehiclePropertyIds.IGNITION_STATE, CarPropertyManager.SENSOR_RATE_ONCHANGE)
            manager = mgr
            callback = cb
            true
        } catch (t: Throwable) {
            log.w(TAG, "Ignition signal unavailable", t)
            false
        }
    }

    fun stop() {
        callback?.let { manager?.unregisterCallback(it) }
        callback = null
    }

    private companion object { const val TAG = "VehicleSignals" }
}
