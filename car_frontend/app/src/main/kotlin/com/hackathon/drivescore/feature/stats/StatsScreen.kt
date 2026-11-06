package com.hackathon.drivescore.feature.stats

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
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
import com.hackathon.drivescore.core.designsystem.Banner
import com.hackathon.drivescore.core.designsystem.CarColors
import com.hackathon.drivescore.core.designsystem.CarScreen
import com.hackathon.drivescore.core.designsystem.Space
import com.hackathon.drivescore.core.designsystem.KpiTile
import com.hackathon.drivescore.core.designsystem.MistakeBars
import com.hackathon.drivescore.core.designsystem.SectionCard
import com.hackathon.drivescore.core.designsystem.TrendChart
import com.hackathon.drivescore.core.designsystem.TrendPoint
import com.hackathon.drivescore.core.designsystem.VSpace
import com.hackathon.drivescore.core.model.Period
import com.hackathon.drivescore.core.network.ConnectivityMonitor
import com.hackathon.drivescore.core.network.RankDto
import com.hackathon.drivescore.core.network.StatsDto
import com.hackathon.drivescore.data.DriverRepository
import com.hackathon.drivescore.data.StatsRepository
import com.hackathon.drivescore.service.SessionManager
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

enum class StatsSource { CACHE, LOCAL_ONLY, NONE }

data class StatsUi(
    val period: Period = Period.WEEK,
    val stats: StatsDto? = null,
    val fetchedAt: Long? = null,
    val source: StatsSource = StatsSource.NONE,
    val online: Boolean = true,
    val regionCode: String = "",
)

@OptIn(ExperimentalCoroutinesApi::class)
@HiltViewModel
class StatsViewModel @Inject constructor(
    private val repo: StatsRepository,
    session: SessionManager,
    private val connectivity: ConnectivityMonitor,
    private val drivers: DriverRepository,
    val clock: Clock,
) : ViewModel() {
    private val userId = session.state.value.unlockedUserId
    private val period = MutableStateFlow(Period.WEEK)

    /** Cache first, then network (stale-while-revalidate); offline + no cache → local, this car only (spec §6.7). */
    val ui: StateFlow<StatsUi> = period.flatMapLatest { p ->
        flow {
            val id = userId ?: return@flow
            val region = drivers.get(id)?.regionCode ?: ""
            viewModelScope.launch { repo.refresh(id, p) }
            repo.observe(id, p).collect { cached ->
                if (cached != null) {
                    emit(StatsUi(p, cached.value, cached.fetchedAt, StatsSource.CACHE, connectivity.online.value, region))
                } else if (!connectivity.online.value) {
                    emit(StatsUi(p, repo.localStats(id, p), null, StatsSource.LOCAL_ONLY, false, region))
                } else {
                    emit(StatsUi(p, null, null, StatsSource.NONE, true, region))
                }
            }
        }
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), StatsUi())

    fun select(p: Period) { period.value = p }
}

@Composable
fun StatsScreen(vm: StatsViewModel = hiltViewModel()) {
    val ui by vm.ui.collectAsStateWithLifecycle()
    CarScreen(title = stringResource(R.string.nav_stats)) {
        PeriodTabs(ui.period, vm::select)
        VSpace(Space.L)
        when {
            ui.source == StatsSource.LOCAL_ONLY -> { Banner(stringResource(R.string.offline_this_car_only), accent = CarColors.Amber); VSpace(Space.L) }
            !ui.online && ui.fetchedAt != null -> { Banner(stringResource(R.string.last_updated, Formatters.minutesAgo(ui.fetchedAt!!, vm.clock.nowMs())), accent = CarColors.Amber); VSpace(Space.L) }
        }
        val s = ui.stats ?: run { Text(stringResource(R.string.loading), style = MaterialTheme.typography.bodyLarge, color = CarColors.Muted); return@CarScreen }
        Row(horizontalArrangement = Arrangement.spacedBy(Space.L)) {
            KpiTile(stringResource(R.string.weighted_score), s.weightedScore?.let { Formatters.score(it) } ?: "–", Modifier.weight(1f),
                valueColor = s.weightedScore?.let { CarColors.score(it) } ?: CarColors.OnDark)
            KpiTile(stringResource(R.string.drives), s.driveCount.toString(), Modifier.weight(1f))
            KpiTile(stringResource(R.string.driving_time), Formatters.drivingTime(s.totalDurationS), Modifier.weight(1f))
            KpiTile(stringResource(R.string.best_score), s.bestScore?.let { Formatters.score(it) } ?: "–", Modifier.weight(1f))
        }
        s.rank?.let { r ->
            VSpace(Space.L)
            Row(horizontalArrangement = Arrangement.spacedBy(Space.L)) {
                val regionCode = r.region?.code ?: ui.regionCode
                RankTile(r.region, stringResource(R.string.my_region, regionCode), "${Formatters.flagEmoji(regionCode)} $regionCode", Modifier.weight(1f))
                RankTile(r.global, stringResource(R.string.global), stringResource(R.string.global), Modifier.weight(1f))
            }
        }
        if (s.series.isNotEmpty()) {
            VSpace(Space.L)
            SectionCard(Modifier.fillMaxWidth(), title = stringResource(R.string.trend)) {
                TrendChart(s.series.map { TrendPoint(it.weightedScore, it.driveCount) })
            }
        }
        if (s.mistakes.isNotEmpty()) {
            VSpace(Space.L)
            SectionCard(Modifier.fillMaxWidth(), title = stringResource(R.string.mistake_breakdown)) {
                MistakeBars(s.mistakes.sortedByDescending { it.count }.map { Triple(it.type, it.count, it.perHour) })
            }
        }
    }
}

@Composable
private fun RankTile(r: RankDto?, title: String, scope: String, modifier: Modifier) {
    val text = when {
        r == null -> "–"
        r.qualified && r.rank != null -> stringResource(R.string.rank_of, r.rank, "%,d".format(r.totalRanked), scope)
        r.minutesNeeded != null -> stringResource(R.string.drive_more_to_qualify, r.minutesNeeded)
        else -> stringResource(R.string.not_ranked_yet_scope, scope)
    }
    SectionCard(modifier, title = title) { Text(text, style = MaterialTheme.typography.titleLarge) }
}
