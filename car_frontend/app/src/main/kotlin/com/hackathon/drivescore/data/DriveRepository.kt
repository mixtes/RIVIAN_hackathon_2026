package com.hackathon.drivescore.data

import androidx.room.withTransaction
import com.hackathon.drivescore.core.common.AppLogger
import com.hackathon.drivescore.core.common.Clock
import com.hackathon.drivescore.core.common.IsoTime
import com.hackathon.drivescore.core.database.AppDatabase
import com.hackathon.drivescore.core.database.DriveEntity
import com.hackathon.drivescore.core.database.DriveEventEntity
import com.hackathon.drivescore.core.database.DriveMistakeEntity
import com.hackathon.drivescore.core.datastore.AppPreferences
import com.hackathon.drivescore.core.engine.IncomingDriveResult
import com.hackathon.drivescore.core.model.Drive
import com.hackathon.drivescore.core.model.DriveEvent
import com.hackathon.drivescore.core.model.DriveSource
import com.hackathon.drivescore.core.model.Mistake
import com.hackathon.drivescore.core.model.SyncState
import com.hackathon.drivescore.core.network.DriveScoreApi
import com.hackathon.drivescore.core.network.apiCall
import com.hackathon.drivescore.service.DriveResultSink
import com.hackathon.drivescore.service.PersistOutcome
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.map

data class HistoryItem(
    val id: String,
    val startedAt: Long,
    val endedAt: Long,
    val durationS: Int,
    val score: Double,
    val carName: String?,
    val otherCar: Boolean,
    val mistakeCount: Int,
    val syncState: SyncState,
    val source: DriveSource,
)

/** Drives: persistence of engine results, history (local + remote), detail (spec §6.6, §11). */
class DriveRepository(
    private val db: AppDatabase,
    private val api: DriveScoreApi,
    private val prefs: AppPreferences,
    private val clock: Clock,
    private val log: AppLogger,
) : DriveResultSink {
    private val drives = db.drives()
    private val profiles = db.driverProfiles()

    /** Validate → one Room transaction (spec §11.2 step 1). */
    override suspend fun persist(result: IncomingDriveResult): PersistOutcome {
        val p = result.payload
        DriveResultMapper.validate(p)?.let { return PersistOutcome.Invalid(it) }
        drives.get(p.sessionId)?.let { return PersistOutcome.Saved(it.id, it.userId, duplicate = true) }
        if (profiles.get(p.userId) == null) return PersistOutcome.UnknownUser
        val carId = prefs.currentCarId() ?: "unregistered"
        val mapped = DriveResultMapper.toEntities(DriveResultMapper.normalizeRaw(result.rawPayload), carId, clock.nowMs())
        return try {
            db.withTransaction { drives.insertResult(mapped.drive, mapped.mistakes, mapped.events) }
            PersistOutcome.Saved(mapped.drive.id, mapped.drive.userId, duplicate = false)
        } catch (e: android.database.sqlite.SQLiteConstraintException) {
            PersistOutcome.Saved(mapped.drive.id, mapped.drive.userId, duplicate = true)
        }
    }

    fun observeHistory(userId: String): Flow<List<HistoryItem>> {
        val myCar = prefs.carId.value
        return drives.observeHistory(userId).map { rows ->
            rows.map {
                HistoryItem(
                    id = it.id, startedAt = it.startedAt, endedAt = it.endedAt, durationS = it.durationS, score = it.score,
                    carName = it.carName, otherCar = it.carId != myCar && it.source == DriveSource.REMOTE,
                    mistakeCount = it.mistakeTotal ?: it.localMistakeTotal, syncState = it.syncState, source = it.source,
                )
            }
        }
    }

    /** One page of server history (all cars), written into Room as REMOTE/SYNCED (spec §11.4). Returns next cursor. */
    suspend fun fetchHistoryPage(userId: String, cursor: String?): Result<String?> =
        apiCall { api.drives(userId, 20, cursor) }.map { page ->
            for (item in page.items) {
                val existing = drives.get(item.id)
                if (existing != null && existing.source == DriveSource.LOCAL) {
                    if (existing.syncState != SyncState.SYNCED && existing.syncState != SyncState.FAILED_PERMANENT) {
                        drives.updateSync(existing.id, SyncState.SYNCED, null, attemptInc = 0)
                    }
                    continue
                }
                drives.upsertDrive(
                    DriveEntity(
                        id = item.id, userId = userId, carId = item.car?.id ?: "", carName = item.car?.name,
                        startedAt = IsoTime.parse(item.startedAt) ?: 0, endedAt = IsoTime.parse(item.endedAt) ?: 0,
                        durationS = item.durationS, distanceKm = null, score = item.score, engineVersion = null,
                        rawJson = "{}", source = DriveSource.REMOTE, syncState = SyncState.SYNCED, createdAt = clock.nowMs(),
                        detailLoaded = existing?.detailLoaded ?: false, mistakeTotal = item.mistakesSummary?.totalCount,
                    ),
                )
            }
            page.nextCursor
        }

    fun observeDrive(driveId: String): Flow<Drive?> = combine(
        drives.observe(driveId), drives.observeMistakes(driveId), drives.observeEvents(driveId),
    ) { d, m, e -> d?.toDrive(m, e) }

    /** Server-only drives: fetch the detail once and cache it (spec §6.6). */
    suspend fun ensureDetail(userId: String, driveId: String) {
        val d = drives.get(driveId) ?: return
        if (d.source != DriveSource.REMOTE || d.detailLoaded) return
        apiCall { api.drive(userId, driveId) }.onSuccess { dto ->
            db.withTransaction {
                drives.replaceDetail(
                    driveId,
                    dto.mistakes.map { DriveMistakeEntity(driveId, it.type, it.count, it.totalDurationS, it.maxDurationS) },
                    dto.events.mapNotNull { ev -> IsoTime.parse(ev.startedAt)?.let { DriveEventEntity(driveId = driveId, type = ev.type, startedAt = it, durationS = ev.durationS) } },
                )
                drives.upsertDrive(d.copy(detailLoaded = true, engineVersion = dto.engineVersion))
            }
        }.onFailure { log.w(TAG, "Detail fetch failed for $driveId: ${it.message}") }
    }

    suspend fun markForRetry(driveId: String) = drives.updateSync(driveId, SyncState.PENDING, null, attemptInc = 0)

    fun observePendingCount(): Flow<Int> = drives.observePendingCount()

    suspend fun localDrivesSince(userId: String, fromMs: Long): List<Pair<DriveEntity, List<DriveMistakeEntity>>> {
        val list = drives.localDrivesSince(userId, fromMs)
        val mistakes = if (list.isEmpty()) emptyList() else drives.mistakesFor(list.map { it.id })
        val byDrive = mistakes.groupBy { it.driveId }
        return list.map { it to byDrive[it.id].orEmpty() }
    }

    private fun DriveEntity.toDrive(m: List<DriveMistakeEntity>, e: List<DriveEventEntity>) = Drive(
        id = id, userId = userId, carId = carId, carName = carName, startedAt = startedAt, endedAt = endedAt,
        durationS = durationS, score = score, engineVersion = engineVersion, source = source, syncState = syncState,
        lastSyncError = lastSyncError,
        mistakes = m.map { Mistake(it.type, it.count, it.totalDurationS, it.maxDurationS) },
        events = e.map { DriveEvent(it.type, it.startedAt, it.durationS) },
        mistakeTotal = if (m.isEmpty()) (mistakeTotal ?: 0) else m.sumOf { it.count },
    )

    private companion object { const val TAG = "DriveRepository" }
}
