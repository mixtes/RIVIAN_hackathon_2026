package com.hackathon.drivescore.core.datastore

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.hackathon.drivescore.BuildConfig
import com.hackathon.drivescore.core.model.ActiveSession
import com.hackathon.drivescore.core.model.DrivingState
import com.hackathon.drivescore.service.SessionStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.runBlocking

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "app_prefs")

/** DataStore-backed preferences (spec §11.1): car identity, active session, debug URLs, feature flags. */
class AppPreferences(context: Context, scope: CoroutineScope) : SessionStore {
    private val ds = context.dataStore

    private object K {
        val carId = stringPreferencesKey("car_id")
        val carName = stringPreferencesKey("car_name")
        val carRevoked = booleanPreferencesKey("car_revoked")
        val sessionId = stringPreferencesKey("session_id")
        val sessionUser = stringPreferencesKey("session_user_id")
        val sessionName = stringPreferencesKey("session_display_name")
        val sessionStarted = longPreferencesKey("session_started_at")
        val sessionEnding = booleanPreferencesKey("session_ending")
        val engineUrl = stringPreferencesKey("engine_url")
        val apiBaseUrl = stringPreferencesKey("api_base_url")
        val useFakeEngine = booleanPreferencesKey("use_fake_engine")
        val drivingOverride = stringPreferencesKey("driving_state_override")
        val vehicleSignals = booleanPreferencesKey("feature_vehicle_signals")
    }

    // One blocking read at startup so URLs/toggles are correct before the first connection.
    private val snapshot: Preferences by lazy { runBlocking { ds.data.first() } }
    private fun <T> initial(f: (Preferences) -> T?): T? = f(snapshot)

    val carId: StateFlow<String?> = ds.data.map { it[K.carId] }.stateIn(scope, SharingStarted.Eagerly, null)
    val carName: StateFlow<String?> = ds.data.map { it[K.carName] }.stateIn(scope, SharingStarted.Eagerly, null)
    val carRevoked: StateFlow<Boolean> = ds.data.map { it[K.carRevoked] ?: false }.stateIn(scope, SharingStarted.Eagerly, false)
    val engineUrl: StateFlow<String> =
        ds.data.map { it[K.engineUrl] ?: BuildConfig.ENGINE_URL }.stateIn(scope, SharingStarted.Eagerly, initial { it[K.engineUrl] } ?: BuildConfig.ENGINE_URL)
    val apiBaseUrl: StateFlow<String> =
        ds.data.map { it[K.apiBaseUrl] ?: BuildConfig.API_BASE_URL }.stateIn(scope, SharingStarted.Eagerly, initial { it[K.apiBaseUrl] } ?: BuildConfig.API_BASE_URL)
    val useFakeEngine: StateFlow<Boolean> =
        ds.data.map { it[K.useFakeEngine] ?: false }.stateIn(scope, SharingStarted.Eagerly, initial { it[K.useFakeEngine] } ?: false)
    val drivingStateOverride: StateFlow<DrivingState?> = ds.data.map { p ->
        p[K.drivingOverride]?.let { runCatching { DrivingState.valueOf(it) }.getOrNull() }
    }.stateIn(scope, SharingStarted.Eagerly, null)
    val vehicleSignalsEnabled: Flow<Boolean> = ds.data.map { it[K.vehicleSignals] ?: false }

    suspend fun currentCarId(): String? = ds.data.first()[K.carId]

    suspend fun setCar(carId: String?, name: String?) = ds.edit {
        if (carId == null) it.remove(K.carId) else it[K.carId] = carId
        if (name == null) it.remove(K.carName) else it[K.carName] = name
        it[K.carRevoked] = false
    }

    suspend fun setCarRevoked(value: Boolean) = ds.edit { it[K.carRevoked] = value }
    suspend fun setEngineUrl(url: String?) = ds.edit { if (url.isNullOrBlank()) it.remove(K.engineUrl) else it[K.engineUrl] = url.trim() }
    suspend fun setApiBaseUrl(url: String?) = ds.edit { if (url.isNullOrBlank()) it.remove(K.apiBaseUrl) else it[K.apiBaseUrl] = url.trim() }
    suspend fun setUseFakeEngine(value: Boolean) = ds.edit { it[K.useFakeEngine] = value }
    suspend fun setDrivingStateOverride(value: DrivingState?) = ds.edit {
        if (value == null) it.remove(K.drivingOverride) else it[K.drivingOverride] = value.name
    }
    suspend fun setVehicleSignals(value: Boolean) = ds.edit { it[K.vehicleSignals] = value }

    override suspend fun load(): ActiveSession? {
        val p = ds.data.first()
        val id = p[K.sessionId] ?: return null
        val user = p[K.sessionUser] ?: return null
        return ActiveSession(id, user, p[K.sessionName] ?: "", p[K.sessionStarted] ?: 0L, p[K.sessionEnding] ?: false)
    }

    override suspend fun save(session: ActiveSession?) {
        ds.edit {
            if (session == null) {
                listOf(K.sessionId, K.sessionUser, K.sessionName).forEach { k -> it.remove(k) }
                it.remove(K.sessionStarted); it.remove(K.sessionEnding)
            } else {
                it[K.sessionId] = session.sessionId
                it[K.sessionUser] = session.userId
                it[K.sessionName] = session.displayName
                it[K.sessionStarted] = session.startedAt
                it[K.sessionEnding] = session.ending
            }
        }
    }

    suspend fun clearAll() = ds.edit { it.clear() }
}
