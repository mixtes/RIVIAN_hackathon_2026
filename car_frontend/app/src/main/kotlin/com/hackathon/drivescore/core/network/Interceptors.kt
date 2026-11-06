package com.hackathon.drivescore.core.network

import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.Interceptor
import okhttp3.Response
import java.util.UUID

/** Adds `Authorization: Car <token>` (when a token exists) and `X-Request-Id` (spec §12). */
class CarAuthInterceptor(private val tokenProvider: () -> String?) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val b = chain.request().newBuilder()
        if (chain.request().header("X-Request-Id") == null) b.header("X-Request-Id", UUID.randomUUID().toString())
        val isProvisioning = chain.request().header("X-Provisioning-Key") != null
        if (!isProvisioning && chain.request().header("Authorization") == null) {
            tokenProvider()?.let { b.header("Authorization", "Car $it") }
        }
        return chain.proceed(b.build())
    }
}

/** Rewrites scheme/host/port to the (debug-overridable) API base URL at request time. */
class BaseUrlInterceptor(private val baseUrlProvider: () -> String) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val base = baseUrlProvider().toHttpUrlOrNull() ?: return chain.proceed(chain.request())
        val req = chain.request()
        val basePath = base.encodedPath.trimEnd('/')
        val newUrl = req.url.newBuilder()
            .scheme(base.scheme).host(base.host).port(base.port)
            .encodedPath(basePath + req.url.encodedPath)
            .build()
        return chain.proceed(req.newBuilder().url(newUrl).build())
    }
}
