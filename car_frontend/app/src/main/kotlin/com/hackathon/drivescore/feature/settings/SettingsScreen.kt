package com.hackathon.drivescore.feature.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import androidx.compose.material3.Surface
import com.hackathon.drivescore.R
import com.hackathon.drivescore.app.LocalDrivingState
import com.hackathon.drivescore.core.common.Formatters
import com.hackathon.drivescore.core.designsystem.CarColors
import com.hackathon.drivescore.core.designsystem.EmptyState
import com.hackathon.drivescore.core.designsystem.Keypad
import com.hackathon.drivescore.core.designsystem.PinDots
import com.hackathon.drivescore.core.designsystem.PrimaryButton
import com.hackathon.drivescore.core.designsystem.SecondaryButton
import com.hackathon.drivescore.core.designsystem.SectionCard
import com.hackathon.drivescore.core.designsystem.VSpace
import com.hackathon.drivescore.core.model.Driver
import com.hackathon.drivescore.core.model.DrivingState
import com.hackathon.drivescore.data.DriveSyncer
import com.hackathon.drivescore.data.DriverRepository
import com.hackathon.drivescore.data.UnlockResult
import com.hackathon.drivescore.domain.PinHasher
import com.hackathon.drivescore.service.SessionManager
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

enum class ChangePinStage { CURRENT, NEW, CONFIRM }

data class ChangePinUi(val stage: ChangePinStage = ChangePinStage.CURRENT, val entered: String = "", val current: String = "", val newPin: String = "", val message: Int? = null)

data class SettingsUi(
    val driver: Driver? = null,
    val changePin: ChangePinUi? = null,
    val removeWarning: Int? = null,
    val removing: Boolean = false,
    val toast: Int? = null,
)

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val drivers: DriverRepository,
    private val session: SessionManager,
    private val syncer: DriveSyncer,
) : ViewModel() {
    private val userId = session.state.value.unlockedUserId
    private val _ui = MutableStateFlow(SettingsUi())
    val ui: StateFlow<SettingsUi> = _ui.asStateFlow()

    init { viewModelScope.launch { _ui.update { it.copy(driver = userId?.let { id -> drivers.get(id) }) } } }

    fun lockNow() = session.lock()

    fun openChangePin() = _ui.update { it.copy(changePin = ChangePinUi()) }
    fun closeChangePin() = _ui.update { it.copy(changePin = null) }

    fun pinDigit(c: Char) = _ui.update { s -> s.copy(changePin = s.changePin?.let { if (it.entered.length < 6) it.copy(entered = it.entered + c, message = null) else it }) }
    fun pinBackspace() = _ui.update { s -> s.copy(changePin = s.changePin?.let { it.copy(entered = it.entered.dropLast(1)) }) }

    fun pinNext() {
        val cp = _ui.value.changePin ?: return
        val id = userId ?: return
        when (cp.stage) {
            ChangePinStage.CURRENT -> _ui.update { it.copy(changePin = cp.copy(stage = ChangePinStage.NEW, current = cp.entered, entered = "")) }
            ChangePinStage.NEW -> if (!PinHasher.isValidPin(cp.entered)) {
                _ui.update { it.copy(changePin = cp.copy(message = R.string.err_pin_format)) }
            } else {
                _ui.update { it.copy(changePin = cp.copy(stage = ChangePinStage.CONFIRM, newPin = cp.entered, entered = "")) }
            }
            ChangePinStage.CONFIRM -> if (cp.entered != cp.newPin) {
                _ui.update { it.copy(changePin = cp.copy(stage = ChangePinStage.NEW, entered = "", newPin = "", message = R.string.err_pin_mismatch)) }
            } else viewModelScope.launch {
                when (drivers.changePin(id, cp.current, cp.newPin)) {
                    is UnlockResult.Success -> _ui.update { it.copy(changePin = null, toast = R.string.pin_changed) }
                    is UnlockResult.WrongPin -> _ui.update { it.copy(changePin = ChangePinUi(message = R.string.current_pin_wrong)) }
                    is UnlockResult.LockedOut, UnlockResult.PinDisabled -> {
                        _ui.update { it.copy(changePin = null) }
                        session.lock()
                    }
                    UnlockResult.NoProfile -> _ui.update { it.copy(changePin = null) }
                }
            }
        }
    }

    /** Remove me (spec §6.11): try to sync first; warn if drives would be lost. */
    fun requestRemove() {
        val id = userId ?: return
        _ui.update { it.copy(removing = true) }
        viewModelScope.launch {
            runCatching { syncer.run() }
            val unsynced = drivers.unsyncedCount(id)
            _ui.update { it.copy(removing = false, removeWarning = unsynced) }
        }
    }

    fun cancelRemove() = _ui.update { it.copy(removeWarning = null) }

    fun confirmRemove() {
        val id = userId ?: return
        _ui.update { it.copy(removing = true, removeWarning = null) }
        viewModelScope.launch {
            drivers.removeDriver(id)
            session.lock()
        }
    }
}

@Composable
fun SettingsScreen(onDebug: () -> Unit, vm: SettingsViewModel = hiltViewModel()) {
    val ui by vm.ui.collectAsStateWithLifecycle()
    if (LocalDrivingState.current == DrivingState.MOVING) { EmptyState(stringResource(R.string.available_when_parked)); return }
    Column(Modifier.fillMaxSize().padding(24.dp).verticalScroll(rememberScrollState())) {
        Text(stringResource(R.string.nav_settings), style = MaterialTheme.typography.headlineLarge)
        VSpace(16.dp)
        ui.driver?.let { d ->
            SectionCard(Modifier.fillMaxWidth()) {
                Column {
                    Text(stringResource(R.string.profile), style = MaterialTheme.typography.titleMedium)
                    Text("@${d.username}", style = MaterialTheme.typography.bodyLarge)
                    Text(d.displayName, style = MaterialTheme.typography.bodyLarge)
                    Text("${Formatters.flagEmoji(d.regionCode)} ${d.regionCode}", style = MaterialTheme.typography.bodyLarge)
                    Text(stringResource(R.string.edit_in_phone_app), color = CarColors.Muted, style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
        VSpace(24.dp)
        Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            PrimaryButton(stringResource(R.string.change_pin), vm::openChangePin)
            SecondaryButton(stringResource(R.string.lock_now), vm::lockNow)
            PrimaryButton(stringResource(if (ui.removing) R.string.removing else R.string.remove_me), vm::requestRemove, enabled = !ui.removing, color = CarColors.Red)
            TextButton(onClick = onDebug) { Text(stringResource(R.string.debug_settings)) }
        }
        ui.toast?.let { VSpace(16.dp); Text(stringResource(it), color = CarColors.Green, style = MaterialTheme.typography.bodyLarge) }
    }

    ui.removeWarning?.let { n ->
        AlertDialog(
            onDismissRequest = vm::cancelRemove,
            title = { Text(stringResource(R.string.remove_me)) },
            text = { Text(if (n > 0) stringResource(R.string.remove_warning_unsynced, n) else stringResource(R.string.remove_confirm)) },
            confirmButton = { TextButton(onClick = vm::confirmRemove) { Text(stringResource(if (n > 0) R.string.remove_anyway else R.string.remove)) } },
            dismissButton = { TextButton(onClick = vm::cancelRemove) { Text(stringResource(R.string.cancel)) } },
        )
    }

    ui.changePin?.let { cp ->
        Dialog(onDismissRequest = vm::closeChangePin, properties = DialogProperties(usePlatformDefaultWidth = false)) {
            Surface(color = CarColors.Surface, modifier = Modifier.padding(32.dp)) {
                Row(Modifier.padding(32.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(48.dp)) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            stringResource(
                                when (cp.stage) {
                                    ChangePinStage.CURRENT -> R.string.current_pin
                                    ChangePinStage.NEW -> R.string.new_pin
                                    ChangePinStage.CONFIRM -> R.string.confirm_pin_title
                                },
                            ),
                            style = MaterialTheme.typography.headlineMedium,
                        )
                        VSpace(16.dp)
                        PinDots(maxOf(4, cp.entered.length), cp.entered.length)
                        cp.message?.let { VSpace(12.dp); Text(stringResource(it), color = CarColors.Red) }
                        VSpace(16.dp)
                        Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                            SecondaryButton(stringResource(R.string.cancel), vm::closeChangePin)
                            PrimaryButton(stringResource(R.string.next), vm::pinNext, enabled = cp.entered.length >= 4)
                        }
                    }
                    Keypad(onDigit = vm::pinDigit, onBackspace = vm::pinBackspace)
                }
            }
        }
    }
}
