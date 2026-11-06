package com.hackathon.drivescore.data

import android.content.Context
import androidx.hilt.work.HiltWorker
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import dagger.assisted.Assisted
import dagger.assisted.AssistedInject
import java.util.concurrent.TimeUnit

/** Uploads queued drives and queued unlinks (spec §11.3). */
@HiltWorker
class SyncDrivesWorker @AssistedInject constructor(
    @Assisted context: Context,
    @Assisted params: WorkerParameters,
    private val syncer: DriveSyncer,
) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = when (syncer.run()) {
        SyncRunResult.SUCCESS -> Result.success()
        SyncRunResult.RETRY -> Result.retry()
        SyncRunResult.STOPPED_CAR_REVOKED -> Result.success()
    }
}

/** Retries car registration when offline on first launch (spec §10.1). */
@HiltWorker
class RegisterCarWorker @AssistedInject constructor(
    @Assisted context: Context,
    @Assisted params: WorkerParameters,
    private val cars: CarRepository,
) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result =
        if (cars.ensureRegistered().isSuccess) Result.success() else Result.retry()
}

class SyncScheduler(private val context: Context) {
    private val wm get() = WorkManager.getInstance(context)
    private val online = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    fun syncNow() {
        val req = OneTimeWorkRequestBuilder<SyncDrivesWorker>()
            .setConstraints(online)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        wm.enqueueUniqueWork(SYNC_WORK, ExistingWorkPolicy.APPEND_OR_REPLACE, req)
    }

    fun schedulePeriodic() {
        val req = PeriodicWorkRequestBuilder<SyncDrivesWorker>(30, TimeUnit.MINUTES).setConstraints(online).build()
        wm.enqueueUniquePeriodicWork(SYNC_PERIODIC, ExistingPeriodicWorkPolicy.KEEP, req)
    }

    fun registerCar() {
        val req = OneTimeWorkRequestBuilder<RegisterCarWorker>()
            .setConstraints(online)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        wm.enqueueUniqueWork(REGISTER_WORK, ExistingWorkPolicy.KEEP, req)
    }

    companion object {
        const val SYNC_WORK = "sync-drives"
        const val SYNC_PERIODIC = "sync-drives-periodic"
        const val REGISTER_WORK = "register-car"
    }
}
