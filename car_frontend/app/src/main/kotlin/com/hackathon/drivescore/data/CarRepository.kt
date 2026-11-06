package com.hackathon.drivescore.data

import android.content.Context
import android.provider.Settings
import com.hackathon.drivescore.BuildConfig
import com.hackathon.drivescore.core.datastore.AppPreferences
import com.hackathon.drivescore.core.datastore.SecureStore
import com.hackathon.drivescore.core.network.CarRegisterRequest
import com.hackathon.drivescore.core.network.DriveScoreApi
import com.hackathon.drivescore.core.network.apiCall
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** Car identity: car_id (DataStore) + car_token (SecureStore) (spec §10.1). */
class CarRepository(
    private val context: Context,
    private val api: DriveScoreApi,
    private val prefs: AppPreferences,
    private val secure: SecureStore,
) {
    private val mutex = Mutex()

    @Volatile private var cachedToken: String? = null

    fun token(): String? = cachedToken ?: secure.getString(SecureStore.KEY_CAR_TOKEN)?.also { cachedToken = it }

    fun isRegistered(): Boolean = token() != null && prefs.carId.value != null

    suspend fun ensureRegistered(): Result<String> = mutex.withLock {
        val existing = prefs.currentCarId()
        if (existing != null && token() != null) return@withLock Result.success(existing)
        val name = Settings.Global.getString(context.contentResolver, Settings.Global.DEVICE_NAME)
            ?.takeIf { it.isNotBlank() } ?: "My Rivian"
        apiCall { api.registerCar(BuildConfig.CAR_PROVISIONING_KEY, CarRegisterRequest(name = name.take(60))) }
            .map { r ->
                secure.putString(SecureStore.KEY_CAR_TOKEN, r.carToken)
                cachedToken = r.carToken
                prefs.setCar(r.carId, r.name)
                r.carId
            }
    }

    /** Debug: forget the identity and register as a new car (drivers must sign in again). */
    suspend fun reRegister(): Result<String> {
        mutex.withLock {
            secure.putString(SecureStore.KEY_CAR_TOKEN, null)
            cachedToken = null
            prefs.setCar(null, null)
        }
        return ensureRegistered()
    }
}
