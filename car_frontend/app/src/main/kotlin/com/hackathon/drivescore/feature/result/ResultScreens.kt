package com.hackathon.drivescore.feature.result

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.hackathon.drivescore.R
import com.hackathon.drivescore.app.LocalDrivingState
import com.hackathon.drivescore.core.common.Formatters
import com.hackathon.drivescore.core.designsystem.CarColors
import com.hackathon.drivescore.core.designsystem.CarDimens
import com.hackathon.drivescore.core.designsystem.CarScreen
import com.hackathon.drivescore.core.designsystem.CarShapes
import com.hackathon.drivescore.core.designsystem.ScoreBadge
import com.hackathon.drivescore.core.designsystem.SectionCard
import com.hackathon.drivescore.core.designsystem.Space
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
import com.hackathon.drivescore.core.model.MistakeSeverity
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
    val d = drive
    Row(Modifier.fillMaxSize().padding(CarDimens.ScreenPadding), horizontalArrangement = Arrangement.spacedBy(Space.L)) {
        SectionCard(Modifier.weight(1f).fillMaxHeight()) {
            Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
                when {
                    d != null -> {
                        ScoreGauge(d.score)
                        VSpace(Space.M)
                        Text(
                            "${Formatters.drivingTime(d.durationS.toLong())} · ${Formatters.relativeDateTime(d.endedAt)}",
                            style = MaterialTheme.typography.titleMedium, color = CarColors.Muted,
                        )
                    }
                    state.resultInvalid -> EmptyState(stringResource(R.string.result_invalid))
                    else -> EmptyState(stringResource(R.string.result_not_available), stringResource(R.string.result_not_available_body))
                }
            }
        }
        Column(Modifier.weight(1f).fillMaxHeight(), verticalArrangement = Arrangement.spacedBy(Space.L)) {
            if (d != null) {
                SectionCard(Modifier.fillMaxWidth().weight(1f), title = stringResource(R.string.mistake_breakdown)) {
                    MistakeChips(d)
                    Spacer(Modifier.weight(1f))
                    SyncStatus(d.syncState, online, onRetry = vm::retryUpload)
                }
            } else {
                Spacer(Modifier.weight(1f))
            }
            PrimaryButton(stringResource(R.string.start_new_drive), { vm.startNewDrive() }, Modifier.fillMaxWidth())
            Row(horizontalArrangement = Arrangement.spacedBy(Space.L)) {
                if (d != null && parked) SecondaryButton(stringResource(R.string.details), { onDetails(d.id) }, Modifier.weight(1f))
                SecondaryButton(stringResource(R.string.lock_switch_driver), vm::lock, Modifier.weight(1f))
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
    FlowRow(horizontalArrangement = Arrangement.spacedBy(Space.S), verticalArrangement = Arrangement.spacedBy(Space.S)) {
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
            modifier = Modifier.clickable(onClick = onRetry).padding(vertical = Space.M),
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
    val d = drive
    CarScreen(
        title = stringResource(R.string.details), onBack = onBack,
        trailing = { if (d != null) ScoreBadge(d.score) },
    ) {
        if (d == null) { EmptyState(stringResource(R.string.loading)); return@CarScreen }
        Text(
            "${Formatters.drivingTime(d.durationS.toLong())} · ${Formatters.relativeDateTime(d.endedAt)}",
            style = MaterialTheme.typography.titleMedium, color = CarColors.Muted,
        )
        VSpace(Space.L)
        SectionCard(Modifier.fillMaxWidth()) {
            TableHeader(stringResource(R.string.col_mistake), stringResource(R.string.col_count), stringResource(R.string.col_total), stringResource(R.string.col_longest))
            HorizontalDivider(color = CarColors.Outline)
            MistakeTypes.detailRows(d.mistakes).forEach { (info, m) ->
                val label = if (MistakeTypes.known.any { it.type == info.type }) info.label else labels[info.type] ?: info.label
                val count = m?.count ?: 0
                MistakeRow(
                    "${info.icon} $label", count, d.durationS,
                    if (count == 0 || !info.hasDuration) "–" else Formatters.shortDuration(m?.totalDurationS),
                    if (count == 0 || !info.hasDuration) "–" else Formatters.shortDuration(m?.maxDurationS),
                )
            }
        }
        if (d.events.isNotEmpty()) {
            VSpace(Space.L)
            SectionCard(Modifier.fillMaxWidth(), title = stringResource(R.string.timeline)) {
                DriveTimeline(d.startedAt, d.endedAt, d.events)
            }
        }
    }
}

private val CountCol = 200.dp
private val DurationCol = 150.dp

@Composable
private fun TableHeader(a: String, b: String, c: String, e: String) {
    val style = MaterialTheme.typography.labelSmall
    Row(Modifier.fillMaxWidth().padding(vertical = Space.S)) {
        Text(a, style = style, color = CarColors.Muted, modifier = Modifier.weight(1f))
        Text(b, style = style, color = CarColors.Muted, modifier = Modifier.width(CountCol))
        Text(c, style = style, color = CarColors.Muted, modifier = Modifier.width(DurationCol))
        Text(e, style = style, color = CarColors.Muted, modifier = Modifier.width(DurationCol))
    }
}

/** One mistake type: the count is a pill colored by its rate per hour (green none, amber < 2/h, red otherwise). */
@Composable
private fun MistakeRow(label: String, count: Int, driveDurationS: Int, total: String, longest: String) {
    val textColor = if (count == 0) CarColors.Muted else CarColors.OnDark
    val style = MaterialTheme.typography.bodyLarge
    Row(Modifier.fillMaxWidth().padding(vertical = Space.XS), verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = style, color = textColor, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
        Box(Modifier.width(CountCol)) { SeverityPill(count, driveDurationS) }
        Text(total, style = style, color = textColor, modifier = Modifier.width(DurationCol))
        Text(longest, style = style, color = textColor, modifier = Modifier.width(DurationCol))
    }
}

@Composable
private fun SeverityPill(count: Int, driveDurationS: Int) {
    val text = count.toString()
    Box(
        Modifier.heightIn(min = 44.dp).clip(CarShapes.Pill).background(CarColors.severity(MistakeSeverity.of(count, driveDurationS)))
            .padding(horizontal = Space.M),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold, color = CarColors.BackgroundDeep)
    }
}
