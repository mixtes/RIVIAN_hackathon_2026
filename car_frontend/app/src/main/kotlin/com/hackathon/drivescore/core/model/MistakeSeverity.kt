package com.hackathon.drivescore.core.model

/**
 * How bad one mistake type was in a drive, by its rate per hour of driving (spec §6.5):
 * none → [GOOD], fewer than [WARN_BELOW_PER_HOUR] per hour → [WARN], otherwise [BAD].
 */
enum class MistakeSeverity {
    GOOD, WARN, BAD;

    companion object {
        const val WARN_BELOW_PER_HOUR = 2.0

        /** Mistakes per hour, or null when the drive has no duration. */
        fun perHour(count: Int, driveDurationS: Int): Double? =
            if (driveDurationS <= 0) null else count * 3600.0 / driveDurationS

        fun of(count: Int, driveDurationS: Int): MistakeSeverity {
            if (count <= 0) return GOOD
            val rate = perHour(count, driveDurationS) ?: return BAD
            return if (rate < WARN_BELOW_PER_HOUR) WARN else BAD
        }
    }
}
