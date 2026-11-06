package com.hackathon.drivescore.app

import android.content.Context
import androidx.room.Room
import com.hackathon.drivescore.BuildConfig
import com.hackathon.drivescore.core.car.DrivingStateMonitor
import com.hackathon.drivescore.core.car.VehicleSignals
import com.hackathon.drivescore.core.common.AndroidLogger
import com.hackathon.drivescore.core.common.AppLogger
import com.hackathon.drivescore.core.common.Clock
import com.hackathon.drivescore.core.common.SystemClockImpl
import com.hackathon.drivescore.core.database.AppDatabase
import com.hackathon.drivescore.core.datastore.AppPreferences
import com.hackathon.drivescore.core.datastore.KeystoreAesGcmCipher
import com.hackathon.drivescore.core.datastore.SecureStore
import com.hackathon.drivescore.core.engine.EngineClient
import com.hackathon.drivescore.core.engine.EngineGateway
import com.hackathon.drivescore.core.engine.FakeEngineGateway
import com.hackathon.drivescore.core.engine.SwitchingEngineGateway
import com.hackathon.drivescore.core.engine.WebSocketEngineGateway
import com.hackathon.drivescore.core.model.AppState
import com.hackathon.drivescore.core.network.BaseUrlInterceptor
import com.hackathon.drivescore.core.network.CarAuthInterceptor
import com.hackathon.drivescore.core.network.ConnectivityMonitor
import com.hackathon.drivescore.core.network.DriveScoreApi
import com.hackathon.drivescore.data.ApiCache
import com.hackathon.drivescore.data.CarRepository
import com.hackathon.drivescore.data.DriveRepository
import com.hackathon.drivescore.data.DriveSyncer
import com.hackathon.drivescore.data.DriverRepository
import com.hackathon.drivescore.data.LeaderboardRepository
import com.hackathon.drivescore.data.MetaRepository
import com.hackathon.drivescore.data.RetrofitSyncRemote
import com.hackathon.drivescore.data.RoomSyncStore
import com.hackathon.drivescore.data.StatsRepository
import com.hackathon.drivescore.data.SyncScheduler
import com.hackathon.drivescore.domain.PinHasher
import com.hackathon.drivescore.service.AlertPresenter
import com.hackathon.drivescore.service.AndroidAlertOutput
import com.hackathon.drivescore.service.SessionManager
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.map
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import java.util.concurrent.TimeUnit
import javax.inject.Named
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object AppModule {
    @Provides @Singleton fun clock(): Clock = SystemClockImpl
    @Provides @Singleton fun logger(): AppLogger = AndroidLogger
    @Provides @Singleton fun appScope(): CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    @Provides @Singleton fun prefs(@ApplicationContext c: Context, scope: CoroutineScope) = AppPreferences(c, scope)
    @Provides @Singleton fun secureStore(@ApplicationContext c: Context) = SecureStore(c, KeystoreAesGcmCipher("drivescore_secure_store"))
    @Provides @Singleton @Named("pin") fun pinCipher(): com.hackathon.drivescore.core.datastore.BytesCipher =
        KeystoreAesGcmCipher("drivescore_pin_records")

    @Provides @Singleton fun db(@ApplicationContext c: Context): AppDatabase =
        Room.databaseBuilder(c, AppDatabase::class.java, "drivescore.db").build()

    @Provides @Singleton fun connectivity(@ApplicationContext c: Context) = ConnectivityMonitor(c)

    @Provides @Singleton @Named("api")
    fun apiOkHttp(prefs: AppPreferences, secure: SecureStore): OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .addInterceptor(BaseUrlInterceptor { prefs.apiBaseUrl.value })
        .addInterceptor(CarAuthInterceptor { secure.getString(SecureStore.KEY_CAR_TOKEN) })
        .apply {
            if (BuildConfig.DEBUG) {
                // BASIC never logs headers or bodies (no tokens/passwords in logs).
                addInterceptor(HttpLoggingInterceptor().setLevel(HttpLoggingInterceptor.Level.BASIC))
            }
        }
        .build()

    @Provides @Singleton fun api(@Named("api") client: OkHttpClient): DriveScoreApi {
        val json = Json { ignoreUnknownKeys = true; explicitNulls = false }
        return Retrofit.Builder()
            .baseUrl(BuildConfig.API_BASE_URL.trimEnd('/') + "/")
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json; charset=utf-8".toMediaType()))
            .build()
            .create(DriveScoreApi::class.java)
    }

    @Provides @Singleton @Named("ws")
    fun wsOkHttp(): OkHttpClient = OkHttpClient.Builder()
        .pingInterval(10, TimeUnit.SECONDS)
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    @Provides @Singleton fun engineClient(@Named("ws") client: OkHttpClient, scope: CoroutineScope, log: AppLogger) =
        EngineClient(client, scope, log)

    @Provides @Singleton fun fakeEngine(scope: CoroutineScope) = FakeEngineGateway(scope)

    @Provides @Singleton fun switchingGateway(
        client: EngineClient, fake: FakeEngineGateway, prefs: AppPreferences, scope: CoroutineScope, log: AppLogger,
    ): SwitchingEngineGateway {
        val real = WebSocketEngineGateway(client, scope, log, { prefs.engineUrl.value }, BuildConfig.VERSION_NAME)
        return SwitchingEngineGateway(real, fake, prefs.useFakeEngine, scope)
    }

    @Provides @Singleton fun gateway(g: SwitchingEngineGateway): EngineGateway = g

    @Provides @Singleton fun carRepo(@ApplicationContext c: Context, api: DriveScoreApi, prefs: AppPreferences, secure: SecureStore) =
        CarRepository(c, api, prefs, secure)

    @Provides @Singleton fun driveRepo(db: AppDatabase, api: DriveScoreApi, prefs: AppPreferences, clock: Clock, log: AppLogger) =
        DriveRepository(db, api, prefs, clock, log)

    @Provides @Singleton fun pinHasher() = PinHasher()

    @Provides @Singleton fun driverRepo(
        api: DriveScoreApi, db: AppDatabase, @Named("pin") cipher: com.hackathon.drivescore.core.datastore.BytesCipher,
        hasher: PinHasher, clock: Clock,
    ) = DriverRepository(api, db.driverProfiles(), db.drives(), db.apiCache(), cipher, hasher, clock)

    @Provides @Singleton fun apiCache(db: AppDatabase, clock: Clock) = ApiCache(db.apiCache(), clock)
    @Provides @Singleton fun statsRepo(api: DriveScoreApi, cache: ApiCache, drives: DriveRepository, clock: Clock) =
        StatsRepository(api, cache, drives, clock)
    @Provides @Singleton fun leaderboardRepo(api: DriveScoreApi, cache: ApiCache) = LeaderboardRepository(api, cache)
    @Provides @Singleton fun metaRepo(api: DriveScoreApi) = MetaRepository(api)

    @Provides @Singleton fun syncScheduler(@ApplicationContext c: Context) = SyncScheduler(c)
    @Provides fun driveSyncer(db: AppDatabase, prefs: AppPreferences, api: DriveScoreApi, log: AppLogger) =
        DriveSyncer(RoomSyncStore(db.drives(), db.driverProfiles(), db.apiCache(), prefs), RetrofitSyncRemote(api), log)

    @Provides @Singleton fun sessionManager(
        gateway: EngineGateway, prefs: AppPreferences, drives: DriveRepository, clock: Clock, scope: CoroutineScope, log: AppLogger,
    ) = SessionManager(gateway, prefs, drives, { prefs.currentCarId() }, clock, scope, log)

    @Provides @Singleton fun foreground() = AppForeground()

    @Provides @Singleton fun alertPresenter(
        @ApplicationContext c: Context, gateway: EngineGateway, clock: Clock, scope: CoroutineScope, log: AppLogger,
        fg: AppForeground, session: SessionManager,
    ) = AlertPresenter(
        gateway, AndroidAlertOutput(c, log), clock, scope, fg.inForeground,
        session.state.map { it is AppState.SessionActive || it is AppState.Ending },
    )

    @Provides @Singleton fun drivingState(@ApplicationContext c: Context, prefs: AppPreferences, scope: CoroutineScope, log: AppLogger) =
        DrivingStateMonitor(c, prefs.drivingStateOverride, scope, log)

    @Provides @Singleton fun vehicleSignals(@ApplicationContext c: Context, m: DrivingStateMonitor, log: AppLogger) =
        VehicleSignals(c, m, log)
}
