package com.hackathon.drivescore.feature.history

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.hackathon.drivescore.R
import com.hackathon.drivescore.core.common.Formatters
import com.hackathon.drivescore.core.designsystem.Banner
import com.hackathon.drivescore.core.designsystem.CarColors
import com.hackathon.drivescore.core.designsystem.CarScreen
import com.hackathon.drivescore.core.designsystem.Space
import com.hackathon.drivescore.core.designsystem.VSpace
import com.hackathon.drivescore.core.designsystem.EmptyState
import com.hackathon.drivescore.core.designsystem.ScoreBadge
import com.hackathon.drivescore.core.designsystem.SectionCard
import com.hackathon.drivescore.core.model.SyncState
import com.hackathon.drivescore.core.network.ConnectivityMonitor
import com.hackathon.drivescore.data.DriveRepository
import com.hackathon.drivescore.data.HistoryItem
import com.hackathon.drivescore.service.SessionManager
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class HistoryViewModel @Inject constructor(
    private val drives: DriveRepository,
    session: SessionManager,
    connectivity: ConnectivityMonitor,
) : ViewModel() {
    private val userId = session.state.value.unlockedUserId
    val items: StateFlow<List<HistoryItem>> = (userId?.let { drives.observeHistory(it) } ?: emptyFlow())
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())
    val online: StateFlow<Boolean> = connectivity.online
    private var nextCursor: String? = null
    private var endReached = false
    val loading = MutableStateFlow(false)

    init { loadMore(reset = true) }

    /** Infinite scroll via cursor (spec §6.6). */
    fun loadMore(reset: Boolean = false) {
        val id = userId ?: return
        if (loading.value || (!reset && endReached) || !online.value) return
        if (reset) { nextCursor = null; endReached = false }
        loading.value = true
        viewModelScope.launch {
            drives.fetchHistoryPage(id, nextCursor).onSuccess { c -> nextCursor = c; endReached = c == null }
            loading.value = false
        }
    }
}

/** History (spec §6.6): local + server drives, de-duplicated by id (Room primary key). Parked only. */
@Composable
fun HistoryScreen(onOpen: (String) -> Unit, vm: HistoryViewModel = hiltViewModel()) {
    val items by vm.items.collectAsStateWithLifecycle()
    val online by vm.online.collectAsStateWithLifecycle()
    val listState = rememberLazyListState()
    val nearEnd by remember { derivedStateOf { listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0 >= items.size - 3 } }
    LaunchedEffect(nearEnd, items.size) { if (nearEnd) vm.loadMore() }
    CarScreen(title = stringResource(R.string.nav_history), scrollable = false) {
        if (!online) {
            Banner(stringResource(R.string.offline_saved_data), accent = CarColors.Amber)
            VSpace(Space.L)
        }
        if (items.isEmpty()) {
            EmptyState(stringResource(R.string.no_drives_yet))
            return@CarScreen
        }
        LazyColumn(state = listState, verticalArrangement = Arrangement.spacedBy(Space.M)) {
            items(items, key = { it.id }) { item -> HistoryRow(item) { onOpen(item.id) } }
        }
    }
}

@Composable
private fun HistoryRow(item: HistoryItem, onClick: () -> Unit) {
    SectionCard(Modifier.fillMaxWidth(), onClick = onClick) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.L)) {
            Column(Modifier.weight(1f)) {
                Text(Formatters.relativeDateTime(item.endedAt), style = MaterialTheme.typography.titleMedium)
                val car = if (item.otherCar && item.carName != null) " · ${item.carName}" else ""
                Text(
                    "${Formatters.drivingTime(item.durationS.toLong())} · ${stringResource(R.string.mistakes_count, item.mistakeCount)}$car",
                    style = MaterialTheme.typography.bodyMedium, color = CarColors.Muted,
                )
            }
            when (item.syncState) {
                SyncState.PENDING, SyncState.SYNCING -> Text("⇡", style = MaterialTheme.typography.titleMedium, color = CarColors.Amber)
                SyncState.FAILED_PERMANENT -> Text("⚠", style = MaterialTheme.typography.titleMedium, color = CarColors.Red)
                SyncState.SYNCED -> Unit
            }
            ScoreBadge(item.score, Modifier.width(96.dp))
        }
    }
}
