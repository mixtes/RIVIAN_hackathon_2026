package com.hackathon.drivescore.feature.drive

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.hackathon.drivescore.R
import com.hackathon.drivescore.app.LocalDrivingState
import com.hackathon.drivescore.core.common.Clock
import com.hackathon.drivescore.core.common.Formatters
import com.hackathon.drivescore.core.designsystem.CarColors
import com.hackathon.drivescore.core.designsystem.Chip
import com.hackathon.drivescore.core.designsystem.PrimaryButton
import com.hackathon.drivescore.core.designsystem.PulsingDot
import com.hackathon.drivescore.core.designsystem.VSpace
import com.hackathon.drivescore.core.engine.EngineGateway
import com.hackathon.drivescore.core.model.AppState
import com.hackathon.drivescore.core.model.ConnectionStatus
import com.hackathon.drivescore.core.model.DrivingState
import com.hackathon.drivescore.service.SessionManager
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.stateIn
import javax.inject.Inject

data class DriveUi(val name: String = "", val elapsedMs: Long = 0, val engine: ConnectionStatus = ConnectionStatus.DISCONNECTED, val ending: Boolean = false)

@HiltViewModel
class DriveViewModel @Inject constructor(
    private val session: SessionManager,
    gateway: EngineGateway,
    clock: Clock,
) : ViewModel() {
    private val ticker = flow { while (true) { emit(clock.nowMs()); delay(1_000) } }
    val ui: StateFlow<DriveUi> = combine(session.state, gateway.status, ticker) { st, engine, now ->
        when (st) {
            is AppState.SessionActive -> DriveUi(st.session.displayName, now - st.session.startedAt, engine, false)
            is AppState.Ending -> DriveUi(st.session.displayName, now - st.session.startedAt, engine, true)
            else -> DriveUi(engine = engine)
        }
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), DriveUi())

    fun endDrive() = session.endDrive()
}

/** Drive screen (spec §6.3): glanceable, no live score. End drive only while parked. */
@Composable
fun DriveScreen(vm: DriveViewModel = hiltViewModel()) {
    val ui by vm.ui.collectAsStateWithLifecycle()
    val parked = LocalDrivingState.current == DrivingState.PARKED
    Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(20.dp)) {
            PulsingDot(CarColors.Red, 28.dp)
            Text(stringResource(R.string.tracking_name, ui.name), style = MaterialTheme.typography.displaySmall)
        }
        VSpace(16.dp)
        Text(Formatters.elapsed(ui.elapsedMs), style = MaterialTheme.typography.displayMedium, color = CarColors.Muted)
        VSpace(24.dp)
        if (ui.engine == ConnectionStatus.CONNECTED) Chip(stringResource(R.string.engine_ok), CarColors.Green.copy(alpha = 0.25f))
        else Chip(stringResource(R.string.reconnecting), CarColors.Amber.copy(alpha = 0.35f))
        VSpace(48.dp)
        when {
            ui.ending -> Text(stringResource(R.string.waiting_for_result), style = MaterialTheme.typography.titleLarge)
            parked -> PrimaryButton(stringResource(R.string.end_drive), vm::endDrive, Modifier.width(360.dp), color = CarColors.Red)
        }
    }
}
