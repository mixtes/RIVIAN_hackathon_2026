package com.hackathon.drivescore.core.common

import android.os.SystemClock

/** Injectable clock: wall time + monotonic time since boot (spec §10.4 lockout uses both). */
interface Clock {
    fun nowMs(): Long
    fun elapsedRealtimeMs(): Long
}

object SystemClockImpl : Clock {
    override fun nowMs(): Long = System.currentTimeMillis()
    override fun elapsedRealtimeMs(): Long = SystemClock.elapsedRealtime()
}
