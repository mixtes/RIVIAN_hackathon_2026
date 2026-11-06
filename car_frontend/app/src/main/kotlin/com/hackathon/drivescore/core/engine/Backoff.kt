package com.hackathon.drivescore.core.engine

import kotlin.random.Random

/** Reconnect backoff: 0.5, 1, 2, 4, 8 s, capped at 15 s, ±20 % jitter (spec §8.1). */
class Backoff(
    private val baseMs: Long = 500,
    private val capMs: Long = 15_000,
    private val jitter: Double = 0.2,
) {
    fun nominalMs(attempt: Int): Long {
        val shift = attempt.coerceIn(0, 20)
        return (baseMs shl shift).coerceAtMost(capMs)
    }

    fun delayMs(attempt: Int, random: Random = Random.Default): Long {
        val nominal = nominalMs(attempt)
        val factor = 1.0 + (random.nextDouble() * 2 - 1) * jitter
        return (nominal * factor).toLong().coerceAtLeast(0)
    }
}
