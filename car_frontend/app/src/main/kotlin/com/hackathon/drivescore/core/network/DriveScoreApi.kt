package com.hackathon.drivescore.core.network

import kotlinx.serialization.json.JsonObject
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.Header
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

/**
 * Backend REST API (backend spec §8). Car auth (`Authorization: Car …`) and `X-Request-Id` are added by
 * [CarAuthInterceptor]; driver-scoped calls pass `X-Driver-Id` explicitly per call.
 */
interface DriveScoreApi {
    @POST("api/v1/cars/register")
    suspend fun registerCar(
        @Header("X-Provisioning-Key") provisioningKey: String,
        @Body body: CarRegisterRequest,
    ): CarRegisterResponse

    @GET("api/v1/cars/me")
    suspend fun carMe(): CarDto

    @POST("api/v1/cars/me/drivers")
    suspend fun linkDriver(@Body body: LinkDriverRequest): LinkedDriverResponse

    @POST("api/v1/cars/me/drivers/register")
    suspend fun createAccount(@Body body: CreateAccountRequest): LinkedDriverResponse

    @DELETE("api/v1/cars/me/drivers/{userId}")
    suspend fun unlinkDriver(@Path("userId") userId: String): Response<Unit>

    @GET("api/v1/users/me")
    suspend fun me(@Header(DRIVER_HEADER) driverId: String): UserProfileDto

    @POST("api/v1/drives")
    suspend fun uploadDrive(@Header(DRIVER_HEADER) driverId: String, @Body body: JsonObject): Response<JsonObject>

    @GET("api/v1/drives")
    suspend fun drives(
        @Header(DRIVER_HEADER) driverId: String,
        @Query("limit") limit: Int = 20,
        @Query("cursor") cursor: String? = null,
    ): DrivePageDto

    @GET("api/v1/drives/{id}")
    suspend fun drive(@Header(DRIVER_HEADER) driverId: String, @Path("id") id: String): DriveDetailDto

    @GET("api/v1/stats/me")
    suspend fun stats(@Header(DRIVER_HEADER) driverId: String, @Query("period") period: String): StatsDto

    @GET("api/v1/leaderboards")
    suspend fun leaderboard(
        @Header(DRIVER_HEADER) driverId: String,
        @Query("period") period: String,
        @Query("region") region: String,
        @Query("limit") limit: Int = 50,
        @Query("offset") offset: Int = 0,
    ): LeaderboardDto

    @GET("api/v1/meta/mistake-types")
    suspend fun mistakeTypes(): List<MistakeTypeDto>

    @GET("api/v1/meta/regions")
    suspend fun regions(): List<RegionDto>

    companion object {
        const val DRIVER_HEADER = "X-Driver-Id"
    }
}
