package com.hackathon.drivescore.app

import android.app.Application
import androidx.hilt.work.HiltWorkerFactory
import androidx.work.Configuration
import com.hackathon.drivescore.core.network.ConnectivityMonitor
import com.hackathon.drivescore.data.CarRepository
import com.hackathon.drivescore.data.MetaRepository
import com.hackathon.drivescore.data.SyncScheduler
import dagger.hilt.android.HiltAndroidApp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltAndroidApp
class DriveScoreApp : Application(), Configuration.Provider {
    @Inject lateinit var workerFactory: HiltWorkerFactory
    @Inject lateinit var cars: CarRepository
    @Inject lateinit var sync: SyncScheduler
    @Inject lateinit var connectivity: ConnectivityMonitor
    @Inject lateinit var meta: MetaRepository
    @Inject lateinit var foreground: AppForeground
    @Inject lateinit var scope: CoroutineScope

    override val workManagerConfiguration: Configuration
        get() = Configuration.Builder().setWorkerFactory(workerFactory).build()

    override fun onCreate() {
        super.onCreate()
        foreground.attach()
        sync.schedulePeriodic()
        scope.launch {
            // First launch: register the car; if offline, retry via WorkManager (spec §10.1).
            if (cars.ensureRegistered().isFailure) sync.registerCar()
        }
        scope.launch {
            connectivity.online.filter { it }.collect {
                sync.syncNow()
                if (!cars.isRegistered()) sync.registerCar()
                meta.refreshMistakeTypes()
            }
        }
    }
}
