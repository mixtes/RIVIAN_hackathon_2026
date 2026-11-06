package com.hackathon.drivescore.core.database

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import com.hackathon.drivescore.core.model.DriveSource
import com.hackathon.drivescore.core.model.SyncState

/** Room schema from spec §11.1 (+ needsReauth, see spec note). */
@Entity(tableName = "driver_profiles")
data class DriverProfileEntity(
    @PrimaryKey val userId: String,
    val username: String,
    val displayName: String,
    val avatarColor: String,
    val regionCode: String,
    val encryptedPinRecord: ByteArray,
    val failedAttempts: Int = 0,
    val lockoutUntilEpochMs: Long? = null,
    val lockoutUntilElapsedMs: Long? = null,
    val pinDisabled: Boolean = false,
    val addedAt: Long,
    val lastUsedAt: Long?,
    val pendingUnlink: Boolean = false,
    /** 403 DRIVER_NOT_LINKED on upload: "Sign in again to upload" (spec §11.3). */
    val needsReauth: Boolean = false,
) {
    override fun equals(other: Any?): Boolean =
        other is DriverProfileEntity && other.userId == userId && other.failedAttempts == failedAttempts &&
            other.lockoutUntilEpochMs == lockoutUntilEpochMs && other.pinDisabled == pinDisabled &&
            other.displayName == displayName && other.avatarColor == avatarColor && other.regionCode == regionCode &&
            other.pendingUnlink == pendingUnlink && other.needsReauth == needsReauth &&
            other.encryptedPinRecord.contentEquals(encryptedPinRecord)

    override fun hashCode(): Int = userId.hashCode()
}

@Entity(tableName = "drives", indices = [Index("userId"), Index("syncState")])
data class DriveEntity(
    @PrimaryKey val id: String,
    val userId: String,
    val carId: String,
    val carName: String? = null,
    val startedAt: Long,
    val endedAt: Long,
    val durationS: Int,
    val distanceKm: Double?,
    val score: Double,
    val engineVersion: String?,
    val rawJson: String,
    val source: DriveSource,
    val syncState: SyncState,
    val syncAttempts: Int = 0,
    val lastSyncError: String? = null,
    val createdAt: Long,
    /** For REMOTE list items: true once the detail (mistakes/events) has been fetched. */
    val detailLoaded: Boolean = true,
    /** For REMOTE list items: total mistake count from the list summary. */
    val mistakeTotal: Int? = null,
)

@Entity(tableName = "drive_mistakes", primaryKeys = ["driveId", "type"])
data class DriveMistakeEntity(
    val driveId: String,
    val type: String,
    val count: Int,
    val totalDurationS: Double?,
    val maxDurationS: Double?,
)

@Entity(tableName = "drive_events", indices = [Index("driveId")])
data class DriveEventEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val driveId: String,
    val type: String,
    val startedAt: Long,
    val durationS: Double?,
)

@Entity(tableName = "api_cache", primaryKeys = ["userId", "key"])
data class ApiCacheEntity(
    val userId: String,
    val key: String,
    val json: String,
    val fetchedAt: Long,
)
