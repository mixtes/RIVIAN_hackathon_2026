# car_frontend: DriveScore car app (Android Automotive OS)

This is the Kotlin / Jetpack Compose app that runs on the car's head unit. Drivers pick their profile and unlock it with a PIN, which works offline. The app then tracks the drive through the engine bridge, shows real-time alerts, and stores the end-of-drive score. It syncs drives to the backend and shows history, stats, and leaderboards.

- Spec: [`frontend_specification.md`](frontend_specification.md)
- Backend contract: [`../backend/backend_specification.md`](../backend/backend_specification.md) §7–§8
- Engine bridge (WebSocket `:8765`): [`../simulated_game_engine/`](../simulated_game_engine/)

## Prerequisites

| What | Version used | Notes |
|---|---|---|
| JDK | 17+ (tested with Android Studio's bundled JBR 25) | `export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"` |
| Android SDK | platform `android-37.0`, build-tools 36 | `local.properties` → `sdk.dir=…` (Android Studio writes it) |
| Gradle | 9.8.2 via the wrapper (`./gradlew`) | downloaded automatically into `~/.gradle` |
| AAOS emulator image (optional) | `system-images;android-34-ext9;android-automotive;arm64-v8a` | for running the app |

Toolchain: AGP 9.5.1 (built-in Kotlin), Kotlin 2.4.21, KSP 2.3.13, Compose BOM 2026.09.00, Hilt 2.60.1, Room 2.8.5, WorkManager 2.12.0, DataStore 1.3.0, Navigation 2.10.2, Retrofit 3.0.0, OkHttp 5.5.1, kotlinx.serialization 1.12.0, coroutines 1.11.0. All versions are in `gradle/libs.versions.toml`.

## Build and test

```bash
cd car_frontend
./gradlew :app:assembleDebug        # → app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:testDebugUnitTest    # 57 JVM unit tests
```

Debug `BuildConfig` defaults (in `app/build.gradle.kts`):

| Field | Debug value |
|---|---|
| `API_BASE_URL` | `http://10.0.2.2:8000` (the backend on the host, as the emulator sees it) |
| `ENGINE_URL` | `ws://10.0.2.2:8765/ws` (the `simulated_game_engine` bridge on the host) |
| `CAR_PROVISIONING_KEY` | `dev-provisioning-key` (must match the backend's `CAR_PROVISIONING_KEY`) |

Cleartext HTTP/WS is allowed in **debug only** (`res/xml/network_security_config_debug.xml`). Release builds allow HTTPS only, plus loopback for the on-board engine. Release URLs can be set with `-PapiBaseUrl=… -PengineUrl=… -PcarProvisioningKey=…`.

Both URLs can also be changed at runtime in **Debug settings**.

## Run on the AAOS emulator

```bash
# one-time: create an AVD from the automotive image
avdmanager create avd -n aaos34 -d automotive_1024p_landscape -k "system-images;android-34-ext9;android-automotive;arm64-v8a"
# (without -d the AVD gets a tiny 320x640 screen)
emulator -avd aaos34 &
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n com.hackathon.drivescore/.app.MainActivity
```

Start the other two projects on the host first:
- the backend: `cd ../backend && docker compose up`, which serves `http://localhost:8000`;
- the engine bridge: `cd ../simulated_game_engine && python -m simulated_game_engine`, which serves `ws://localhost:8765/ws`.

The emulator reaches both through `10.0.2.2`. On first launch the app registers the car with `POST /cars/register`. If the backend is unreachable, registration is retried by WorkManager (`register-car`) when the network comes back.

**No engine or backend?** Turn on **Debug settings → FakeEngine (in-app)**. The fake engine emits random alerts every 20–60 s and returns a random `drive.result` when you end the drive. Debug settings also has buttons that send a test alert at each level and that simulate a `drive.result`.

### Simulating driving state (PARKED / MOVING)

The app follows `CarUxRestrictionsManager.isRequiresDistractionOptimization()` (spec §7.2). You can switch it in three ways:
- **Emulator:** Extended controls → *Car data*: set the gear to Drive and the speed above 0.
- **adb:** `adb shell cmd car_service inject-vhal-event 0x11600207 20` (PERF_VEHICLE_SPEED = 20 m/s) and `adb shell cmd car_service inject-vhal-event 0x11400400 8` (GEAR_SELECTION = DRIVE). Check the property IDs against your image.
- **In the app:** Debug settings → *Driving state override* (Auto / Parked / Moving).

While moving, the navigation rail, Add driver, Settings, History, Stats and Leaderboard are hidden. An open secondary screen falls back to Drive or Result. PIN entry still works (spec §7.3).

**Debug settings:** in debug builds, open them from Settings → *Debug settings*, or long-press the car name on the lock screen for 5 s.

## Architecture

There is a single `:app` module. The spec allows this, and the packages keep the boundaries from spec §13:

```
com.hackathon.drivescore
├── app/            Application (Hilt, WorkManager config), MainActivity, AppRoot (status bar, nav rail, NavHosts), AppModule (DI)
├── core/
│   ├── model/      Driver, Drive, Mistake, AlertLevel, AppState, Period, MistakeTypes registry
│   ├── common/     Clock (wall + elapsedRealtime), AppLogger, Formatters, IsoTime
│   ├── database/   Room: entities (spec §11.1), DAOs, AppDatabase (schema exported to app/schemas)
│   ├── datastore/  AppPreferences (DataStore; also the SessionStore), SecureStore + KeystoreAesGcmCipher
│   ├── network/    Retrofit DriveScoreApi, DTOs, CarAuthInterceptor (Car token + X-Request-Id), BaseUrlInterceptor, ConnectivityMonitor, ApiFailure
│   ├── engine/     Protocol + EngineCodec, EngineClient (OkHttp WS, backoff+jitter, ping 10 s), WebSocketEngineGateway (§8.4 rules), FakeEngineGateway, SwitchingEngineGateway
│   ├── car/        DrivingStateMonitor (UX restrictions + override), VehicleSignals (ignition, behind a flag)
│   └── designsystem/ theme, ScoreGauge, Keypad, PinDots, AlertOverlay, charts, timeline
├── data/           CarRepository, DriverRepository (PIN + lockout + linking), DriveRepository (DriveResultSink), Stats/Leaderboard (ApiCache, stale-while-revalidate), MetaRepository,
│                   DriveResultMapper, DriveSyncer + UploadClassifier, SyncDrivesWorker, RegisterCarWorker, SyncScheduler
├── domain/         PinHasher (PBKDF2), PinRecord, PinLockoutPolicy
├── service/        SessionManager (state machine §5), AlertQueue (pure §9 logic), AlertPresenter, AndroidAlertOutput (SoundPool/TTS/focus/notifications), EngineConnectionService (foreground)
└── feature/        lock, adddriver, drive, result (+details), history, stats, leaderboard, settings, debug
```

Key flows:
- **Unlock → session:** `PinViewModel` → `DriverRepository.unlock` (Keystore-decrypt the PIN record, PBKDF2 verify, lockout) → `SessionManager.onUnlocked`. This persists the session to DataStore and sends `session.start` (5 s ack, 3 tries).
- **Result:** engine `drive.result` → `SessionManager.handleResult` → `DriveRepository.persist` (validate, normalize, one Room transaction) → **then** `drive.result.ack` → Result screen → `SyncDrivesWorker`.
- **Reconnect:** `EngineClient` reconnects forever. On each open the gateway sends `hello`. When the engine's `hello` arrives, the gateway ends a foreign `active_session_id` (`DRIVER_SWITCH`) and re-sends `session.start` with the same id.
- **Alerts:** `AlertQueue` decides what is shown and what sounds. `AlertPresenter` runs the effects. When the app is in the background, alerts become heads-up notifications posted from the foreground service.

## End-to-end smoke test (done once, 2026-11-06)

This run used the AAOS API 34 emulator (1024×768), with the backend and the bridge (`--scenario demo --fast`) running on the host. Each of these steps worked:
1. The car registered itself with the backend.
2. A driver created an account on the car and chose a PIN.
3. On the lock screen, a wrong PIN showed "4 attempts left", and the correct PIN unlocked the profile.
4. Tracking started and the engine acknowledged `session.start`.
5. Alerts came in from the real engine: PHONE_CALL VISUAL→AUDIO, then EYES_OFF_ROAD AUDIO→HAPTIC banner→CRITICAL full-screen overlay, then clears.
6. The drive result arrived (score 85), was saved to Room, acked, and uploaded (`201`).
7. Details, History, Stats and Leaderboard all showed the data.

In the emulator, text was entered with `adb shell input text` after disabling the on-screen IME (`adb shell ime disable …`).

## What is implemented and what is not

Implemented (milestones M1–M6):
- The §5 state machine, persisted and resumed after a process restart.
- Car registration with offline retry.
- Create account, sign in, and Forgot PIN / re-link, with server errors mapped to form fields.
- PIN choose/confirm with a trivial-PIN warning; PIN unlock with auto-submit, shake, and the full §10.4 lockout; Change PIN; Remove me, with sync-first and the "N drives will be lost" warning, queued if offline.
- The full engine protocol (§8) and alert presentation (§9: priority, preemption, escalation, cooldown, min/max display, CRITICAL repeats, TTS, audio focus, heads-up notifications).
- Result and Details screens, including unknown mistake types and the timeline.
- Room + `SyncDrivesWorker` handling every §11.3 response class, plus periodic and on-connectivity sync.
- History merging local and server drives with cursor paging; Stats with a Canvas trend chart and mistake bars, an offline cache, and a local "this car only" fallback; Leaderboard with region/global, paging, pull to refresh, and a pinned "my position".
- Driving-state gating, Debug settings, and the in-app FakeEngine.

Stubbed or simplified (be aware):
- **Sounds are generated placeholder WAVs** (`res/raw/chime_warning.wav`, `alarm_critical.wav`), not designed OGG assets. The double chime is the chime played twice.
- **Dark theme only.** There is no light/day theme yet.
- **Vehicle signals** (ignition → end session) are wired behind a debug flag. They need `CAR_POWERTRAIN`, which a normal app won't get, so they are untested on real hardware.
- **Real haptics** don't exist on AAOS for apps. `HAPTIC` is a red pulsing banner plus a double chime, per the spec.
- **Debug screen strings** are hard-coded in English. All user-facing strings are in `strings.xml`.
- **Tests:** 57 JVM unit tests cover PIN hashing and lockout (including clock tampering and reboot), all `SessionManager` transitions, `AlertQueue`, protocol (de)serialization, backoff, result mapping and upload body, the sync response classes and `DriveSyncer`, and `EngineClient` + gateway against a MockWebServer WebSocket (hello, ack/retry, alerts, results, reconnect + resume + foreign-session end), and the `AlertPresenter` wiring. **Not written yet:** Room DAO instrumented tests, Compose UI tests, and a `SyncDrivesWorker` test with `WorkManagerTestInitHelper`. Those need an emulator or Robolectric.
- **Distraction optimization** on real head units still needs to be verified, as spec §3 and §19 #4 say.
