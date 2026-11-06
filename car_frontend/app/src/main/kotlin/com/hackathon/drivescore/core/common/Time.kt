package com.hackathon.drivescore.core.common

import java.time.Instant
import java.time.format.DateTimeFormatter

object IsoTime {
    fun format(epochMs: Long): String = DateTimeFormatter.ISO_INSTANT.format(Instant.ofEpochMilli(epochMs))

    /** Parses ISO-8601 with offset or Z. Returns null on failure. */
    fun parse(value: String?): Long? = try {
        value?.let { java.time.OffsetDateTime.parse(it).toInstant().toEpochMilli() }
    } catch (_: Exception) {
        try { value?.let { Instant.parse(it).toEpochMilli() } } catch (_: Exception) { null }
    }
}
