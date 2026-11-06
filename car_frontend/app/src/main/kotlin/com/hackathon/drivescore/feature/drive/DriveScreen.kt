package com.hackathon.drivescore.feature.drive

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.hackathon.drivescore.R
import com.hackathon.drivescore.app.LocalDrivingState
import com.hackathon.drivescore.core.designsystem.CarColors
import com.hackathon.drivescore.core.designsystem.CarDimens
import com.hackathon.drivescore.core.designsystem.Chip
import com.hackathon.drivescore.core.designsystem.DriveBackdrop
import com.hackathon.drivescore.core.designsystem.PrimaryButton
import com.hackathon.drivescore.core.designsystem.PulsingDot
import com.hackathon.drivescore.core.designsystem.Space
import com.hackathon.drivescore.core.designsystem.VSpace
import com.hackathon.drivescore.core.engine.EngineGateway
import com.hackathon.drivescore.core.model.AppState
import com.hackathon.drivescore.core.model.ConnectionStatus
import com.hackathon.drivescore.core.model.DrivingState
import com.hackathon.drivescore.service.SessionManager
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import javax.inject.Inject

data class DriveUi(val name: String = "", val engine: ConnectionStatus = ConnectionStatus.DISCONNECTED, val ending: Boolean = false)

@HiltViewModel
class DriveViewModel @Inject constructor(
    private val session: SessionManager,
    gateway: EngineGateway,
) : ViewModel() {
    val ui: StateFlow<DriveUi> = combine(session.state, gateway.status) { st, engine ->
        when (st) {
            is AppState.SessionActive -> DriveUi(st.session.displayName, engine, false)
            is AppState.Ending -> DriveUi(st.session.displayName, engine, true)
            else -> DriveUi(engine = engine)
        }
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), DriveUi())

    fun endDrive() = session.endDrive()
}

/** Drive screen (spec §6.3): "Tracking" + driver name over a calm backdrop. No timer, no live score. End drive only while parked. */
@Composable
fun DriveScreen(vm: DriveViewModel = hiltViewModel()) {
    val ui by vm.ui.collectAsStateWithLifecycle()
    val parked = LocalDrivingState.current == DrivingState.PARKED
    Box(Modifier.fillMaxSize()) {
        DriveBackdrop()
        if (ui.engine != ConnectionStatus.CONNECTED) {
            Chip(
                stringResource(R.string.reconnecting), CarColors.Amber.copy(alpha = 0.18f), CarColors.Amber,
                Modifier.align(Alignment.TopCenter).padding(top = CarDimens.ScreenPadding),
            )
        }
        Column(
            Modifier.fillMaxSize().padding(horizontal = CarDimens.ScreenPadding),
            horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center,
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.S)) {
                PulsingDot(CarColors.Orange, 18.dp)
                Text(stringResource(R.string.tracking).uppercase(), style = MaterialTheme.typography.labelMedium, color = CarColors.Orange)
            }
            VSpace(Space.M)
            Text(
                ui.name, style = MaterialTheme.typography.displayMedium, color = CarColors.OnDark,
                maxLines = 1, overflow = TextOverflow.Ellipsis, textAlign = TextAlign.Center,
            )
            VSpace(Space.XXL)
            // Fixed-height slot so the title doesn't jump between parked / moving / ending.
            Box(Modifier.height(CarDimens.ButtonHeight), contentAlignment = Alignment.Center) {
                when {
                    ui.ending -> Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.M)) {
                        CircularProgressIndicator(Modifier.size(32.dp), color = CarColors.TealSoft, strokeWidth = 3.dp)
                        Text(stringResource(R.string.waiting_for_result), style = MaterialTheme.typography.titleMedium, color = CarColors.TealSoft)
                    }
                    parked -> PrimaryButton(stringResource(R.string.end_drive), vm::endDrive, Modifier.width(360.dp))
                    else -> Text(stringResource(R.string.drive_safely), style = MaterialTheme.typography.titleMedium, color = CarColors.Muted)
                }
            }
        }
    }
}
