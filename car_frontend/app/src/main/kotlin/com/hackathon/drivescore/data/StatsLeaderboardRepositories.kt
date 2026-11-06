package com.hackathon.drivescore.data

import com.hackathon.drivescore.core.common.Clock
import com.hackathon.drivescore.core.common.IsoTime
import com.hackathon.drivescore.core.database.ApiCacheDao
import com.hackathon.drivescore.core.database.ApiCacheEntity
import com.hackathon.drivescore.core.database.DriveEntity
import com.hackathon.drivescore.core.database.DriveMistakeEntity
import com.hackathon.drivescore.core.model.Period
import com.hackathon.drivescore.core.network.DriveScoreApi
import com.hackathon.drivescore.core.network.LeaderboardDto
import com.hackathon.drivescore.core.network.StatsDto
import com.hackathon.drivescore.core.network.StatsMistakeDto
import com.hackathon.drivescore.core.network.apiCall
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.Json

data class Cached<T>(val value: T, val fetchedAt: Long)

/** Stale-while-revalidate cache over api_cache (spec §11.4): network refresh skipped if < 60 s old. */
class ApiCache(private val dao: ApiCacheDao, private val clock: Clock, private val freshMs: Long = 60_000) {
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }

    fun <T> observe(userId: String, key: String, s: KSerializer<T>): Flow<Cached<T>?> = dao.observe(userId, key).map { e ->
        e?.let { runCatching { Cached(json.decodeFromString(s, it.json), it.fetchedAt) }.getOrNull() }
    }

    suspend fun <T> get(userId: String, key: String, s: KSerializer<T>): Cached<T>? =
        dao.get(userId, key)?.let { e -> runCatching { Cached(json.decodeFromString(s, e.json), e.fetchedAt) }.getOrNull() }

    suspend fun <T> refresh(userId: String, key: String, s: KSerializer<T>, force: Boolean, fetch: suspend () -> T): Result<T?> {
        val existing = dao.get(userId, key)
        if (!force && existing != null && clock.nowMs() - existing.fetchedAt < freshMs) return Result.success(null)
        return apiCall { fetch() }.map { v ->
            dao.put(ApiCacheEntity(userId, key, json.encodeToString(s, v), clock.nowMs()))
            v
        }
    }
}

class StatsRepository(
    private val api: DriveScoreApi,
    private val cache: ApiCache,
    private val drives: DriveRepository,
    private val clock: Clock,
) {
    private fun key(p: Period) = "stats:${p.wire}"

    fun observe(userId: String, period: Period): Flow<Cached<StatsDto>?> = cache.observe(userId, key(period), StatsDto.serializer())

    suspend fun refresh(userId: String, period: Period, force: Boolean = false): Result<StatsDto?> =
        cache.refresh(userId, key(period), StatsDto.serializer(), force) { api.stats(userId, period.wire) }

    /** Offline with nothing cached: compute from this car's Room drives (spec §6.7). */
    suspend fun localStats(userId: String, period: Period): StatsDto {
        val now = clock.nowMs()
        val from = period.windowMs?.let { now - it } ?: 0L
        return LocalStats.compute(period, drives.localDrivesSince(userId, from), from, now)
    }
}

object LocalStats {
    fun compute(period: Period, rows: List<Pair<DriveEntity, List<DriveMistakeEntity>>>, fromMs: Long, nowMs: Long): StatsDto {
        val totalS = rows.sumOf { it.first.durationS.toLong() }
        val weighted = if (totalS > 0) rows.sumOf { it.first.score * it.first.durationS } / totalS else null
        val mistakes = rows.flatMap { it.second }.groupBy { it.type }.map { (type, list) ->
            val count = list.sumOf { it.count }
            val dur = list.mapNotNull { it.totalDurationS }.takeIf { it.isNotEmpty() }?.sum()
            StatsMistakeDto(type, count, dur, if (totalS > 0) count / (totalS / 3600.0) else null)
        }.sortedByDescending { it.count }
        return StatsDto(
            period = period.wire,
            windowStart = IsoTime.format(fromMs),
            windowEnd = IsoTime.format(nowMs),
            driveCount = rows.size,
            totalDurationS = totalS,
            avgScore = rows.map { it.first.score }.takeIf { it.isNotEmpty() }?.average(),
            weightedScore = weighted,
            bestScore = rows.maxOfOrNull { it.first.score },
            worstScore = rows.minOfOrNull { it.first.score },
            mistakes = mistakes,
        )
    }
}

class LeaderboardRepository(private val api: DriveScoreApi, private val cache: ApiCache) {
    private fun key(p: Period, region: String, offset: Int) = "lb:${p.wire}:$region:$offset"

    fun observe(userId: String, period: Period, region: String): Flow<Cached<LeaderboardDto>?> =
        cache.observe(userId, key(period, region, 0), LeaderboardDto.serializer())

    suspend fun refresh(userId: String, period: Period, region: String, force: Boolean = false): Result<LeaderboardDto?> =
        cache.refresh(userId, key(period, region, 0), LeaderboardDto.serializer(), force) {
            api.leaderboard(userId, period.wire, region, PAGE, 0)
        }

    /** Further pages (offset > 0) are network-only. */
    suspend fun page(userId: String, period: Period, region: String, offset: Int): Result<LeaderboardDto> =
        apiCall { api.leaderboard(userId, period.wire, region, PAGE, offset) }

    companion object { const val PAGE = 50 }
}
