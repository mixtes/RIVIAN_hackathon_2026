package com.hackathon.drivescore.feature.debug

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.hackathon.drivescore.core.common.Clock
import com.hackathon.drivescore.core.database.AppDatabase
import com.hackathon.drivescore.core.datastore.AppPreferences
import com.hackathon.drivescore.core.datastore.SecureStore
import com.hackathon.drivescore.core.designsystem.CarColors
import com.hackathon.drivescore.core.designsystem.PrimaryButton
import com.hackathon.drivescore.core.designsystem.SecondaryButton
import com.hackathon.drivescore.core.designsystem.VSpace
import com.hackathon.drivescore.core.engine.EngineClient
import com.hackathon.drivescore.core.engine.EngineGateway
import com.hackathon.drivescore.core.engine.SessionStartRequest
import com.hackathon.drivescore.core.engine.SwitchingEngineGateway
import com.hackathon.drivescore.core.engine.TrafficLine
import com.hackathon.drivescore.core.model.AlertEvent
import com.hackathon.drivescore.core.model.AlertLevel
import com.hackathon.drivescore.core.model.AppState
import com.hackathon.drivescore.core.model.DrivingState
import com.hackathon.drivescore.core.model.EngineAlert
import com.hackathon.drivescore.data.CarRepository
import com.hackathon.drivescore.data.SyncScheduler
import com.hackathon.drivescore.service.SessionManager
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.UUID
import javax.inject.Inject

@HiltViewModel
class DebugViewModel @Inject constructor(
    private val prefs: AppPreferences,
    private val gateway: SwitchingEngineGateway,
    private val engine: EngineGateway,
    client: EngineClient,
    private val session: SessionManager,
    private val sync: SyncScheduler,
    private val cars: CarRepository,
    private val db: AppDatabase,
    private val secure: SecureStore,
    private val clock: Clock,
) : ViewModel() {
    val engineUrl = prefs.engineUrl
    val apiUrl = prefs.apiBaseUrl
    val useFake = prefs.useFakeEngine
    val override = prefs.drivingStateOverride
    val vehicleSignals: StateFlow<Boolean> = prefs.vehicleSignalsEnabled.stateIn(viewModelScope, SharingStarted.Eagerly, false)
    val traffic: StateFlow<List<TrafficLine>> = client.traffic
    val info = engine.info
    val message = MutableStateFlow<String?>(null)

    fun saveUrls(engineUrl: String, apiUrl: String) = viewModelScope.launch {
        prefs.setEngineUrl(engineUrl); prefs.setApiBaseUrl(apiUrl); message.value = "Saved"
    }

    fun setFake(v: Boolean) = viewModelScope.launch { prefs.setUseFakeEngine(v) }
    fun setOverride(v: DrivingState?) = viewModelScope.launch { prefs.setDrivingStateOverride(v) }
    fun setVehicleSignals(v: Boolean) = viewModelScope.launch { prefs.setVehicleSignals(v) }
    fun drop() = engine.dropConnection()

    fun testAlert(level: AlertLevel) = viewModelScope.launch {
        val id = UUID.randomUUID().toString()
        val type = if (level == AlertLevel.CRITICAL || level == AlertLevel.HAPTIC) "EYES_OFF_ROAD" else "PHONE_CALL"
        gateway.debugAlerts.emit(AlertEvent.Show(EngineAlert(id, null, type, level, "Test ${level.name} alert", 1.0, null)))
        delay(5_000)
        gateway.debugAlerts.emit(AlertEvent.Clear(id))
    }

    /** Simulates a drive.result for the active session (or a fresh one for the unlocked driver). */
    fun simulateResult() = viewModelScope.launch {
        val st = session.state.value
        val req = when (st) {
            is AppState.SessionActive -> SessionStartRequest(st.session.sessionId, st.session.userId, st.session.displayName, null, st.session.startedAt)
            is AppState.Ending -> SessionStartRequest(st.session.sessionId, st.session.userId, st.session.displayName, null, st.session.startedAt)
            else -> {
                val user = st.unlockedUserId ?: run { message.value = "Unlock a driver first"; return@launch }
                SessionStartRequest(UUID.randomUUID().toString(), user, "", null, clock.nowMs() - 20 * 60_000)
            }
        }
        gateway.debugResults.emit(gateway.fake.buildResult(req, clock.nowMs()))
        message.value = "drive.result simulated"
    }

    fun forceSync() { sync.syncNow(); message.value = "Sync enqueued" }

    fun reRegister() = viewModelScope.launch {
        message.value = cars.reRegister().fold({ "Registered as $it" }, { "Failed: ${it.message}" })
    }

    fun wipe() = viewModelScope.launch {
        session.lock()
        withContext(Dispatchers.IO) { db.clearAllTables() }
        prefs.clearAll()
        secure.clear()
        message.value = "Local data wiped. Restart the app to re-register."
    }
}

/** Debug settings (spec §15.1). */
@Composable
fun DebugScreen(onBack: () -> Unit, vm: DebugViewModel = hiltViewModel()) {
    val engineUrl by vm.engineUrl.collectAsStateWithLifecycle()
    val apiUrl by vm.apiUrl.collectAsStateWithLifecycle()
    val fake by vm.useFake.collectAsStateWithLifecycle()
    val override by vm.override.collectAsStateWithLifecycle()
    val signals by vm.vehicleSignals.collectAsStateWithLifecycle()
    val traffic by vm.traffic.collectAsStateWithLifecycle()
    val info by vm.info.collectAsStateWithLifecycle()
    val message by vm.message.collectAsStateWithLifecycle()
    var eUrl by remember(engineUrl) { mutableStateOf(engineUrl) }
    var aUrl by remember(apiUrl) { mutableStateOf(apiUrl) }
    val fmt = remember { SimpleDateFormat("HH:mm:ss", Locale.US) }

    Row(Modifier.fillMaxSize().padding(24.dp), horizontalArrangement = Arrangement.spacedBy(24.dp)) {
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState())) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                SecondaryButton("Back", onBack)
                Text("  Debug settings", style = MaterialTheme.typography.headlineMedium)
            }
            VSpace(12.dp)
            OutlinedTextField(eUrl, { eUrl = it }, label = { Text("Engine URL") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(aUrl, { aUrl = it }, label = { Text("API base URL") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            VSpace(8.dp)
            PrimaryButton("Save URLs", { vm.saveUrls(eUrl, aUrl) })
            VSpace(12.dp)
            Row(verticalAlignment = Alignment.CenterVertically) { Switch(fake, vm::setFake); Text("  FakeEngine (in-app)") }
            Row(verticalAlignment = Alignment.CenterVertically) { Switch(signals, vm::setVehicleSignals); Text("  Vehicle signals (ignition)") }
            VSpace(8.dp)
            Text("Driving state override")
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(override == null, { vm.setOverride(null) }, label = { Text("Auto") })
                FilterChip(override == DrivingState.PARKED, { vm.setOverride(DrivingState.PARKED) }, label = { Text("Parked") })
                FilterChip(override == DrivingState.MOVING, { vm.setOverride(DrivingState.MOVING) }, label = { Text("Moving") })
            }
            VSpace(12.dp)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                AlertLevel.entries.forEach { l -> SecondaryButton("Alert ${l.name}", { vm.testAlert(l) }) }
                SecondaryButton("Simulate drive.result", { vm.simulateResult() })
                SecondaryButton("Force sync", vm::forceSync)
                SecondaryButton("Drop engine connection", vm::drop)
                SecondaryButton("Re-register car", { vm.reRegister() })
                PrimaryButton("Wipe local data", { vm.wipe() }, color = CarColors.Red)
            }
            message?.let { VSpace(8.dp); Text(it, color = CarColors.Amber) }
            info?.let { VSpace(8.dp); Text("Engine: ${it.engineVersion} · session ${it.activeSessionId ?: "none"} · ${it.supportedActions.joinToString()}", color = CarColors.Muted) }
        }
        Column(Modifier.weight(1f)) {
            Text("Engine messages (last ${traffic.size})", style = MaterialTheme.typography.titleMedium)
            LazyColumn(Modifier.fillMaxWidth().weight(1f)) {
                items(traffic.reversed()) { l ->
                    Text(
                        "${fmt.format(Date(l.atMs))} ${if (l.outgoing) "→" else "←"} ${l.text}",
                        fontFamily = FontFamily.Monospace, fontSize = 14.sp,
                        color = if (l.outgoing) CarColors.Accent else CarColors.OnDark,
                    )
                }
            }
        }
    }
}
