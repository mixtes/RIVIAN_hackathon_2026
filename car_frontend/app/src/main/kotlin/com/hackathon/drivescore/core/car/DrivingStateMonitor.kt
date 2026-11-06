package com.hackathon.drivescore.core.car

import android.car.Car
import android.car.drivingstate.CarUxRestrictionsManager
import android.content.Context
import com.hackathon.drivescore.core.common.AppLogger
import com.hackathon.drivescore.core.model.DrivingState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn

/**
 * PARKED / MOVING from CarUxRestrictionsManager: isRequiresDistractionOptimization() → MOVING (spec §7.2).
 * The debug override (spec §15.1) wins when set.
 */
class DrivingStateMonitor(
    context: Context,
    override: StateFlow<DrivingState?>,
    scope: CoroutineScope,
    private val log: AppLogger,
) {
    private val fromCar = MutableStateFlow(DrivingState.PARKED)
    private var car: Car? = null

    /** True when the car service is reachable (false on non-automotive devices). */
    var carServiceAvailable: Boolean = false
        private set

    val state: StateFlow<DrivingState> = combine(fromCar, override) { car, ov -> ov ?: car }
        .stateIn(scope, SharingStarted.Eagerly, DrivingState.PARKED)

    init {
        try {
            val c = Car.createCar(context)
            car = c
            val mgr = c?.getCarManager(Car.CAR_UX_RESTRICTION_SERVICE) as? CarUxRestrictionsManager
            if (mgr != null) {
                carServiceAvailable = true
                fromCar.value = map(mgr.currentCarUxRestrictions?.isRequiresDistractionOptimization == true)
                mgr.registerListener { r -> fromCar.value = map(r.isRequiresDistractionOptimization) }
            }
        } catch (t: Throwable) {
            log.w(TAG, "Car service unavailable; driving state defaults to PARKED", t)
        }
    }

    private fun map(requiresDo: Boolean) = if (requiresDo) DrivingState.MOVING else DrivingState.PARKED

    fun carOrNull(): Car? = car

    private companion object { const val TAG = "DrivingState" }
}
