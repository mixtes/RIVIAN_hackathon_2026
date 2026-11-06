package com.hackathon.drivescore.core.database

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Transaction
import androidx.room.Upsert
import com.hackathon.drivescore.core.model.SyncState
import kotlinx.coroutines.flow.Flow

@Dao
interface DriverProfileDao {
    @Query("SELECT * FROM driver_profiles WHERE pendingUnlink = 0 ORDER BY addedAt")
    fun observeActive(): Flow<List<DriverProfileEntity>>

    @Query("SELECT * FROM driver_profiles WHERE userId = :userId")
    suspend fun get(userId: String): DriverProfileEntity?

    @Query("SELECT * FROM driver_profiles WHERE userId = :userId")
    fun observe(userId: String): Flow<DriverProfileEntity?>

    @Query("SELECT COUNT(*) FROM driver_profiles WHERE pendingUnlink = 0")
    suspend fun activeCount(): Int

    @Query("SELECT * FROM driver_profiles WHERE pendingUnlink = 1")
    suspend fun pendingUnlinks(): List<DriverProfileEntity>

    @Upsert
    suspend fun upsert(profile: DriverProfileEntity)

    @Query("DELETE FROM driver_profiles WHERE userId = :userId")
    suspend fun delete(userId: String)

    @Query("UPDATE driver_profiles SET needsReauth = :value WHERE userId = :userId")
    suspend fun setNeedsReauth(userId: String, value: Boolean)
}

data class DriveWithCounts(
    val id: String,
    val userId: String,
    val carId: String,
    val carName: String?,
    val startedAt: Long,
    val endedAt: Long,
    val durationS: Int,
    val score: Double,
    val source: com.hackathon.drivescore.core.model.DriveSource,
    val syncState: SyncState,
    val lastSyncError: String?,
    val mistakeTotal: Int?,
    val localMistakeTotal: Int,
)

@Dao
interface DriveDao {
    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insertDrive(drive: DriveEntity)

    @Upsert
    suspend fun upsertDrive(drive: DriveEntity)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertMistakes(mistakes: List<DriveMistakeEntity>)

    @Insert
    suspend fun insertEvents(events: List<DriveEventEntity>)

    @Query("DELETE FROM drive_mistakes WHERE driveId = :driveId")
    suspend fun deleteMistakes(driveId: String)

    @Query("DELETE FROM drive_events WHERE driveId = :driveId")
    suspend fun deleteEvents(driveId: String)

    @Query("SELECT * FROM drives WHERE id = :id")
    suspend fun get(id: String): DriveEntity?

    @Query("SELECT * FROM drives WHERE id = :id")
    fun observe(id: String): Flow<DriveEntity?>

    @Query("SELECT * FROM drive_mistakes WHERE driveId = :driveId")
    fun observeMistakes(driveId: String): Flow<List<DriveMistakeEntity>>

    @Query("SELECT * FROM drive_events WHERE driveId = :driveId ORDER BY startedAt")
    fun observeEvents(driveId: String): Flow<List<DriveEventEntity>>

    @Query(
        """SELECT d.id, d.userId, d.carId, d.carName, d.startedAt, d.endedAt, d.durationS, d.score, d.source,
                  d.syncState, d.lastSyncError, d.mistakeTotal,
                  COALESCE((SELECT SUM(m.count) FROM drive_mistakes m WHERE m.driveId = d.id), 0) AS localMistakeTotal
           FROM drives d WHERE d.userId = :userId ORDER BY d.endedAt DESC, d.id DESC""",
    )
    fun observeHistory(userId: String): Flow<List<DriveWithCounts>>

    @Query("SELECT * FROM drives WHERE syncState IN ('PENDING', 'SYNCING') AND source = 'LOCAL' ORDER BY createdAt LIMIT :limit")
    suspend fun pendingUploads(limit: Int): List<DriveEntity>

    @Query("SELECT COUNT(*) FROM drives WHERE syncState IN ('PENDING', 'SYNCING') AND source = 'LOCAL'")
    fun observePendingCount(): Flow<Int>

    @Query("SELECT DISTINCT userId FROM drives WHERE syncState IN ('PENDING', 'SYNCING') AND source = 'LOCAL'")
    fun observeUsersWithPending(): Flow<List<String>>

    @Query("SELECT COUNT(*) FROM drives WHERE userId = :userId AND syncState IN ('PENDING', 'SYNCING', 'FAILED_PERMANENT') AND source = 'LOCAL'")
    suspend fun unsyncedCount(userId: String): Int

    @Query("UPDATE drives SET syncState = :state, syncAttempts = syncAttempts + :attemptInc, lastSyncError = :error WHERE id = :id")
    suspend fun updateSync(id: String, state: SyncState, error: String?, attemptInc: Int = 1)

    @Query("SELECT * FROM drives WHERE userId = :userId AND source = 'LOCAL' AND endedAt >= :fromMs")
    suspend fun localDrivesSince(userId: String, fromMs: Long): List<DriveEntity>

    @Query("SELECT * FROM drive_mistakes WHERE driveId IN (:driveIds)")
    suspend fun mistakesFor(driveIds: List<String>): List<DriveMistakeEntity>

    @Query("DELETE FROM drives WHERE userId = :userId")
    suspend fun deleteAllForUser(userId: String)

    @Query("DELETE FROM drives WHERE userId = :userId AND NOT (source = 'LOCAL' AND syncState != 'SYNCED')")
    suspend fun deleteSyncedForUser(userId: String)

    @Transaction
    suspend fun replaceDetail(driveId: String, mistakes: List<DriveMistakeEntity>, events: List<DriveEventEntity>) {
        deleteMistakes(driveId)
        deleteEvents(driveId)
        if (mistakes.isNotEmpty()) insertMistakes(mistakes)
        if (events.isNotEmpty()) insertEvents(events)
    }

    /** One transaction for drive + mistakes + events (spec §11.2). */
    @Transaction
    suspend fun insertResult(drive: DriveEntity, mistakes: List<DriveMistakeEntity>, events: List<DriveEventEntity>) {
        insertDrive(drive)
        if (mistakes.isNotEmpty()) insertMistakes(mistakes)
        if (events.isNotEmpty()) insertEvents(events)
    }
}

@Dao
interface ApiCacheDao {
    @Query("SELECT * FROM api_cache WHERE userId = :userId AND `key` = :key")
    suspend fun get(userId: String, key: String): ApiCacheEntity?

    @Query("SELECT * FROM api_cache WHERE userId = :userId AND `key` = :key")
    fun observe(userId: String, key: String): Flow<ApiCacheEntity?>

    @Upsert
    suspend fun put(entry: ApiCacheEntity)

    @Query("DELETE FROM api_cache WHERE userId = :userId")
    suspend fun deleteForUser(userId: String)
}
