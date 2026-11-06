package com.hackathon.drivescore.core.model

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class MistakeSeverityTest {
    private val hour = 3600

    @Test fun zeroIsGood() = assertEquals(MistakeSeverity.GOOD, MistakeSeverity.of(0, hour))

    @Test fun zeroIsGoodEvenWithoutDuration() = assertEquals(MistakeSeverity.GOOD, MistakeSeverity.of(0, 0))

    @Test fun belowTwoPerHourIsWarn() {
        assertEquals(MistakeSeverity.WARN, MistakeSeverity.of(1, hour)) // 1/h
        assertEquals(MistakeSeverity.WARN, MistakeSeverity.of(3, 2 * hour)) // 1.5/h
    }

    @Test fun exactlyTwoPerHourIsBad() = assertEquals(MistakeSeverity.BAD, MistakeSeverity.of(2, hour))

    @Test fun shortDriveWithOneMistakeIsBad() = assertEquals(MistakeSeverity.BAD, MistakeSeverity.of(1, 10 * 60)) // 6/h

    @Test fun mistakesWithoutDurationAreBad() = assertEquals(MistakeSeverity.BAD, MistakeSeverity.of(1, 0))

    @Test fun perHour() {
        assertEquals(6.0, MistakeSeverity.perHour(1, 600)!!, 1e-9)
        assertNull(MistakeSeverity.perHour(1, 0))
    }
}
