package com.hackathon.drivescore.data

import com.hackathon.drivescore.core.database.ApiCacheDao
import com.hackathon.drivescore.core.database.DriveDao
import com.hackathon.drivescore.core.database.DriveEntity
import com.hackathon.drivescore.core.database.DriverProfileDao
import com.hackathon.drivescore.core.datastore.AppPreferences
import com.hackathon.drivescore.core.model.SyncState
import com.hackathon.drivescore.core.network.ApiErrors
import com.hackathon.drivescore.core.network.DriveScoreApi
import kotlinx.serialization.json.JsonObject
import retrofit2.Response

class RoomSyncStore(
    private val drives: DriveDao,
    private val profiles: DriverProfileDao,
    private val cache: ApiCacheDao,
    private val prefs: AppPreferences,
) : SyncStore {
    override suspend fun pendingDrives(limit: Int): List<DriveEntity> = drives.pendingUploads(limit)
    override suspend fun needsReauth(userId: String) = profiles.get(userId)?.needsReauth ?: false
    override suspend fun setNeedsReauth(userId: String, value: Boolean) = profiles.setNeedsReauth(userId, value)
    override suspend fun markSyncing(driveId: String) = drives.updateSync(driveId, SyncState.SYNCING, null, attemptInc = 0)

    override suspend fun mark(driveId: String, outcome: UploadOutcome, error: String?) {
        val state = when (outcome) {
            UploadOutcome.SYNCED -> SyncState.SYNCED
            UploadOutcome.FAILED_PERMANENT -> SyncState.FAILED_PERMANENT
            else -> SyncState.PENDING
        }
        drives.updateSync(driveId, state, error)
    }

    override suspend fun pendingUnlinks(): List<String> = profiles.pendingUnlinks().map { it.userId }

    override suspend fun completeUnlink(userId: String) {
        profiles.delete(userId)
        drives.deleteAllForUser(userId)
        cache.deleteForUser(userId)
    }

    override suspend fun setCarRevoked() { prefs.setCarRevoked(true) }
    override suspend fun isCarRevoked(): Boolean = prefs.carRevoked.value
}

class RetrofitSyncRemote(private val api: DriveScoreApi) : SyncRemote {
    override suspend fun upload(driverId: String, body: JsonObject): HttpOutcome = api.uploadDrive(driverId, body).toOutcome()
    override suspend fun unlink(userId: String): HttpOutcome = api.unlinkDriver(userId).toOutcome()

    private fun Response<*>.toOutcome(): HttpOutcome =
        HttpOutcome(code(), if (isSuccessful) null else ApiErrors.parseBody(errorBody()?.string())?.code)
}
