package com.hackathon.drivescore.feature.result

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.hackathon.drivescore.R
import com.hackathon.drivescore.app.LocalDrivingState
import com.hackathon.drivescore.core.common.Formatters
import com.hackathon.drivescore.core.designsystem.CarColors
import com.hackathon.drivescore.core.designsystem.Chip
import com.hackathon.drivescore.core.designsystem.DriveTimeline
import com.hackathon.drivescore.core.designsystem.EmptyState
import com.hackathon.drivescore.core.designsystem.PrimaryButton
import com.hackathon.drivescore.core.designsystem.ScoreGauge
import com.hackathon.drivescore.core.designsystem.SecondaryButton
import com.hackathon.drivescore.core.designsystem.VSpace
import com.hackathon.drivescore.core.model.AppState
import com.hackathon.drivescore.core.model.Drive
import com.hackathon.drivescore.core.model.DrivingState
import com.hackathon.drivescore.core.model.MistakeTypes
import com.hackathon.drivescore.core.model.SyncState
import com.hackathon.drivescore.core.network.ConnectivityMonitor
import com.hackathon.drivescore.data.DriveRepository
import com.hackathon.drivescore.data.MetaRepository
import com.hackathon.drivescore.data.SyncScheduler
import com.hackathon.drivescore.service.SessionManager
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

@OptIn(ExperimentalCoroutinesApi::class)
@HiltViewModel
class ResultViewModel @Inject constructor(
    private val session: SessionManager,
    private val drives: DriveRepository,
    private val sync: SyncScheduler,
    connectivity: ConnectivityMonitor,
) : ViewModel() {
    private val driveId = MutableStateFlow<String?>(null)
    val drive: StateFlow<Drive?> = driveId.flatMapLatest { id -> if (id == null) flowOf(null) else drives.observeDrive(id) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), null)
    val online: StateFlow<Boolean> = connectivity.online

    fun bind(id: String?) { driveId.value = id }
    fun startNewDrive() = viewModelScope.launch { session.startNewDrive() }
    fun lock() = session.lock()
    fun retryUpload() {
        val id = driveId.value ?: return
        viewModelScope.launch { drives.markForRetry(id); sync.syncNow() }
    }
}

/** Drive Result (spec §6.4). Read-only while moving. */
@Composable
fun ResultScreen(state: AppState.Result, onDetails: (String) -> Unit, vm: ResultViewModel = hiltViewModel()) {
    LaunchedEffect(state.driveId) { vm.bind(state.driveId) }
    val drive by vm.drive.collectAsStateWithLifecycle()
    val online by vm.online.collectAsStateWithLifecycle()
    val parked = LocalDrivingState.current == DrivingState.PARKED
    Row(Modifier.fillMaxSize().padding(32.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally) {
            val d = drive
            when {
                d != null -> {
                    ScoreGauge(d.score)
                    VSpace(12.dp)
                    Text(
                        "${Formatters.drivingTime(d.durationS.toLong())} · ${Formatters.relativeDateTime(d.endedAt)}",
                        style = MaterialTheme.typography.titleMedium, color = CarColors.Muted,
                    )
                }
                state.resultInvalid -> EmptyState(stringResource(R.string.result_invalid))
                else -> EmptyState(stringResource(R.string.result_not_available), stringResource(R.string.result_not_available_body))
            }
        }
        Column(Modifier.weight(1f)) {
            val d = drive
            if (d != null) {
                MistakeChips(d)
                VSpace(24.dp)
                SyncStatus(d.syncState, online, onRetry = vm::retryUpload)
                VSpace(24.dp)
            }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(16.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                if (d != null && parked) SecondaryButton(stringResource(R.string.details), { onDetails(d.id) })
                PrimaryButton(stringResource(R.string.start_new_drive), { vm.startNewDrive() }, color = CarColors.Green)
                SecondaryButton(stringResource(R.string.lock_switch_driver), vm::lock)
            }
        }
    }
}

@Composable
private fun MistakeChips(d: Drive) {
    val list = d.mistakes.filter { it.count > 0 }.sortedByDescending { it.count }
    if (list.isEmpty()) {
        Text(stringResource(R.string.no_mistakes), style = MaterialTheme.typography.headlineMedium, color = CarColors.Green)
        return
    }
    FlowRow(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        list.forEach { m ->
            val info = MistakeTypes.info(m.type)
            Chip("${info.icon} ${info.label} × ${m.count}")
        }
    }
}

@Composable
fun SyncStatus(state: SyncState, online: Boolean, onRetry: () -> Unit) {
    when (state) {
        SyncState.SYNCED -> Text(stringResource(R.string.saved_to_cloud), color = CarColors.Green, style = MaterialTheme.typography.bodyLarge)
        SyncState.PENDING, SyncState.SYNCING -> Text(
            stringResource(if (online) R.string.uploading else R.string.will_upload_when_online),
            color = CarColors.Muted, style = MaterialTheme.typography.bodyLarge,
        )
        SyncState.FAILED_PERMANENT -> Text(
            stringResource(R.string.upload_failed_retry), color = CarColors.Red, style = MaterialTheme.typography.bodyLarge,
            modifier = Modifier.clickable(onClick = onRetry).padding(vertical = 16.dp),
        )
    }
}

@OptIn(ExperimentalCoroutinesApi::class)
@HiltViewModel
class DetailsViewModel @Inject constructor(
    private val drives: DriveRepository,
    private val session: SessionManager,
    meta: MetaRepository,
) : ViewModel() {
    private val id = MutableStateFlow<String?>(null)
    val drive: StateFlow<Drive?> = id.flatMapLatest { i -> if (i == null) flowOf(null) else drives.observeDrive(i) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), null)
    val labels: StateFlow<Map<String, String>> = meta.labels

    fun bind(driveId: String) {
        if (id.value == driveId) return
        id.value = driveId
        val user = session.state.value.unlockedUserId ?: return
        viewModelScope.launch { drives.ensureDetail(user, driveId) }
    }
}

/** Drive Details (spec §6.5): all 8 known types (zeros dimmed), unknown types appended, timeline. */
@Composable
fun DetailsScreen(driveId: String, onBack: () -> Unit, vm: DetailsViewModel = hiltViewModel()) {
    LaunchedEffect(driveId) { vm.bind(driveId) }
    val drive by vm.drive.collectAsStateWithLifecycle()
    val labels by vm.labels.collectAsStateWithLifecycle()
    val d = drive ?: run { EmptyState(stringResource(R.string.loading)); return }
    Column(Modifier.fillMaxSize().padding(32.dp).verticalScroll(rememberScrollState())) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            SecondaryButton(stringResource(R.string.back), onBack)
            Text(
                "  ${stringResource(R.string.score_label)} ${Formatters.score(d.score)} · ${Formatters.drivingTime(d.durationS.toLong())} · ${Formatters.relativeDateTime(d.endedAt)}",
                style = MaterialTheme.typography.titleLarge,
            )
        }
        VSpace(24.dp)
        TableRow(stringResource(R.string.col_mistake), stringResource(R.string.col_count), stringResource(R.string.col_total), stringResource(R.string.col_longest), header = true)
        HorizontalDivider()
        MistakeTypes.detailRows(d.mistakes).forEach { (info, m) ->
            val label = if (MistakeTypes.known.any { it.type == info.type }) info.label else labels[info.type] ?: info.label
            val count = m?.count ?: 0
            TableRow(
                "${info.icon} $label", count.toString(),
                if (count == 0 || !info.hasDuration) "–" else Formatters.shortDuration(m?.totalDurationS),
                if (count == 0 || !info.hasDuration) "–" else Formatters.shortDuration(m?.maxDurationS),
                dim = count == 0,
            )
        }
        if (d.events.isNotEmpty()) {
            VSpace(32.dp)
            Text(stringResource(R.string.timeline), style = MaterialTheme.typography.titleMedium)
            VSpace(12.dp)
            DriveTimeline(d.startedAt, d.endedAt, d.events)
        }
    }
}

@Composable
private fun TableRow(a: String, b: String, c: String, e: String, header: Boolean = false, dim: Boolean = false) {
    val style = if (header) MaterialTheme.typography.titleMedium else MaterialTheme.typography.bodyLarge
    Row(Modifier.fillMaxWidth().padding(vertical = 10.dp).alpha(if (dim) 0.4f else 1f)) {
        Text(a, style = style, modifier = Modifier.weight(2f))
        Text(b, style = style, modifier = Modifier.width(140.dp))
        Text(c, style = style, modifier = Modifier.width(180.dp))
        Text(e, style = style, modifier = Modifier.width(180.dp))
    }
}
