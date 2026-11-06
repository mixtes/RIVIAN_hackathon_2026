package com.hackathon.drivescore.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PinHasherTest {
    private val fast = PinHasher(iterations = 1_000)

    @Test fun `hash and verify round trip`() {
        val r = fast.hash("4821")
        assertTrue(fast.verify("4821", r))
        assertFalse(fast.verify("4822", r))
        assertFalse(fast.verify("48210", r))
        assertEquals(4, r.length)
        assertEquals(16, r.salt.size)
        assertEquals(32, r.hash.size)
    }

    @Test fun `salts are random so equal pins give different hashes`() {
        assertNotEquals(fast.hash("1357").hash.toList(), fast.hash("1357").hash.toList())
    }

    @Test fun `record serialization round trip`() {
        val r = fast.hash("975310")
        val back = PinRecord.deserialize(r.serialize())
        assertEquals(r, back)
        assertTrue(fast.verify("975310", back))
    }

    @Test fun `default is PBKDF2 with 210k iterations`() {
        val r = PinHasher().hash("2468")
        assertEquals(210_000, r.iterations)
        assertTrue(PinHasher().verify("2468", r))
    }

    @Test fun `pin validity and trivial pins`() {
        assertTrue(PinHasher.isValidPin("0000"))
        assertTrue(PinHasher.isValidPin("123456"))
        assertFalse(PinHasher.isValidPin("123"))
        assertFalse(PinHasher.isValidPin("1234567"))
        assertFalse(PinHasher.isValidPin("12a4"))
        assertTrue(PinHasher.isTrivial("0000"))
        assertTrue(PinHasher.isTrivial("1234"))
        assertTrue(PinHasher.isTrivial("98765"))
        assertFalse(PinHasher.isTrivial("4821"))
    }
}

class PinLockoutPolicyTest {
    private val wall = 1_700_000_000_000L
    private val boot = 50_000_000L

    @Test fun `failures 1 to 4 only warn`() {
        var f = 0
        repeat(4) {
            val u = PinLockoutPolicy.onFailure(f, wall, boot)
            f = u.failures
            assertEquals(null, u.lockoutUntilEpochMs)
            assertEquals(LockoutStatus.Open, PinLockoutPolicy.status(f, false, u.lockoutUntilEpochMs, u.lockoutUntilElapsedMs, wall, boot))
        }
        assertEquals(1, PinLockoutPolicy.attemptsLeftBeforeLockout(4))
    }

    @Test fun `escalating lockouts then disabled at 10`() {
        val expected = mapOf(5 to 30_000L, 6 to 60_000L, 7 to 120_000L, 8 to 300_000L, 9 to 900_000L)
        for ((failures, dur) in expected) {
            val u = PinLockoutPolicy.onFailure(failures - 1, wall, boot)
            val s = PinLockoutPolicy.status(u.failures, u.pinDisabled, u.lockoutUntilEpochMs, u.lockoutUntilElapsedMs, wall, boot)
            assertEquals(LockoutStatus.LockedOut(dur), s)
            val after = PinLockoutPolicy.status(u.failures, false, u.lockoutUntilEpochMs, u.lockoutUntilElapsedMs, wall + dur, boot + dur)
            assertEquals(LockoutStatus.Open, after)
        }
        val tenth = PinLockoutPolicy.onFailure(9, wall, boot)
        assertTrue(tenth.pinDisabled)
        assertEquals(LockoutStatus.PinDisabled, PinLockoutPolicy.status(10, true, null, null, wall, boot))
    }

    @Test fun `moving the wall clock forward does not bypass the lockout`() {
        val u = PinLockoutPolicy.onFailure(4, wall, boot) // 30 s
        val s = PinLockoutPolicy.status(5, false, u.lockoutUntilEpochMs, u.lockoutUntilElapsedMs, wall + 3_600_000, boot + 1_000)
        assertEquals(LockoutStatus.LockedOut(29_000), s)
    }

    @Test fun `moving the wall clock back does not inflate the lockout`() {
        val u = PinLockoutPolicy.onFailure(4, wall, boot)
        val s = PinLockoutPolicy.status(5, false, u.lockoutUntilEpochMs, u.lockoutUntilElapsedMs, wall - 3_600_000, boot + 10_000)
        assertEquals(LockoutStatus.LockedOut(30_000), s)
    }

    @Test fun `after a reboot only the wall clock applies`() {
        val u = PinLockoutPolicy.onFailure(5, wall, boot) // 60 s
        // rebooted 10 s later: elapsedRealtime restarted near 0
        val s = PinLockoutPolicy.status(6, false, u.lockoutUntilEpochMs, u.lockoutUntilElapsedMs, wall + 10_000, 2_000)
        assertEquals(LockoutStatus.LockedOut(50_000), s)
        val later = PinLockoutPolicy.status(6, false, u.lockoutUntilEpochMs, u.lockoutUntilElapsedMs, wall + 61_000, 53_000)
        assertEquals(LockoutStatus.Open, later)
    }
}
