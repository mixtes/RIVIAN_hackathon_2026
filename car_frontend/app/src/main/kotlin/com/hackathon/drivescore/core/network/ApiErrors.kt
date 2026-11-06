package com.hackathon.drivescore.core.network

import kotlinx.serialization.json.Json
import retrofit2.HttpException
import java.io.IOException

/** A classified backend failure (backend spec §8.2). */
sealed class ApiFailure(message: String) : Exception(message) {
    class Http(val status: Int, val code: String?, message: String, val details: List<ErrorDetailDto>) :
        ApiFailure("$status $code: $message")
    class Network(cause: IOException) : ApiFailure(cause.message ?: "network error")
    class Unexpected(cause: Throwable) : ApiFailure(cause.message ?: cause.javaClass.simpleName)
}

object ApiErrors {
    private val json = Json { ignoreUnknownKeys = true }

    fun parseBody(body: String?): ErrorBodyDto? = try {
        body?.let { json.decodeFromString(ErrorEnvelopeDto.serializer(), it).error }
    } catch (_: Exception) {
        null
    }

    fun from(t: Throwable): ApiFailure = when (t) {
        is ApiFailure -> t
        is HttpException -> {
            val body = parseBody(t.response()?.errorBody()?.string())
            ApiFailure.Http(t.code(), body?.code, body?.message ?: t.message(), body?.details.orEmpty())
        }
        is IOException -> ApiFailure.Network(t)
        else -> ApiFailure.Unexpected(t)
    }
}

/** Runs an API call, mapping exceptions to [ApiFailure]. */
suspend fun <T> apiCall(block: suspend () -> T): Result<T> = try {
    Result.success(block())
} catch (e: kotlinx.coroutines.CancellationException) {
    throw e
} catch (t: Throwable) {
    Result.failure(ApiErrors.from(t))
}
