package com.hackathon.drivescore.core.network

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** Backend DTOs (backend spec §8). snake_case on the wire. */

@Serializable
data class UserProfileDto(
    val id: String,
    val username: String,
    @SerialName("display_name") val displayName: String,
    @SerialName("region_code") val regionCode: String,
    @SerialName("avatar_color") val avatarColor: String = "#4F7DF3",
    @SerialName("leaderboard_visible") val leaderboardVisible: Boolean = true,
    @SerialName("created_at") val createdAt: String? = null,
)

@Serializable
data class CarRegisterRequest(val name: String, val model: String? = null, val vin: String? = null)

@Serializable
data class CarRegisterResponse(
    @SerialName("car_id") val carId: String,
    @SerialName("car_token") val carToken: String,
    val name: String,
)

@Serializable
data class LinkDriverRequest(val username: String, val password: String)

@Serializable
data class CreateAccountRequest(
    val username: String,
    val password: String,
    @SerialName("display_name") val displayName: String,
    @SerialName("region_code") val regionCode: String,
    val email: String? = null,
    @SerialName("avatar_color") val avatarColor: String,
)

@Serializable
data class LinkedDriverResponse(
    val user: UserProfileDto,
    @SerialName("linked_at") val linkedAt: String? = null,
)

@Serializable
data class CarDto(
    @SerialName("car_id") val carId: String,
    val name: String,
    val model: String? = null,
    val vin: String? = null,
)

@Serializable
data class CarRefDto(val id: String, val name: String? = null)

@Serializable
data class MistakesSummaryDto(
    @SerialName("total_count") val totalCount: Int = 0,
    val types: List<String> = emptyList(),
)

@Serializable
data class DriveListItemDto(
    val id: String,
    val car: CarRefDto? = null,
    @SerialName("started_at") val startedAt: String,
    @SerialName("ended_at") val endedAt: String,
    @SerialName("duration_s") val durationS: Int,
    val score: Double,
    @SerialName("mistakes_summary") val mistakesSummary: MistakesSummaryDto? = null,
)

@Serializable
data class DrivePageDto(
    val items: List<DriveListItemDto> = emptyList(),
    @SerialName("next_cursor") val nextCursor: String? = null,
)

@Serializable
data class DriveMistakeDto(
    val type: String,
    val count: Int,
    @SerialName("total_duration_s") val totalDurationS: Double? = null,
    @SerialName("max_duration_s") val maxDurationS: Double? = null,
)

@Serializable
data class DriveEventDto(
    val type: String,
    @SerialName("started_at") val startedAt: String,
    @SerialName("duration_s") val durationS: Double? = null,
)

@Serializable
data class DriveDetailDto(
    val id: String,
    val car: CarRefDto? = null,
    @SerialName("started_at") val startedAt: String,
    @SerialName("ended_at") val endedAt: String,
    @SerialName("duration_s") val durationS: Int,
    val score: Double,
    @SerialName("engine_version") val engineVersion: String? = null,
    val mistakes: List<DriveMistakeDto> = emptyList(),
    val events: List<DriveEventDto> = emptyList(),
)

@Serializable
data class StatsMistakeDto(
    val type: String,
    val count: Int,
    @SerialName("total_duration_s") val totalDurationS: Double? = null,
    @SerialName("per_hour") val perHour: Double? = null,
)

@Serializable
data class SeriesPointDto(
    @SerialName("bucket_start") val bucketStart: String,
    @SerialName("drive_count") val driveCount: Int = 0,
    @SerialName("duration_s") val durationS: Long = 0,
    @SerialName("weighted_score") val weightedScore: Double? = null,
    @SerialName("mistake_count") val mistakeCount: Int = 0,
)

@Serializable
data class RankDto(
    val code: String? = null,
    val rank: Int? = null,
    @SerialName("total_ranked") val totalRanked: Int = 0,
    val qualified: Boolean = false,
    @SerialName("minutes_needed") val minutesNeeded: Int? = null,
)

@Serializable
data class StatsRankDto(val region: RankDto? = null, val global: RankDto? = null)

@Serializable
data class StatsDto(
    val period: String,
    @SerialName("window_start") val windowStart: String? = null,
    @SerialName("window_end") val windowEnd: String? = null,
    @SerialName("drive_count") val driveCount: Int = 0,
    @SerialName("total_duration_s") val totalDurationS: Long = 0,
    @SerialName("avg_score") val avgScore: Double? = null,
    @SerialName("weighted_score") val weightedScore: Double? = null,
    @SerialName("best_score") val bestScore: Double? = null,
    @SerialName("worst_score") val worstScore: Double? = null,
    val mistakes: List<StatsMistakeDto> = emptyList(),
    val series: List<SeriesPointDto> = emptyList(),
    val rank: StatsRankDto? = null,
)

@Serializable
data class LeaderboardEntryDto(
    val rank: Int,
    @SerialName("user_id") val userId: String,
    val username: String,
    @SerialName("display_name") val displayName: String,
    @SerialName("avatar_color") val avatarColor: String = "#4F7DF3",
    @SerialName("weighted_score") val weightedScore: Double,
    @SerialName("drive_count") val driveCount: Int = 0,
    @SerialName("driving_time_s") val drivingTimeS: Long = 0,
)

@Serializable
data class LeaderboardMeDto(
    val rank: Int? = null,
    @SerialName("weighted_score") val weightedScore: Double? = null,
    @SerialName("drive_count") val driveCount: Int = 0,
    @SerialName("driving_time_s") val drivingTimeS: Long = 0,
    val qualified: Boolean = false,
    @SerialName("minutes_needed") val minutesNeeded: Int? = null,
)

@Serializable
data class LeaderboardDto(
    val period: String,
    val region: String,
    @SerialName("min_minutes") val minMinutes: Int? = null,
    @SerialName("total_ranked") val totalRanked: Int = 0,
    val entries: List<LeaderboardEntryDto> = emptyList(),
    val me: LeaderboardMeDto? = null,
    @SerialName("generated_at") val generatedAt: String? = null,
)

@Serializable
data class MistakeTypeDto(val type: String, val label: String, @SerialName("has_duration") val hasDuration: Boolean = true)

@Serializable
data class RegionDto(val code: String, val name: String)

@Serializable
data class ErrorDetailDto(val field: String? = null, val issue: String? = null)

@Serializable
data class ErrorBodyDto(
    val code: String,
    val message: String? = null,
    val details: List<ErrorDetailDto> = emptyList(),
    @SerialName("request_id") val requestId: String? = null,
)

@Serializable
data class ErrorEnvelopeDto(val error: ErrorBodyDto)
