package com.hackathon.drivescore.app

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hackathon.drivescore.core.car.DrivingStateMonitor
import com.hackathon.drivescore.core.datastore.AppPreferences
import com.hackathon.drivescore.core.engine.EngineGateway
import com.hackathon.drivescore.core.model.AppState
import com.hackathon.drivescore.core.model.ConnectionStatus
import com.hackathon.drivescore.core.model.Driver
import com.hackathon.drivescore.core.model.DrivingState
import com.hackathon.drivescore.core.network.ConnectivityMonitor
import com.hackathon.drivescore.data.DriveRepository
import com.hackathon.drivescore.data.DriverRepository
import com.hackathon.drivescore.service.AlertPresenter
import com.hackathon.drivescore.service.DisplayedAlert
import com.hackathon.drivescore.service.SessionEvent
import com.hackathon.drivescore.service.SessionManager
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import javax.inject.Inject

data class StatusBarState(
    val driver: Driver? = null,
    val tracking: Boolean = false,
    val engine: ConnectionStatus = ConnectionStatus.DISCONNECTED,
    val online: Boolean = false,
    val pendingUploads: Int = 0,
    val carRevoked: Boolean = false,
)

@HiltViewModel
class RootViewModel @Inject constructor(
    val sessionManager: SessionManager,
    drivingStateMonitor: DrivingStateMonitor,
    gateway: EngineGateway,
    connectivity: ConnectivityMonitor,
    drives: DriveRepository,
    drivers: DriverRepository,
    prefs: AppPreferences,
    alerts: AlertPresenter,
) : ViewModel() {
    val appState: StateFlow<AppState> = sessionManager.state
    val drivingState: StateFlow<DrivingState> = drivingStateMonitor.state
    val alert: StateFlow<DisplayedAlert?> = alerts.current
    val events: SharedFlow<SessionEvent> = sessionManager.events
    val carName: StateFlow<String?> = prefs.carName

    val statusBar: StateFlow<StatusBarState> = combine(
        sessionManager.state, drivers.observeDrivers(), gateway.status, connectivity.online, drives.observePendingCount(),
    ) { st, list, engine, online, pending ->
        StatusBarState(
            driver = st.unlockedUserId?.let { id -> list.firstOrNull { it.userId == id } },
            tracking = st is AppState.SessionActive || st is AppState.Ending,
            engine = engine, online = online, pendingUploads = pending, carRevoked = prefs.carRevoked.value,
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), StatusBarState())

    fun onUserInteraction() = sessionManager.onUserInteraction()
}
