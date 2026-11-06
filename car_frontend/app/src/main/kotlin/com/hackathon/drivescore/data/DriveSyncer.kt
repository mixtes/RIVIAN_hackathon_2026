package com.hackathon.drivescore.data

import com.hackathon.drivescore.core.common.AppLogger
import com.hackathon.drivescore.core.database.DriveEntity
import kotlinx.serialization.json.JsonObject
import java.io.IOException

/** Upload response classes from spec §11.3. */
enum class UploadOutcome { SYNCED, FAILED_PERMANENT, DRIVER_NOT_LINKED, CAR_REVOKED, RETRY }

data class HttpOutcome(val status: Int, val errorCode: String?)

object UploadClassifier {
    fun classify(r: HttpOutcome): UploadOutcome = when {
        r.status == 200 || r.status == 201 -> UploadOutcome.SYNCED
        r.status == 409 || r.status == 422 -> UploadOutcome.FAILED_PERMANENT
        r.status == 403 && r.errorCode == "DRIVER_NOT_LINKED" -> UploadOutcome.DRIVER_NOT_LINKED
        r.status == 401 || (r.status == 403 && r.errorCode == "CAR_REVOKED") -> UploadOutcome.CAR_REVOKED
        r.status == 408 || r.status == 429 || r.status >= 500 -> UploadOutcome.RETRY
        // Remaining 4xx (400, 403 FORBIDDEN, 404, 413…) won't succeed on retry.
        else -> UploadOutcome.FAILED_PERMANENT
    }
}

/** Remote side of the sync, abstracted for tests. Network problems throw [IOException]. */
interface SyncRemote {
    suspend fun upload(driverId: String, body: JsonObject): HttpOutcome
    suspend fun unlink(userId: String): HttpOutcome
}

/** Local side of the sync (Room + prefs), abstracted for tests. */
interface SyncStore {
    suspend fun pendingDrives(limit: Int): List<DriveEntity>
    suspend fun needsReauth(userId: String): Boolean
    suspend fun setNeedsReauth(userId: String, value: Boolean)
    suspend fun mark(driveId: String, outcome: UploadOutcome, error: String?)
    suspend fun markSyncing(driveId: String)
    suspend fun pendingUnlinks(): List<String>
    /** DELETE succeeded: wipe the profile row and any remaining local data for that user. */
    suspend fun completeUnlink(userId: String)
    suspend fun setCarRevoked()
    suspend fun isCarRevoked(): Boolean
}

enum class SyncRunResult { SUCCESS, RETRY, STOPPED_CAR_REVOKED }

/**
 * The SyncDrivesWorker's logic (spec §11.3): queued unlinks first, then pending/syncing drives oldest
 * first in batches of 20, each response mapped by [UploadClassifier].
 */
class DriveSyncer(
    private val store: SyncStore,
    private val remote: SyncRemote,
    private val log: AppLogger,
    private val batchSize: Int = 20,
    private val maxPerRun: Int = 500,
) {
    suspend fun run(): SyncRunResult {
        if (store.isCarRevoked()) return SyncRunResult.STOPPED_CAR_REVOKED
        var retry = false

        for (userId in store.pendingUnlinks()) {
            try {
                val r = remote.unlink(userId)
                when {
                    r.status in 200..299 || r.status == 404 -> store.completeUnlink(userId)
                    r.status == 401 || (r.status == 403 && r.errorCode == "CAR_REVOKED") -> {
                        store.setCarRevoked(); return SyncRunResult.STOPPED_CAR_REVOKED
                    }
                    r.status >= 500 || r.status == 429 -> retry = true
                    else -> store.completeUnlink(userId)
                }
            } catch (e: IOException) {
                retry = true
            }
        }

        val drives = store.pendingDrives(maxPerRun)
        val skipUsers = mutableSetOf<String>()
        for (batch in drives.chunked(batchSize)) {
            for (d in batch) {
                if (d.userId in skipUsers) continue
                if (store.needsReauth(d.userId)) { skipUsers += d.userId; continue }
                store.markSyncing(d.id)
                val outcome = try {
                    val r = remote.upload(d.userId, DriveResultMapper.uploadBody(d.rawJson, d.id))
                    UploadClassifier.classify(r).also {
                        if (it != UploadOutcome.SYNCED) log.w(TAG, "Upload ${d.id}: ${r.status} ${r.errorCode} → $it")
                    } to r
                } catch (e: IOException) {
                    UploadOutcome.RETRY to null
                }
                val (cls, resp) = outcome
                val err = resp?.let { "${it.status} ${it.errorCode ?: ""}".trim() } ?: "network"
                when (cls) {
                    UploadOutcome.SYNCED -> store.mark(d.id, cls, null)
                    UploadOutcome.FAILED_PERMANENT -> store.mark(d.id, cls, err)
                    UploadOutcome.DRIVER_NOT_LINKED -> {
                        store.mark(d.id, cls, err)
                        store.setNeedsReauth(d.userId, true)
                        skipUsers += d.userId
                    }
                    UploadOutcome.CAR_REVOKED -> {
                        store.mark(d.id, cls, err)
                        store.setCarRevoked()
                        return SyncRunResult.STOPPED_CAR_REVOKED
                    }
                    UploadOutcome.RETRY -> {
                        store.mark(d.id, cls, err)
                        return SyncRunResult.RETRY
                    }
                }
            }
        }
        return if (retry) SyncRunResult.RETRY else SyncRunResult.SUCCESS
    }

    private companion object { const val TAG = "DriveSyncer" }
}
