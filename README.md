# RIVIAN_hackathon_2026: DriveScore

A driver-safety scoring system for the car. A driver-monitoring **engine** detects risky behaviour (phone call, eyes off road, …) and raises escalating alerts. At the end of every drive it produces a **score from 0 to 100**. The **car app** shows the alerts and results. The **backend** stores drives and computes personal stats and leaderboards.

```
 ┌──────────────────────────┐  WebSocket :8765   ┌─────────────────────────────┐  HTTP :8000   ┌─────────────────────┐
 │ simulated_game_engine/   │ ─────────────────► │ car_frontend/               │ ────────────► │ backend/            │
 │ bridge + driver simulator│ alerts, results    │ Android Automotive app      │ drives, stats │ FastAPI + Postgres  │
 │ wraps RivianHackatonEngine│ ◄───────────────── │ (Kotlin, Jetpack Compose)   │ leaderboards  │                     │
 └──────────────────────────┘  session start/end └─────────────────────────────┘               └─────────────────────┘
```

| Folder | What it is | Owner | Details |
|---|---|---|---|
| [`RivianHackatonEngine/`](RivianHackatonEngine/) | The real engine: `AlertEngine` + `GameEngine` (Python) | engine team (don't modify) | Requests for it go in [`requested_changes.md`](requested_changes.md) |
| [`simulated_game_engine/`](simulated_game_engine/) | WebSocket bridge around the engine, plus a CLI that simulates the driver | – | [README](simulated_game_engine/README.md) · [spec](simulated_game_engine/simulated_game_engine_specification.md) |
| [`car_frontend/`](car_frontend/) | The car's head-unit app (Android Automotive OS) | – | [README](car_frontend/README.md) · [spec](car_frontend/frontend_specification.md) |
| [`backend/`](backend/) | REST API: accounts, cars, drives, stats, leaderboards | – | [README](backend/README.md) · [spec](backend/backend_specification.md) |

The specs are the source of truth. Team rules are in [`RULES.md`](RULES.md).

---

## Running the whole demo locally (macOS)

### 1. Prerequisites (one-time)

| Tool | Used for | Install |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | Python 3.12 + dependencies for the backend and the bridge | `brew install uv` |
| PostgreSQL 16 | backend database (or use Docker, see 3b) | `brew install postgresql@16` |
| Android Studio | Android SDK, emulator, and its bundled Java (JBR) | [developer.android.com/studio](https://developer.android.com/studio) |
| AAOS emulator image | the car head unit | `system-images;android-34-ext9;android-automotive;arm64-v8a` (Android Studio → SDK Manager) |
| Docker, optional | backend in containers instead of a local Postgres | `brew install colima docker docker-compose` |

Gradle needs Java. Use the one bundled with Android Studio, and set it in every terminal that builds the car app (or add the line to `~/.zshrc`):

```sh
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
```

Without it, `./gradlew` fails with *"Unable to locate a Java Runtime"*.

### 2. First-time setup

**Database (local Postgres):**
```sh
initdb -D /opt/homebrew/var/postgresql@16                           # only if brew didn't create the cluster
/opt/homebrew/opt/postgresql@16/bin/pg_ctl -D /opt/homebrew/var/postgresql@16 start
psql postgres -c "CREATE ROLE drive LOGIN PASSWORD 'drive' CREATEDB;"
psql postgres -c "CREATE DATABASE drive OWNER drive;" -c "CREATE DATABASE drive_test OWNER drive;"
for db in drive drive_test; do psql $db -c "CREATE EXTENSION IF NOT EXISTS citext; CREATE EXTENSION IF NOT EXISTS pgcrypto;"; done
```

**Backend:**
```sh
cd backend
uv sync
cp .env.example .env && sed -i '' 's/@db:5432/@localhost:5432/' .env
uv run alembic upgrade head
uv run python -m scripts.seed        # demo data; login: demo / demo-password-123
```

**Bridge:**
```sh
cd simulated_game_engine && uv sync
```

**Emulator (AVD):**
```sh
~/Library/Android/sdk/cmdline-tools/latest/bin/avdmanager create avd -n drivescore_aaos34 \
  -d automotive_1024p_landscape -k "system-images;android-34-ext9;android-automotive;arm64-v8a"
```
Without `-d`, the AVD gets a tiny 320×640 screen. You can also create it in Android Studio → Device Manager.

### 3. Start everything (one terminal each)

```sh
# 1) Database (skip if it's already running: pg_isready says "accepting connections")
/opt/homebrew/opt/postgresql@16/bin/pg_ctl -D /opt/homebrew/var/postgresql@16 start

# 2) Backend → http://localhost:8000  (API docs: http://localhost:8000/docs)
cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# 3) Engine bridge → ws://localhost:8765/ws  (opens a `bridge>` prompt)
cd simulated_game_engine && uv run python -m simulated_game_engine

# 4) Emulator. Wait until the car home screen is shown.
~/Library/Android/sdk/emulator/emulator -avd drivescore_aaos34

# 5) Build and install the car app. Needed on first run and after code changes.
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
cd car_frontend && ./gradlew :app:installDebug
~/Library/Android/sdk/platform-tools/adb shell am start -n com.hackathon.drivescore/.app.MainActivity
```

The emulator reaches the host as `10.0.2.2`. The debug build already points at `http://10.0.2.2:8000` and `ws://10.0.2.2:8765/ws`, so no configuration is needed.

**3b. Backend with Docker instead of local Postgres:** `colima start`, then `cd backend && cp .env.example .env && docker compose up -d --build && docker compose exec api python -m scripts.seed`. Stop local Postgres first, or set `DB_PORT=55432`, because both use port 5432.

### 4. Do a drive

1. **In the car app:** choose **Create account**, or **Sign in** with `demo` / `demo-password-123`. Then choose a PIN. The PIN stays on the car and is never sent to the server.
2. **Start the drive.** In the `bridge>` terminal, `status` should now show a connected client and an active session.
3. **Act as the driver** in the `bridge>` terminal:
   - `scenario demo fast`: a scripted drive. A phone call escalates VISUAL → AUDIO, then eyes off road escalates AUDIO → HAPTIC → CRITICAL. The drive then ends by itself.
   - Or by hand: `start phone_call` … `stop phone_call`, `pulse eyes_off_road 5`, `auto on` (random behaviour).
   - `end` ends the drive. The car shows the score and the mistakes and uploads the drive to the backend.
4. Open **History**, **Stats** and **Leaderboard** in the app.

Other ways to run it:
- **No commands at all:** start the bridge with `uv run python -m simulated_game_engine --no-cli --scenario demo --fast`. Every drive then plays the demo automatically.
- **No bridge or backend at all:** in the app, Settings → *Debug settings* → **FakeEngine (in-app)**.
- **Simulate driving (the UI locks down while moving):** Debug settings → *Driving state override*, or emulator Extended controls → *Car data*.
- **The on-screen keyboard is disabled** on this AVD. Type with your Mac keyboard.

### 5. Stop everything

`quit` in the bridge, `Ctrl+C` in the backend, close the emulator, then:
```sh
/opt/homebrew/opt/postgresql@16/bin/pg_ctl -D /opt/homebrew/var/postgresql@16 stop
colima stop        # only if you used Docker
```

---

## Tests

```sh
cd backend && uv run pytest                          # 104 tests, needs Postgres (drive_test DB)
cd simulated_game_engine && uv run pytest            # 43 tests
cd car_frontend && ./gradlew :app:testDebugUnitTest  # 57 JVM unit tests (JAVA_HOME set)
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Unable to locate a Java Runtime` | `export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"` |
| `installDebug` fails with "No connected devices" | Start the emulator first and wait for it to boot. Check with `~/Library/Android/sdk/platform-tools/adb devices`. |
| Status bar shows "Engine not responding" instead of "Engine ✓" | The bridge isn't running, or not on port 8765. |
| Drives stay "pending sync" | The backend isn't running. Check `curl localhost:8000/health`. They upload automatically once it's back. |
| Backend: connection refused to Postgres | Start Postgres (step 3.1). Check that `.env` uses `@localhost:5432`, not `@db:5432`. |
| Port 8000, 8765 or 5432 already in use | `lsof -nP -iTCP:<port> -sTCP:LISTEN` shows which process holds it. |
| Demo shows only 2 alert types | Expected. The engine currently detects only `phone_call` and `eyes_off_road`. |
