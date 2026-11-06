# Frontend Specification: Driving Score Car App (Android Automotive)

> Status: Draft v1.0 (2026-11-06) · Scope: Hackathon MVP, built to be close to production quality
> Companion document: [`backend_specification.md`](backend_specification.md) (FastAPI + PostgreSQL)

---

## 1. Overview

A **Kotlin app for Android Automotive OS (AAOS)** that runs on the car's head-unit screen. It:

1. Lets each driver **select their profile icon and unlock it with a PIN**, which works fully **offline**. Unlocking starts **tracking** (a drive session) for that driver.
2. Tells the on-board **Game engine** and **Alert engine** who is driving.
3. Shows **real-time alerts** from the Alert engine ("Eyes off the road for too long", "Eyes closed"…) with visuals and sound scaled to severity.
4. When the drive ends, receives the **drive result** from the Game engine (a **score from 0 to 100** plus **mistakes**). It shows the score, and a **Details** view with each mistake's count and duration (yawn count, time spent looking at the phone…).
5. **Stores drives locally** and **syncs** them to the backend, queuing them while offline.
6. Shows **drive history**, **personal statistics**, and **leaderboards** (region or global; day, week, month, year, all time).
7. Manages the car's **driver profiles**: add a driver (one-time online password login), change PIN, remove a driver.

### 1.1 System context

```
                ┌──────────────────────── Car (AAOS head unit) ─────────────────────────┐
                │                                                                       │
 ┌───────────┐  │  ┌─────────────┐   WebSocket (JSON)   ┌──────────────────────────────┐ │
 │  Driver   │──┼─▶│  Car App    │◀────────────────────▶│ Game engine  ws://…:8765/ws  │ │
 │ (touch,   │  │  │ (this spec) │                      └──────────────────────────────┘ │
 │  audio)   │◀─┼──│             │◀────────────────────▶┌──────────────────────────────┐ │
 └───────────┘  │  │             │   WebSocket (JSON)   │ Alert engine ws://…:8766/ws  │ │
                │  └──────┬──────┘                      └──────────────────────────────┘ │
                └─────────┼─────────────────────────────────────────────────────────────┘
                          │ HTTPS (REST/JSON), when online
                          ▼
                 ┌──────────────────┐
                 │ Backend (FastAPI)│── PostgreSQL
                 └──────────────────┘
```

The engines (driver-monitoring camera, wheel sensors, scoring logic) are **outside this spec**. This document defines the **interface contract** the engines must implement (§8). A **mock engine** is provided for development and demos (§15).

---

## 2. Scope

### In scope (MVP)
- AAOS app, landscape, Jetpack Compose UI.
- Driver profile selection, PIN unlock with lockout, add, remove, change PIN.
- Engine WebSocket clients, session lifecycle, alert presentation (banner, sound, TTS), drive result screen with details.
- Local persistence (Room) and background sync (WorkManager).
- History, personal stats, and leaderboard screens (online, with cached offline fallback).
- Driving-state UX restrictions (safe-while-moving UI).
- Mock engine for development, debug settings screen.

### Out of scope
- The engines' internal logic (computer vision, scoring).
- Phone and iOS apps (separate later projects that use the same backend).
- Account registration on the car (see Open Question #1).
- Media, navigation, or any other in-car features.

---

## 3. Platform and Tech Stack

| Concern | Choice |
|---|---|
| OS | **Android Automotive OS**. `minSdk 29` (Android 10), `targetSdk 35`. Tested on the AAOS emulator (API 33/34 "Automotive with Play Store" image). |
| Manifest requirements | `<uses-feature android:name="android.hardware.type.automotive" android:required="true"/>`, `useLibrary 'android.car'` (or `androidx.car.app:app-automotive` not needed, since a full custom UI is used) |
| Language | **Kotlin 2.x**, coroutines + Flow |
| UI | **Jetpack Compose** (Material 3, custom car theme), single-Activity, **Navigation Compose** |
| Architecture | MVVM + Unidirectional Data Flow (ViewModel exposes `StateFlow<UiState>`, UI sends `Intent`s/events) and a clean-ish layering (ui → domain → data) |
| DI | **Hilt** |
| Networking (REST) | **Retrofit** + **OkHttp** + **kotlinx.serialization** converter |
| Networking (engines) | **OkHttp WebSocket** client |
| Local DB | **Room** (KSP) |
| Key-value prefs | **DataStore** (Preferences) |
| Secrets | **Android Keystore** (AES-256-GCM, non-exportable key) via a `SecureStore` wrapper (or Google **Tink** + Keystore) |
| Background work | **WorkManager** (sync) and a **foreground service** (engine connections and the active session) |
| Audio | `SoundPool` (chimes), `TextToSpeech` (voice), `AudioFocusRequest` with transient-may-duck |
| Car APIs | `CarUxRestrictionsManager` (driving restrictions). Optional: `CarPropertyManager` (gear/ignition, §7.4) |
| Charts | Compose Canvas (simple line and bar charts). No heavy chart library. |
| Testing | JUnit5/JUnit4, **Turbine** (Flow), **MockK**, **MockWebServer** (REST + WebSocket), Room in-memory, Compose UI tests |
| Lint / format | ktlint / detekt, Android Lint |

> **AAOS distraction-optimization note:** on AAOS, activities that aren't distraction-optimized are **blocked while the car is moving**. The main activity declares `<meta-data android:name="distractionOptimized" android:value="true"/>`, and the UI itself follows the driving restrictions (§7.3). On production cars this flag is only honored for system or OEM-signed or store-vetted apps. For the hackathon the app is assumed to be **pre-installed / OEM-trusted**, or demoed on the emulator. **Risk to verify early on the target hardware.**

---

## 4. Core Concepts and Glossary

| Term | Meaning |
|---|---|
| **Driver profile** | A user who has been added to *this car*. Stored locally as user ID, username, display name, avatar color, region, and **PIN hash**. Shown as an icon on the lock screen. |
| **PIN** | 4–6 digits chosen by the driver *on this car*. It is **never sent to the backend**. It is used only to unlock the driver profile offline. The same user can have a different PIN on each car. |
| **Car identity** | `car_id` + `car_token`, obtained once from the backend (§10.1). Used for all backend calls, together with `X-Driver-Id`. |
| **Session** | A tracking period for one driver. It starts at PIN unlock and ends when the Game engine delivers `drive.result`. It is identified by a client-generated **`session_id` (UUID)**, which also becomes the **drive ID** in the backend. |
| **Drive** | The persisted result of a session: score, distance, duration, mistakes, events. |
| **Mistake** | A category of unsafe behavior with `count`, `total_duration_s`, `max_duration_s` (types in §8.5). |
| **Alert** | A real-time warning from the Alert engine, with severity `INFO` / `WARNING` / `CRITICAL`. |

Rules:
- **Only one active session per car** at any time.
- A car holds **at most 8 driver profiles** (the backend enforces this too).

---

## 5. App State Machine

```
              ┌────────────────────────────────────────────────────────┐
              ▼                                                        │
   ┌─────────────────────┐ tap icon  ┌──────────────┐ PIN ok  ┌────────┴─────────┐
   │ LOCKED              │──────────▶│ PIN_ENTRY    │────────▶│ SESSION_ACTIVE   │
   │ (driver select)     │◀──────────│ (lockout)    │         │ (tracking; alerts│
   └─────────┬───────────┘  cancel   └──────────────┘         │  overlay live)   │
             │ "Add driver"                                   └──┬───────────┬───┘
             ▼                                                   │drive.result│"End drive"
   ┌─────────────────────┐                                       │            ▼
   │ ADD_DRIVER (online) │                                       │   ┌────────────────┐
   │ login → set PIN     │                                       │   │ ENDING (await  │
   └─────────────────────┘                                       │   │ result ≤ 15 s) │
                                                                 ▼   └───────┬────────┘
                                                        ┌────────────────────┴──┐
                                                        │ RESULT (UNLOCKED_IDLE)│
                                                        │ score + details;      │
                                                        │ history/stats/board   │
                                                        └──┬─────────────┬──────┘
                                         "Start new drive" │             │ "Lock / Switch driver",
                                   (no PIN within 2 min)   │             │ 5 min inactivity, ignition off
                                                           ▼             ▼
                                                   SESSION_ACTIVE      LOCKED
```

| State | Notes |
|---|---|
| `LOCKED` | Default after boot. Shows driver profile icons, "Add driver", and connection status. Works offline. |
| `PIN_ENTRY` | Keypad for the chosen profile. Enforces lockout (§10.4). On success → `SESSION_ACTIVE`. |
| `SESSION_ACTIVE` | `session.start` sent to both engines. The drive screen shows "Tracking: \<name\>", elapsed time, and engine status. Alerts shown live. The secondary screens (history, stats, leaderboard, settings) are reachable **only while parked** (§7.3). |
| `ENDING` | The driver pressed "End drive" (only possible while parked). The app sends `session.end` and waits up to 15 s for `drive.result`. On timeout it shows "Result not available yet". The result is accepted whenever it arrives later (the engine re-delivers until acked, §8.4). |
| `RESULT` / `UNLOCKED_IDLE` | Shows the drive result. The driver stays unlocked (no tracking) and can browse history, stats, and the leaderboard. Auto-locks after 5 min without interaction, on ignition off, or on "Lock". "Start new drive" within 2 min of the result needs no PIN. |

The state is owned by a single **`SessionManager`** (a singleton, scoped to the foreground service) and exposed as a `StateFlow<AppState>`. The active `session_id` and driver are persisted to DataStore, so an **app or process restart during a drive resumes the session** (the app reconnects and re-sends `session.start` with the same ID, §8.4).

---

## 6. Screens

General layout: **landscape**, dark theme by default. A persistent **top status bar** shows the current driver's avatar and name, a session indicator (● Tracking), engine connection dots (Game, Alert), an online/offline indicator, and the count of pending uploads. A left **navigation rail** appears only when unlocked and parked.

### 6.1 Driver Select (Lock screen): `LOCKED`
- A grid of up to 8 **profile tiles**: circular avatar (initials on `avatar_color`), display name, and a small "sync pending" badge if that driver has unsynced drives.
- Tile states: normal; **Locked out** ("Try again in 0:45"); **PIN disabled** ("Sign in with password", §10.4).
- **"+ Add driver"** tile. Disabled with a hint when offline or at 8 profiles.
- Empty state (first run): "No drivers yet. Add the first driver" plus a big Add button.
- Footer: car name. Long-press the car name for 5 s → **Debug settings** (§15).

### 6.2 PIN Entry: `PIN_ENTRY`
- Large numeric keypad (keys ≥ 96 dp), PIN dots (4–6), a backspace key, and a "Cancel" button.
- **Auto-submit** when the length matches the stored PIN length (the length is stored with the hash, so users with 4-digit PINs don't need to press OK).
- Wrong PIN: a shake animation, "Wrong PIN. 3 attempts left", and haptic feedback if available.
- Lockout: keypad disabled with a countdown.
- A "Forgot PIN?" link → password login (same as the Add-driver flow, online), then set a new PIN.

### 6.3 Drive screen: `SESSION_ACTIVE`
Minimal and glanceable, since this is the screen visible while driving:
- A large "**Tracking, \<Display name\>**", elapsed time, and a pulsing status dot.
- Engine status chips: `Game ✓` / `Alert ✓`, or `Reconnecting…` in amber.
- **No live score.** The engine only scores at the end of the drive.
- While **parked**, it also shows the "End drive" button and access to the navigation rail.
- An alerts overlay is drawn on top (§6.9).

### 6.4 Drive Result: `RESULT`
- **Score gauge**: a large circular gauge with the integer score in the center. Color bands: **≥ 85 green**, **60–84 amber**, **< 60 red**. A short label: "Excellent", "Good", or "Needs attention".
- A summary row: duration (h:mm), distance (km, one decimal), date and time.
- **Mistake chips**: one chip per mistake type with count > 0 (icon, label, count), sorted by count. "No mistakes 🎉" when empty.
- Sync status: "Saved to cloud ✓", "Will upload when online", or "Upload failed – tap to retry".
- Buttons: **Details**, **Start new drive**, **Lock / Switch driver**.

### 6.5 Drive Details (from Result or History)
- A table with one row per mistake type:

| Mistake | Count | Total time | Longest |
|---|---|---|---|
| 👁 Eyes off road | 4 | 9.6 s | 3.1 s |
| 📱 Phone use | 1 | 6.0 s | 6.0 s |
| 😴 Eyes closed | 0 | – | – |
| 🥱 Yawns | 2 | – | – |
| ✋ Hands off wheel | 1 | 4.0 s | 4.0 s |
| 📞 Phone call | 0 | – | – |
| 🍔 Eating / drinking | 0 | – | – |
| 🚬 Smoking | 0 | – | – |

- All 8 known types are always listed, with zero rows dimmed. Unknown types reported by the engine are appended with their raw name, title-cased.
- Durations are formatted as `s` below 60 s and `m:ss` above.
- **Timeline** (if `events` were provided): a horizontal bar across the drive's duration, with colored marks per event type. Tap a mark to see its type, time, and duration.

### 6.6 History (parked only)
- A reverse-chronological list: date and time, car name (if not this car), duration, distance, score badge (colored), and mistake count. A sync icon on unsynced items.
- Data source: **local Room drives (this car)** merged with **server drives (`GET /drives`, all cars)** when online, de-duplicated by ID. Infinite scroll via cursor.
- Tap → Drive Details. Server-only drives are fetched with `GET /drives/{id}` and cached.
- Offline: shows local drives plus previously cached server drives, with an "Offline: showing saved data" banner.

### 6.7 Statistics (parked only)
- Period tabs: **Day · Week · Month · Year · All**.
- KPI tiles: weighted score (big), drives, distance, driving time, best score.
- Rank tiles: "#14 of 233 in 🇷🇸 RS" and "#412 of 5,120 Global", or "Drive 12 km more to qualify".
- **Trend chart**: weighted score per bucket as a line, with drive count as faint bars (from `series`).
- **Mistake breakdown**: horizontal bars per type (count), with a "per 100 km" figure.
- Source: `GET /stats/me?period=…`. Cached per period (Room `api_cache`). Offline: the cached response with "Last updated 2 h ago". If nothing is cached, stats are computed locally from Room drives on this car, labeled "Offline: this car only".

### 6.8 Leaderboard (parked only)
- Period tabs (as in Stats) and a scope toggle **My region (RS) | Global**.
- A list of ranks: rank number (🥇🥈🥉 for the top 3), avatar, display name, weighted score, distance. **"My position" is pinned** at the bottom if it's outside the visible page. "Not ranked yet: drive X km more" if not qualified.
- Paged with `limit=50&offset`. Pull to refresh.
- Requires online. Offline: the last cached board with a timestamp, or "Connect to the internet to see the leaderboard".

### 6.9 Alerts overlay (all states while a session is active)
See §9 for the behavior. Visuals:
- **INFO**: a small toast at the bottom center, icon and text, for 3 s.
- **WARNING**: a full-width **amber banner** at the top (≥ 120 dp tall, icon and text ≥ 32 sp), with a chime and TTS.
- **CRITICAL**: a **full-screen red overlay** (semi-transparent, pulsing red border, huge icon and text), with an alarm tone and TTS. Stays until `alert.clear` arrives or the maximum display time passes.

### 6.10 Add Driver (from Lock screen, online only)
1. **Sign in**: username and password fields (on-screen keyboard), "Sign in" button → `POST /cars/me/drivers`. Errors: wrong credentials, car full, offline, server error.
2. **Welcome \<display name\>**: confirms the profile (avatar, region).
3. **Choose PIN**: enter a 4–6 digit PIN, then confirm it. Rejects trivial PINs (`0000`, `1234`, all-same, strictly sequential) with a warning, though the driver can override the warning.
4. Done → the profile appears on the lock screen. **Optional:** "Start driving now" goes straight to `SESSION_ACTIVE`.
- If the user is **already on this car**, skip to "Reset PIN?" (the same flow as Forgot PIN).
- Only available while parked.

### 6.11 Driver Settings (unlocked, parked)
- **Change PIN**: current PIN → new PIN → confirm.
- **Remove me from this car**: confirmation dialog. If the driver has unsynced drives, it first tries to sync them. If that fails, it warns "N drives not uploaded yet will be lost" and offers "Remove anyway" or "Cancel". On confirm: `DELETE /cars/me/drivers/{user_id}` (queued if offline), then all local data for that driver is wiped, and the app returns to `LOCKED`.
- **Profile info** (read-only on the car for the MVP): username, display name, region. "Edit in the phone app (coming soon)".
- **Lock now**.

---

## 7. Driving Safety and Car Integration

### 7.1 Principles
- While moving, the app shows **only** the Drive screen, the Result screen (read-only, no navigation), and alerts. No text input, no scrolling lists.
- Large text (body ≥ 24 sp, titles ≥ 32 sp, numbers ≥ 48 sp), touch targets ≥ **76 dp**, high contrast (WCAG AA minimum), dark theme for night. Light theme follows the system day/night mode.
- Alerts never require a tap to dismiss.

### 7.2 Driving state
`CarUxRestrictionsManager` is observed and exposed as `StateFlow<DrivingState>` (`PARKED` / `MOVING`). The rule is `isRequiresDistractionOptimization()` → `MOVING`.

### 7.3 Behavior while moving

| Feature | Parked | Moving |
|---|---|---|
| Driver select / PIN entry | ✔ | ✔ (PIN keypad allowed, since tracking must be able to start; the keypad is a large-target, minimal UI) |
| Add driver, Change PIN, Remove driver | ✔ | ✖ "Available when parked" |
| History, Stats, Leaderboard, Debug settings | ✔ | ✖ (navigation rail hidden; an open screen falls back to the Drive or Result screen) |
| End drive button | ✔ | ✖ |
| Alerts | ✔ | ✔ |

### 7.4 Vehicle signals (optional, behind a feature flag)
- If permitted on the target head unit, the app reads `GEAR_SELECTION` and ignition state via `CarPropertyManager`:
  - Ignition off → `session.end` (reason `IGNITION_OFF`), then lock.
  - These are hints only. The **Game engine remains the authority** for when a drive ends.
- If the permissions are unavailable, the app relies on the engine and the manual "End drive" button.

---

## 8. Engine Integration Contract

Both engines run **WebSocket servers** on the car. The app is the **client**. A single JSON envelope format is shared by both engines.

### 8.1 Endpoints and connection
| Engine | Default URL | Configurable |
|---|---|---|
| Game engine | `ws://127.0.0.1:8765/ws` | Debug settings / `BuildConfig` |
| Alert engine | `ws://127.0.0.1:8766/ws` | Debug settings / `BuildConfig` |

- The emulator connects to a mock engine on the host at `ws://10.0.2.2:8765/ws` and `:8766`.
- OkHttp `pingInterval = 10 s`. A missing pong → reconnect.
- Reconnect with **exponential backoff**: 0.5, 1, 2, 4, 8 s, capped at 15 s, with ±20% jitter. Retries continue indefinitely while the foreground service runs.
- Connections are owned by the **foreground service** (`EngineConnectionService`, type `connectedDevice` or `specialUse`). They stay up while the app is in the background (for example while navigation is in the foreground).

### 8.2 Envelope

```json
{
  "v": 1,
  "type": "session.start",
  "msg_id": "3f2a0c3e-…",          // UUID, unique per message
  "ts": "2026-11-06T08:01:12.345Z",
  "payload": { }
}
```
- Unknown `type`s are logged and ignored. Unknown fields are ignored (`ignoreUnknownKeys = true`).
- `v` is the protocol version. If the engine replies with an `error` of code `UNSUPPORTED_VERSION`, the app shows "Engine incompatible".

### 8.3 Message catalogue

| Direction | `type` | Payload | Notes |
|---|---|---|---|
| App → both | `hello` | `{ "app_version": "1.0.0", "car_id": "uuid", "protocol": 1 }` | Sent on every (re)connect |
| Engine → App | `hello` | `{ "engine": "game" \| "alert", "engine_version": "1.3.0", "active_session_id": "uuid" \| null }` | Lets the app detect a session mismatch |
| App → both | `session.start` | `{ "session_id": "uuid", "user_id": "uuid", "display_name": "Mihajlo", "car_id": "uuid", "started_at": "ts" }` | Must be **idempotent** for the same `session_id` |
| Engine → App | `session.ack` | `{ "session_id": "uuid" }` | If no ack arrives within 5 s, the app re-sends (max 3), then shows "Engine not responding" |
| App → both | `session.end` | `{ "session_id": "uuid", "reason": "MANUAL" \| "DRIVER_SWITCH" \| "IGNITION_OFF" \| "APP_SHUTDOWN" }` | The Game engine responds with `drive.result` |
| Game → App | `drive.result` | see §8.5 | May arrive **spontaneously** (the engine detected the drive end) or after `session.end` |
| App → Game | `drive.result.ack` | `{ "session_id": "uuid" }` | Sent **after** the result is persisted in Room |
| Alert → App | `alert` | `{ "alert_id": "uuid", "session_id": "uuid", "type": "EYES_CLOSED", "severity": "CRITICAL", "message": "Eyes closed for too long!", "started_at": "ts", "ttl_s": 10 }` | `message` is optional. The app has default localized texts per type. |
| Alert → App | `alert.clear` | `{ "alert_id": "uuid" }` | The condition has ended |
| Either → App | `error` | `{ "code": "NO_ACTIVE_SESSION", "message": "…", "ref_msg_id": "uuid" }` | Logged. Shown in the debug view. |

### 8.4 Reliability rules
1. **Session resumption:** after a reconnect during `SESSION_ACTIVE`, the app sends `hello` and then re-sends `session.start` with the **same** `session_id`. The engine must continue the existing session, not restart it.
2. **Session mismatch:** if the engine's `hello.active_session_id` differs from the app's, the app sends `session.end` (reason `DRIVER_SWITCH`) for the engine's session, then `session.start` for its own.
3. **At-least-once results:** the Game engine keeps each `drive.result` until it receives `drive.result.ack`. On every reconnect it re-sends any unacked results. The app de-duplicates by `session_id` (Room primary key).
4. A result whose `session_id` matches no session the app knows is still persisted, attributed via its `user_id` (included in the payload). If that user has no profile on this car, it is dropped with an error log.
5. Alerts are **fire-and-forget**. If the connection is lost, alerts that were showing are auto-cleared after their `ttl_s` (default 10 s).

### 8.5 `drive.result` payload (canonical)

This is identical to the backend upload body (backend spec §8.6), minus `id`, plus routing fields:

```json
{
  "session_id": "6b1e6f0a-3c55-4d0b-9f9d-2f8c3f0d6a11",
  "user_id": "uuid",
  "started_at": "2026-11-06T08:01:12Z",
  "ended_at":   "2026-11-06T08:34:40Z",
  "distance_km": 21.437,
  "score": 87.5,
  "engine_version": "game-engine/1.3.0",
  "mistakes": [
    { "type": "EYES_OFF_ROAD", "count": 4, "total_duration_s": 9.6, "max_duration_s": 3.1 },
    { "type": "YAWN", "count": 2, "total_duration_s": null, "max_duration_s": null }
  ],
  "events": [
    { "type": "EYES_OFF_ROAD", "started_at": "2026-11-06T08:05:03Z", "duration_s": 3.1 }
  ],
  "extra": { }
}
```

Known mistake types (shared with the backend): `EYES_OFF_ROAD`, `PHONE_USE`, `EYES_CLOSED`, `YAWN`, `HANDS_OFF_WHEEL`, `PHONE_CALL`, `EATING_DRINKING`, `SMOKING`. Any `^[A-Z][A-Z0-9_]{1,39}$` value is accepted.

Validation in the app, before persisting (invalid → log, then ack anyway so the engine doesn't loop, and show "Result invalid"): `0 ≤ score ≤ 100`, `ended_at ≥ started_at`, counts ≥ 0.

### 8.6 Alert types and default texts

| `type` | Default severity | Default text (EN) | Icon |
|---|---|---|---|
| `EYES_OFF_ROAD` | WARNING | "Eyes on the road" | 👁 |
| `PHONE_USE` | WARNING | "Put the phone down" | 📱 |
| `EYES_CLOSED` | CRITICAL | "Wake up! Eyes closed" | 😴 |
| `DROWSINESS` | WARNING | "You seem tired. Consider a break" | ☕ |
| `HANDS_OFF_WHEEL` | WARNING → CRITICAL (engine escalates) | "Hands on the wheel" | ✋ |
| `PHONE_CALL` | WARNING | "Use hands-free for calls" | 📞 |
| `EATING_DRINKING` | INFO | "Focus on driving" | 🍔 |
| `SMOKING` | INFO | "Focus on driving" | 🚬 |
| *(unknown)* | as sent | `message`, or "Drive carefully" | ⚠️ |

The engine's `severity` and `message` always take precedence over these defaults.

---

## 9. Alerts Behavior

### 9.1 Presentation

| Severity | Visual | Sound | TTS | Display time |
|---|---|---|---|---|
| INFO | Toast (bottom) | none | no | 3 s |
| WARNING | Amber top banner | short chime (`chime_warning.ogg`) | yes, once | until `alert.clear`, min 2 s, max `ttl_s` (default 8 s) |
| CRITICAL | Full-screen red pulsing overlay | repeating alarm tone (`alarm_critical.ogg`) every 2 s | yes, repeated every 6 s | until `alert.clear`, max `ttl_s` (default 15 s) |

### 9.2 Queueing and deduplication
- Only **one** visual alert is shown at a time. Priority is CRITICAL > WARNING > INFO, and a higher severity **preempts** a lower one. The preempted alert resumes if it is still active.
- The same `alert_id` → update in place (for example an escalation from WARNING to CRITICAL).
- The same `type` within a **10 s cooldown** after its clear → the visual is shown, but the chime and TTS are not replayed (to avoid nagging).
- Audio uses `AudioFocusRequest(AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)` with `USAGE_ASSISTANCE_NAVIGATION_GUIDANCE`, so media is ducked rather than stopped.
- TTS language follows the system locale. TTS initialization failure → sound only.

### 9.3 When the app is not in the foreground
- The foreground service posts the alert as a **heads-up notification** (an `IMPORTANCE_HIGH` channel "Driver alerts", category `CATEGORY_NAVIGATION` or `CATEGORY_ALARM`), and plays the sound and TTS from the service. A CRITICAL alert also tries to bring the app's overlay to the front if the platform allows it.
- Notifications are cancelled on `alert.clear`.

---

## 10. Identity, Profiles and PIN

### 10.1 Car registration (first launch)
1. If there is no `car_token` in `SecureStore` → `POST /api/v1/cars/register` with `X-Provisioning-Key` (from `BuildConfig`) and `{ name: "<device name or 'My Rivian'>", model, vin? }`.
2. Store `car_id` (DataStore) and `car_token` (SecureStore).
3. Offline on first launch: the app works for UI preview, but adding a driver is impossible. It retries registration whenever the connection comes back (WorkManager unique work `register-car`).

### 10.2 Adding a driver
- `POST /api/v1/cars/me/drivers {username, password}` with `Authorization: Car <token>`.
- The response `UserProfile` → a local `driver_profiles` row. The password is **never stored**.
- PIN set in the following step (§6.10).

### 10.3 PIN storage
- PIN hash: **PBKDF2WithHmacSHA256**, a 16-byte random salt, **210,000 iterations**, 32-byte output. The hash is stored along with the salt, iteration count, and PIN length.
- The whole PIN record is **encrypted with a non-exportable Android Keystore AES-GCM key** before it is written to Room. This blocks offline brute-forcing of a 4–6 digit PIN from a copied database.
- Comparison is constant-time.
- The PIN is never logged and never sent over the network or to the engines.

### 10.4 Lockout policy (per profile, persisted)

| Consecutive failures | Effect |
|---|---|
| 1–4 | "Wrong PIN, N attempts left" |
| 5 | Locked for 30 s |
| 6, 7, 8, 9 | Locked for 1, 2, 5, 15 min |
| 10 | **PIN disabled.** The driver must sign in with their password (online) and set a new PIN. |

- The counter resets on success. Lockout end times are stored as wall-clock time **and** checked against `SystemClock.elapsedRealtime()` since boot, so changing the clock doesn't bypass a lockout.
- "Forgot PIN" = password login on this car via `POST /cars/me/drivers` (which re-links) → set a new PIN.

### 10.5 Removing a driver
See §6.11. Local wipe: `driver_profiles` row, cached server data for that user, and synced drives. Unsynced drives are removed only after the explicit "Remove anyway" confirmation.

---

## 11. Local Data and Sync

### 11.1 Room schema

```kotlin
@Entity(tableName = "driver_profiles")
data class DriverProfileEntity(
    @PrimaryKey val userId: String,          // backend UUID
    val username: String,
    val displayName: String,
    val avatarColor: String,
    val regionCode: String,
    val encryptedPinRecord: ByteArray,       // Keystore-encrypted {hash, salt, iterations, length}
    val failedAttempts: Int = 0,
    val lockoutUntilEpochMs: Long? = null,
    val lockoutUntilElapsedMs: Long? = null,
    val pinDisabled: Boolean = false,
    val addedAt: Long,
    val lastUsedAt: Long?,
    val pendingUnlink: Boolean = false       // DELETE queued while offline
)

@Entity(tableName = "drives", indices = [Index("userId"), Index("syncState")])
data class DriveEntity(
    @PrimaryKey val id: String,              // = session_id
    val userId: String,
    val carId: String,
    val startedAt: Long, val endedAt: Long,
    val durationS: Int,
    val distanceKm: Double?,
    val score: Double,
    val engineVersion: String?,
    val rawJson: String,                     // exact drive.result payload (for upload)
    val source: DriveSource,                 // LOCAL (this car) | REMOTE (fetched from server)
    val syncState: SyncState,                // PENDING | SYNCING | SYNCED | FAILED_PERMANENT
    val syncAttempts: Int = 0,
    val lastSyncError: String? = null,
    val createdAt: Long
)

@Entity(tableName = "drive_mistakes", primaryKeys = ["driveId", "type"])
data class DriveMistakeEntity(val driveId: String, val type: String, val count: Int,
                              val totalDurationS: Double?, val maxDurationS: Double?)

@Entity(tableName = "drive_events", indices = [Index("driveId")])
data class DriveEventEntity(@PrimaryKey(autoGenerate = true) val id: Long = 0,
                            val driveId: String, val type: String,
                            val startedAt: Long, val durationS: Double?)

@Entity(tableName = "api_cache", primaryKeys = ["userId", "key"])
data class ApiCacheEntity(val userId: String, val key: String,   // e.g. "stats:week", "lb:month:RS:0"
                          val json: String, val fetchedAt: Long)
```

DataStore holds: `car_id`, `car_name`, active session (`session_id`, `user_id`, `started_at`, state), engine URLs (debug), `api_base_url` (debug), and feature flags.
`SecureStore` (Keystore-encrypted) holds `car_token`.

### 11.2 Drive persistence flow
1. A `drive.result` arrives → validate → **one Room transaction**: insert `drives` (`syncState=PENDING`), `drive_mistakes`, and `drive_events`.
2. Send `drive.result.ack` to the Game engine.
3. Navigate to the Result screen.
4. Enqueue **WorkManager** `SyncDrivesWorker` (unique work `sync-drives`, policy `APPEND_OR_REPLACE`, network constraint `CONNECTED`).

### 11.3 SyncDrivesWorker
- Picks drives where `syncState IN (PENDING, SYNCING)`, oldest first, in batches of 20. Uploads each with `POST /api/v1/drives`, headers `Authorization: Car <token>` and `X-Driver-Id: <userId>`, and the body `rawJson` + `id`.

| Response | Action |
|---|---|
| 201 / 200 | `SYNCED` |
| 409 `DRIVE_CONFLICT`, 422 | `FAILED_PERMANENT` (shown in history with an error icon and a "Details" option). Logged. |
| 403 `DRIVER_NOT_LINKED` | Keep `PENDING` and mark the profile "Sign in again to upload" (the driver was revoked remotely). Re-linking via password resumes uploads. |
| 401 / 403 `CAR_REVOKED` | Stop. The car must re-register (shown in debug settings). Drives are kept. |
| 5xx / network | `Result.retry()`, with WorkManager exponential backoff starting at 30 s |

- Also runs as **periodic work** every 30 min, and when connectivity changes to online.
- A queued `pendingUnlink` → `DELETE /cars/me/drivers/{id}` runs in the same worker before cleanup.

### 11.4 Remote data fetch
- History: `GET /drives` pages are written into `drives` (`source=REMOTE`, `SYNCED`), so they are available offline.
- Stats and leaderboard: written to `api_cache`. The UI reads Room/cache first, then refreshes from the network (stale-while-revalidate). A network refresh is skipped if the cache is younger than 60 s.

---

## 12. Backend Integration

- Base URL from `BuildConfig.API_BASE_URL`: emulator `http://10.0.2.2:8000`, release `https://…`. It can be overridden in debug settings.
- An OkHttp **interceptor** adds `Authorization: Car <car_token>`, plus `X-Driver-Id` for driver-scoped calls (a Retrofit `@Tag` or a per-call header), plus `X-Request-Id` (UUID).
- Timeouts: connect 10 s, read 20 s. Cleartext HTTP allowed **only** in the debug build (`network_security_config`).
- DTOs: Kotlin `@Serializable` data classes with `@SerialName` for snake_case. They can optionally be generated from the backend's `/openapi.json`.

| App feature | Endpoint (see the backend spec) |
|---|---|
| Car registration | `POST /api/v1/cars/register` |
| Add driver / Forgot PIN | `POST /api/v1/cars/me/drivers` |
| Remove driver | `DELETE /api/v1/cars/me/drivers/{user_id}` |
| Refresh profile data | `GET /api/v1/users/me` (driver-scoped). Run on unlock when online, to update display name, region, and color. |
| Upload drive | `POST /api/v1/drives` |
| History | `GET /api/v1/drives`, `GET /api/v1/drives/{id}` |
| Stats | `GET /api/v1/stats/me?period=` |
| Leaderboard | `GET /api/v1/leaderboards?period=&region=&limit=&offset=` |
| Mistake labels | `GET /api/v1/meta/mistake-types` (cached. Built-in defaults are bundled.) |

Connectivity is observed with `ConnectivityManager.NetworkCallback` → `StateFlow<Boolean>` and shown in the status bar.

---

## 13. Architecture and Code Organization

```
android/
├── app/                              # Application, MainActivity, NavHost, Hilt setup, theme
├── core/
│   ├── model/                        # Domain models: Driver, Drive, Mistake, Alert, Severity, Period…
│   ├── common/                       # Clock, dispatchers, Result types, logging
│   ├── database/                     # Room DB, DAOs, entities, migrations
│   ├── datastore/                    # Preferences + SecureStore (Keystore)
│   ├── network/                      # Retrofit API, DTOs, interceptors, connectivity monitor
│   ├── engine/                       # EngineClient (OkHttp WS), protocol DTOs, GameEngine/AlertEngine facades, FakeEngine
│   ├── car/                          # DrivingStateMonitor (UX restrictions), VehicleSignals (optional)
│   └── designsystem/                 # Car theme, typography, ScoreGauge, Keypad, AlertBanner, charts
├── data/                             # Repositories: DriverRepository, DriveRepository, StatsRepository,
│                                     #   LeaderboardRepository, CarRepository; SyncDrivesWorker
├── domain/                           # Use cases: UnlockWithPin, AddDriver, StartSession, EndSession,
│                                     #   HandleDriveResult, ObserveAlerts, RemoveDriver, ChangePin
├── service/                          # EngineConnectionService (foreground), SessionManager, AlertPresenter (sound/TTS/notifications)
└── feature/
    ├── lock/        (driver select, PIN entry)
    ├── adddriver/
    ├── drive/       (active session screen)
    ├── result/      (result + details)
    ├── history/
    ├── stats/
    ├── leaderboard/
    ├── settings/    (driver settings)
    └── debug/       (engine/API URLs, mock controls, logs)
```

For the hackathon, the modules may be collapsed into packages of a single `:app` module with the same structure. The boundaries above must still be respected.

**Key components**
- `EngineClient`: a generic WebSocket client: connect, reconnect, send, `incoming: SharedFlow<EngineMessage>`, `status: StateFlow<ConnectionStatus>`.
- `GameEngine` / `AlertEngine`: typed facades over `EngineClient`.
- `SessionManager`: the state machine from §5. It coordinates the engines, persistence, and navigation events.
- `AlertPresenter`: the queue, priority, and cooldown from §9. It drives the Compose overlay state, `SoundPool`, `TextToSpeech`, and notifications.
- `DrivingStateMonitor`: exposes `PARKED`/`MOVING`. A Compose `LocalDrivingState` gates UI.

---

## 14. UX and Design Guidelines

- **Theme:** dark by default (background `#0E1116`, surface `#1A1F27`). Accent is a brand-neutral blue. Score colors: green `#2BB673`, amber `#F5A524`, red `#E5484D`. Alert colors are reserved for alerts.
- **Typography:** a large-scale type ramp (body 24 sp, title 32 sp, display 64–96 sp for the score).
- **Motion:** subtle. Only the CRITICAL overlay pulses (1 Hz). Respects the system "remove animations" setting.
- **Glanceability:** a driver should be able to read any screen shown while driving in < 2 s.
- **Accessibility:** content descriptions on all icons. Mistake info is conveyed by icon + text + color (never color alone).
- **Localization:** all strings in `strings.xml`. English for the MVP, with structure ready for Serbian and other languages. Units: km (metric) by default, with miles behind a setting later.
- **Formatting:** score as an integer. Distance with 1 decimal. Durations `s` / `m:ss` / `h:mm`. Dates relative ("Today 08:34", "Yesterday", then `dd MMM`).

---

## 15. Debug Tools and Mock Engine

### 15.1 Debug settings screen (debug builds, or a 5 s long-press on the car name)
- Edit the Game and Alert engine URLs and the API base URL. Toggle **FakeEngine (in-app)**.
- Live connection log (the last 200 engine messages, in and out).
- Buttons: send a test alert (each severity), simulate `drive.result`, force sync, re-register the car, wipe all local data.
- Toggle the driving-state override (`PARKED`/`MOVING`) for testing on the emulator.

### 15.2 FakeEngine (in-app)
- An implementation of the `GameEngine` and `AlertEngine` facades that needs no network: it emits random alerts every 20–60 s during a session and produces a randomized `drive.result` on `session.end`.

### 15.3 Mock engine server (`tools/mock_engine/`)
- A small **Python** (`websockets`) script that runs both servers (`:8765` and `:8766`) and implements the full §8 protocol, including session resumption and unacked-result re-delivery.
- An interactive CLI: `alert EYES_CLOSED critical`, `clear`, `end` (emits `drive.result` with a configurable score and mistakes), `scenario demo` (a scripted 2-minute drive with a handful of alerts, then the result).
- Used for demos and as the reference implementation for the real engine team.

---

## 16. Testing Strategy

| Level | What |
|---|---|
| Unit | PIN hashing and verification, the lockout policy (including clock tampering), `SessionManager` transitions (all edges in §5), `AlertPresenter` priority, preemption, and cooldown, protocol (de)serialization, and the mapping of results into entities and DTOs. |
| Integration | `EngineClient` against `MockWebServer` WebSocket: reconnect, resume, re-delivery, and de-duplication. `SyncDrivesWorker` with `MockWebServer` (every response class in §11.3) using `WorkManagerTestInitBuilder`. Room DAO tests. |
| UI (Compose) | Driver select states, PIN entry (auto-submit, lockout UI), Result and Details rendering for 0 or many mistakes and unknown types, gating of screens by driving state. |
| End-to-end (manual / scripted) | AAOS emulator + `tools/mock_engine` + the backend in Docker: add driver → PIN → drive with alerts → result → sync → appears in stats and leaderboard. Then repeat with airplane mode (queued sync). |

---

## 17. Implementation Milestones (hackathon)

1. **M1 Shell:** AAOS project, theme, navigation, the driving-state monitor, debug screen, FakeEngine.
2. **M2 Profiles:** car registration, Add driver, PIN set and unlock, lockout, lock-screen grid.
3. **M3 Engines:** `EngineClient`, the session lifecycle, `drive.result` persistence and ack, the Result and Details screens.
4. **M4 Alerts:** overlay UI, sound, TTS, queue and priority, background notifications.
5. **M5 Sync and data:** `SyncDrivesWorker`, History, Stats, Leaderboard with caching.
6. **M6 Polish:** mock engine demo scenario, end-to-end run-through, edge cases (offline, restarts, engine drops).

---

## 18. Future Work

- Create account on the car, QR pairing with the phone app (scan → link the driver without typing a password).
- Live in-drive coaching (a running score, if the engine supports streaming).
- Weekly summaries, achievements and streaks, and push notifications to the phone.
- Per-driver car preferences (seat, mirrors, climate) via vehicle APIs.
- Face-ID login via the in-cabin camera as an alternative to the PIN.
- Miles/imperial units, more languages.

---

## 19. Open Questions and Assumptions

| # | Topic | Assumption | To confirm |
|---|---|---|---|
| 1 | Account creation | Accounts are created outside the car for now (Swagger/seed, later the phone app). | Add a "Create account" step on the car for the demo? |
| 2 | Engine transport | WebSocket servers on localhost, ports 8765 and 8766, implementing §8. | Engine team agrees? Do they run on the same head unit or a separate ECU (in which case the host IP changes)? |
| 3 | Distance | The Game engine includes `distance_km` in `drive.result`. | If not, the backend switches to duration weighting, or the app reads the odometer (needs a car permission). |
| 4 | Distraction optimization | The app is installed as an OEM-trusted app, or demoed on the emulator. | Verify on the target head unit. |
| 5 | PIN entry while moving | Allowed (minimal keypad), so a passenger swap or a late unlock can still start tracking. | Prefer blocking unlock while moving? |
| 6 | Auto-lock timing | 5 min idle after a drive. "Start new drive" without a PIN within 2 min. | Values OK? |
| 7 | Alert texts | English defaults per §8.6. The engine may override them. | Final copy and languages? |
