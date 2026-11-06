package com.hackathon.drivescore.domain

import java.nio.ByteBuffer
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

/** Stored PIN record {hash, salt, iterations, length} (spec §10.3). Encrypted with Keystore before storage. */
data class PinRecord(val hash: ByteArray, val salt: ByteArray, val iterations: Int, val length: Int) {
    fun serialize(): ByteArray = ByteBuffer.allocate(4 + 4 + 4 + salt.size + 4 + hash.size).apply {
        putInt(VERSION); putInt(iterations); putInt(salt.size); put(salt); putInt(length); put(hash)
    }.array()

    override fun equals(other: Any?) = other is PinRecord && hash.contentEquals(other.hash) &&
        salt.contentEquals(other.salt) && iterations == other.iterations && length == other.length

    override fun hashCode() = hash.contentHashCode()

    companion object {
        private const val VERSION = 1

        fun deserialize(bytes: ByteArray): PinRecord {
            val b = ByteBuffer.wrap(bytes)
            require(b.int == VERSION) { "unknown PIN record version" }
            val iterations = b.int
            val salt = ByteArray(b.int).also { b.get(it) }
            val length = b.int
            val hash = ByteArray(b.remaining()).also { b.get(it) }
            return PinRecord(hash, salt, iterations, length)
        }
    }
}

/** PBKDF2WithHmacSHA256, 16-byte salt, 210,000 iterations, 32-byte output; constant-time compare. */
class PinHasher(
    private val iterations: Int = DEFAULT_ITERATIONS,
    private val random: SecureRandom = SecureRandom(),
) {
    fun hash(pin: String): PinRecord {
        require(isValidPin(pin)) { "PIN must be 4–6 digits" }
        val salt = ByteArray(SALT_LEN).also { random.nextBytes(it) }
        return PinRecord(derive(pin, salt, iterations), salt, iterations, pin.length)
    }

    fun verify(pin: String, record: PinRecord): Boolean {
        if (pin.length != record.length) return false
        val candidate = derive(pin, record.salt, record.iterations)
        return MessageDigest.isEqual(candidate, record.hash)
    }

    private fun derive(pin: String, salt: ByteArray, iterations: Int): ByteArray {
        val chars = pin.toCharArray()
        val spec = PBEKeySpec(chars, salt, iterations, KEY_LEN_BITS)
        try {
            return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).encoded
        } finally {
            spec.clearPassword()
            chars.fill('\u0000')
        }
    }

    companion object {
        const val DEFAULT_ITERATIONS = 210_000
        const val SALT_LEN = 16
        const val KEY_LEN_BITS = 256

        fun isValidPin(pin: String) = pin.length in 4..6 && pin.all { it in '0'..'9' }

        /** Trivial PINs get a (dismissable) warning (spec §6.10). */
        fun isTrivial(pin: String): Boolean {
            if (pin.toSet().size == 1) return true
            val diffs = pin.zipWithNext { a, b -> b - a }.toSet()
            return diffs == setOf(1) || diffs == setOf(-1)
        }
    }
}

/** Lockout state after a check (spec §10.4). */
sealed interface LockoutStatus {
    data object Open : LockoutStatus
    data class LockedOut(val remainingMs: Long) : LockoutStatus
    data object PinDisabled : LockoutStatus
}

/**
 * Lockout policy: 1–4 failures → warning; 5 → 30 s; 6,7,8,9 → 1, 2, 5, 15 min; 10 → PIN disabled.
 * Lockout end is stored as wall clock AND elapsedRealtime; the remaining time is the max of both,
 * capped at the lockout's own duration, so moving the wall clock forward can't bypass it and moving it
 * back can't inflate it. After a reboot (elapsedRealtime reset) only the wall-clock part applies.
 */
object PinLockoutPolicy {
    const val MAX_ATTEMPTS = 10
    const val WARN_UNTIL = 4

    fun lockoutDurationMs(failures: Int): Long? = when (failures) {
        5 -> 30_000L
        6 -> 60_000L
        7 -> 2 * 60_000L
        8 -> 5 * 60_000L
        9 -> 15 * 60_000L
        else -> null
    }

    fun attemptsLeftBeforeLockout(failures: Int): Int = (5 - failures).coerceAtLeast(0)

    fun status(
        failures: Int,
        pinDisabled: Boolean,
        lockoutUntilEpochMs: Long?,
        lockoutUntilElapsedMs: Long?,
        nowMs: Long,
        nowElapsedMs: Long,
    ): LockoutStatus {
        if (pinDisabled || failures >= MAX_ATTEMPTS) return LockoutStatus.PinDisabled
        val duration = lockoutDurationMs(failures) ?: return LockoutStatus.Open
        val wallRemaining = (lockoutUntilEpochMs ?: 0L) - nowMs
        // elapsedRealtime restarts at boot: if "now" is before the lockout started, the device rebooted
        // and only the wall-clock part applies.
        val elapsedUntil = lockoutUntilElapsedMs ?: 0L
        val rebooted = nowElapsedMs < elapsedUntil - duration
        val elapsedRemaining = if (rebooted) 0L else elapsedUntil - nowElapsedMs
        val remaining = maxOf(wallRemaining, elapsedRemaining).coerceAtMost(duration)
        return if (remaining > 0) LockoutStatus.LockedOut(remaining) else LockoutStatus.Open
    }

    data class FailureUpdate(
        val failures: Int,
        val lockoutUntilEpochMs: Long?,
        val lockoutUntilElapsedMs: Long?,
        val pinDisabled: Boolean,
    )

    fun onFailure(previousFailures: Int, nowMs: Long, nowElapsedMs: Long): FailureUpdate {
        val failures = previousFailures + 1
        if (failures >= MAX_ATTEMPTS) return FailureUpdate(failures, null, null, true)
        val d = lockoutDurationMs(failures)
        return FailureUpdate(failures, d?.let { nowMs + it }, d?.let { nowElapsedMs + it }, false)
    }
}
