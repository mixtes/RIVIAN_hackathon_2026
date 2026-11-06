package com.hackathon.drivescore.feature.leaderboard

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
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
import com.hackathon.drivescore.core.common.Clock
import com.hackathon.drivescore.core.common.Formatters
import com.hackathon.drivescore.core.designsystem.Avatar
import com.hackathon.drivescore.core.designsystem.Banner
import com.hackathon.drivescore.core.designsystem.CarColors
import com.hackathon.drivescore.core.designsystem.EmptyState
import com.hackathon.drivescore.core.designsystem.VSpace
import com.hackathon.drivescore.core.model.Period
import com.hackathon.drivescore.core.network.ConnectivityMonitor
import com.hackathon.drivescore.core.network.LeaderboardEntryDto
import com.hackathon.drivescore.core.network.LeaderboardMeDto
import com.hackathon.drivescore.data.DriverRepository
import com.hackathon.drivescore.data.LeaderboardRepository
import com.hackathon.drivescore.feature.stats.PeriodTabs
import com.hackathon.drivescore.service.SessionManager
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import javax.inject.Inject

data class LeaderboardUi(
    val period: Period = Period.WEEK,
    val global: Boolean = false,
    val regionCode: String = "",
    val entries: List<LeaderboardEntryDto> = emptyList(),
    val me: LeaderboardMeDto? = null,
    val totalRanked: Int = 0,
    val fetchedAt: Long? = null,
    val online: Boolean = true,
    val refreshing: Boolean = false,
    val loaded: Boolean = false,
)

@HiltViewModel
class LeaderboardViewModel @Inject constructor(
    private val repo: LeaderboardRepository,
    session: SessionManager,
    private val drivers: DriverRepository,
    private val connectivity: ConnectivityMonitor,
    val clock: Clock,
) : ViewModel() {
    private val userId = session.state.value.unlockedUserId
    private val _ui = MutableStateFlow(LeaderboardUi())
    val ui: StateFlow<LeaderboardUi> = _ui.asStateFlow()
    private var observeJob: Job? = null
    private var loadingMore = false

    init {
        viewModelScope.launch {
            _ui.update { it.copy(regionCode = userId?.let { id -> drivers.get(id)?.regionCode } ?: "") }
            reload()
        }
    }

    private val region get() = if (_ui.value.global) "GLOBAL" else _ui.value.regionCode

    fun select(p: Period) { _ui.update { it.copy(period = p) }; reload() }
    fun setGlobal(global: Boolean) { _ui.update { it.copy(global = global) }; reload() }

    private fun reload() {
        val id = userId ?: return
        val p = _ui.value.period
        val r = region
        observeJob?.cancel()
        _ui.update { it.copy(entries = emptyList(), me = null, loaded = false, online = connectivity.online.value) }
        observeJob = viewModelScope.launch {
            launch { repo.refresh(id, p, r) }
            repo.observe(id, p, r).collect { c ->
                _ui.update {
                    if (c == null) it.copy(loaded = !connectivity.online.value)
                    else it.copy(entries = c.value.entries, me = c.value.me, totalRanked = c.value.totalRanked, fetchedAt = c.fetchedAt, loaded = true)
                }
            }
        }
    }

    fun refresh() {
        val id = userId ?: return
        _ui.update { it.copy(refreshing = true) }
        viewModelScope.launch {
            repo.refresh(id, _ui.value.period, region, force = true)
            _ui.update { it.copy(refreshing = false, online = connectivity.online.value) }
        }
    }

    /** Paged with limit=50&offset (spec §6.8). */
    fun loadMore() {
        val id = userId ?: return
        val s = _ui.value
        if (loadingMore || s.entries.size >= s.totalRanked || s.entries.isEmpty()) return
        loadingMore = true
        viewModelScope.launch {
            repo.page(id, s.period, region, s.entries.size).onSuccess { page ->
                _ui.update { it.copy(entries = (it.entries + page.entries).distinctBy { e -> e.userId }) }
            }
            loadingMore = false
        }
    }
}

@Composable
fun LeaderboardScreen(vm: LeaderboardViewModel = hiltViewModel()) {
    val ui by vm.ui.collectAsStateWithLifecycle()
    val list = rememberLazyListState()
    val nearEnd by remember { derivedStateOf { (list.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0) >= ui.entries.size - 5 } }
    LaunchedEffect(nearEnd) { if (nearEnd) vm.loadMore() }
    Column(Modifier.fillMaxSize().padding(24.dp)) {
        Text(stringResource(R.string.nav_leaderboard), style = MaterialTheme.typography.headlineLarge)
        VSpace(12.dp)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(24.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            PeriodTabs(ui.period, vm::select)
            FilterChip(!ui.global, { vm.setGlobal(false) }, label = { Text(stringResource(R.string.my_region, ui.regionCode), Modifier.padding(8.dp)) })
            FilterChip(ui.global, { vm.setGlobal(true) }, label = { Text(stringResource(R.string.global), Modifier.padding(8.dp)) })
        }
        VSpace(12.dp)
        if (!ui.online && ui.fetchedAt != null) Banner(stringResource(R.string.last_updated, Formatters.minutesAgo(ui.fetchedAt!!, vm.clock.nowMs())))
        when {
            ui.entries.isEmpty() && ui.loaded && !ui.online -> EmptyState(stringResource(R.string.leaderboard_needs_internet))
            ui.entries.isEmpty() && ui.loaded -> EmptyState(stringResource(R.string.leaderboard_empty))
            ui.entries.isEmpty() -> EmptyState(stringResource(R.string.loading))
            else -> PullToRefreshBox(isRefreshing = ui.refreshing, onRefresh = vm::refresh, modifier = Modifier.weight(1f)) {
                LazyColumn(state = list, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(ui.entries, key = { it.userId }) { e -> EntryRow(e) }
                }
            }
        }
        MyPosition(ui)
    }
}

@Composable
private fun EntryRow(e: LeaderboardEntryDto, highlight: Boolean = false) {
    Row(
        Modifier.fillMaxWidth().background(if (highlight) CarColors.SurfaceHigh else CarColors.Surface).padding(16.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(20.dp),
    ) {
        val medal = when (e.rank) { 1 -> "🥇"; 2 -> "🥈"; 3 -> "🥉"; else -> "#${e.rank}" }
        Text(medal, style = MaterialTheme.typography.titleLarge, modifier = Modifier.width(90.dp))
        Avatar(e.displayName.take(1).uppercase(), e.avatarColor, 56.dp)
        Text(e.displayName, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
        Text(Formatters.drivingTime(e.drivingTimeS), style = MaterialTheme.typography.bodyLarge, color = CarColors.Muted)
        Text(Formatters.score(e.weightedScore), style = MaterialTheme.typography.headlineMedium, color = CarColors.score(e.weightedScore))
    }
}

/** "My position" pinned at the bottom (spec §6.8). */
@Composable
private fun MyPosition(ui: LeaderboardUi) {
    val me = ui.me ?: return
    val visible = me.rank != null && ui.entries.any { it.rank == me.rank }
    if (visible) return
    VSpace(8.dp)
    Banner(
        if (me.qualified && me.rank != null) stringResource(R.string.my_position, me.rank, me.weightedScore?.let { Formatters.score(it) } ?: "–")
        else stringResource(R.string.not_ranked_drive_more, me.minutesNeeded ?: 0),
        color = CarColors.SurfaceHigh,
    )
}
