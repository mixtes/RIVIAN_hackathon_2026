# Driving Score Platform: Backend

FastAPI + PostgreSQL API for users, cars, drives, personal statistics and leaderboards.
Specification: [`backend_specification.md`](backend_specification.md) (the source of truth).
Clients: the Android Automotive car app ([`../car_frontend/`](../car_frontend/)), and later phone and iOS apps.

## Quick start (Docker)

```bash
cd backend
cp .env.example .env                 # dev values; CAR_PROVISIONING_KEY matches the car app's debug build
docker compose up -d --build         # db + api; runs migrations on start
docker compose exec api python -m scripts.seed   # demo data (prints car tokens)
open http://localhost:8000/docs      # Swagger UI
```

- If port 5432 or 8000 is taken on the host: `DB_PORT=55432 API_PORT=8001 docker compose up -d`.
- The Android emulator reaches the API at `http://10.0.2.2:8000`. A real head unit needs the host's LAN IP or a public deployment.
- With Colima instead of Docker Desktop, run `colima start` first. If the `docker compose` plugin isn't registered, `docker-compose` (with a hyphen) works too.

## Local development (no Docker)

Needs Python 3.12 (`uv python install 3.12`), [uv](https://docs.astral.sh/uv/) and PostgreSQL 16 with a `drive` role.

```bash
cd backend
uv sync                                         # creates .venv with dev tools
cp .env.example .env && sed -i '' 's/@db:5432/@localhost:5432/' .env
# one-time DB setup (as a Postgres superuser):
#   CREATE ROLE drive LOGIN PASSWORD 'drive' CREATEDB;
#   CREATE DATABASE drive OWNER drive; CREATE DATABASE drive_test OWNER drive;
#   then in each DB: CREATE EXTENSION citext; CREATE EXTENSION pgcrypto;
uv run alembic upgrade head
uv run python -m scripts.seed
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The extensions need a superuser, so create them once per database. The migration also runs `CREATE EXTENSION IF NOT EXISTS`, which works when the `drive` role owns the DB on Postgres ≥ 13 (`pgcrypto` and `citext` are "trusted" extensions).

## Tests and quality

```bash
uv run pytest                                   # needs Postgres; DB from TEST_DATABASE_URL
                                                # (default postgresql+asyncpg://drive:drive@localhost:5432/drive_test)
uv run coverage run -m pytest && uv run coverage report
uv run ruff check . && uv run ruff format --check .
uv run mypy                                     # strict on app/
UPDATE_OPENAPI_SNAPSHOT=1 uv run pytest tests/test_openapi.py   # after an intentional API change
```

The test suite runs the real Alembic migration (downgrade + upgrade) once, then truncates every table before each test. A frozen clock (`2026-11-06T12:00:00Z`) makes windows, token expiry and the throttle deterministic. Argon2 uses cheap parameters in tests only.

Current state: 104 tests pass. Line coverage is about 96%, both on `services/` + `repositories/` and overall.

## curl walkthrough (what the car app does)

```bash
B=http://localhost:8000/api/v1
# 1. Car provisioning (once per install). The token is shown exactly once.
curl -s -X POST $B/cars/register -H 'X-Provisioning-Key: dev-provisioning-key' \
     -H 'Content-Type: application/json' -d '{"name":"Hackathon R1S","model":"R1S"}'
TOKEN=car_...   # from the response

# 2a. Create an account on the car (and link it), or
curl -s -X POST $B/cars/me/drivers/register -H "Authorization: Car $TOKEN" -H 'Content-Type: application/json' \
     -d '{"username":"mixa","password":"correct horse battery","display_name":"Mihajlo","region_code":"RS","avatar_color":"#E8603C"}'
# 2b. link an existing account (demo / demo-password-123 after seeding)
curl -s -X POST $B/cars/me/drivers -H "Authorization: Car $TOKEN" -H 'Content-Type: application/json' \
     -d '{"username":"demo","password":"demo-password-123"}'
DRIVER=<user id from the response>

# 3. Upload a drive (idempotent on id: 201 first time, 200 for an identical retry, 409 if different)
curl -s -X POST $B/drives -H "Authorization: Car $TOKEN" -H "X-Driver-Id: $DRIVER" -H 'Content-Type: application/json' -d '{
  "id":"6b1e6f0a-3c55-4d0b-9f9d-2f8c3f0d6a11",
  "started_at":"2026-11-06T08:01:12Z","ended_at":"2026-11-06T08:34:40Z",
  "score":87.5,"engine_version":"rivian-hackathon-engine/0.1.0",
  "mistakes":[{"type":"PHONE_CALL","count":1,"total_duration_s":8.0,"max_duration_s":8.0}],
  "events":[{"type":"PHONE_CALL","started_at":"2026-11-06T08:01:22Z","duration_s":8.0}]}'

# 4. Read back
curl -s "$B/drives?limit=20"            -H "Authorization: Car $TOKEN" -H "X-Driver-Id: $DRIVER"
curl -s "$B/stats/me?period=week"       -H "Authorization: Car $TOKEN" -H "X-Driver-Id: $DRIVER"
curl -s "$B/leaderboards?period=month&region=GLOBAL" -H "Authorization: Car $TOKEN" -H "X-Driver-Id: $DRIVER"

# Phone-app style (user JWT)
curl -s -X POST $B/auth/login -H 'Content-Type: application/json' -d '{"username":"demo","password":"demo-password-123"}'
curl -s $B/users/me -H "Authorization: Bearer <access_token>"
```

## Layout

```
app/
  main.py            app factory (create_app), middleware, routers; `app.main:app` is built lazily
  config.py          settings (env / .env)          clock.py   injectable clock (FrozenClock in tests)
  middleware.py      request id, 1 MB body limit, JSON access log, last-resort 500
  errors.py          AppError hierarchy + uniform error body
  security/          argon2id, JWT, opaque tokens, principals (User / Car / Driver-via-car / Provisioning), login throttle
  models/            SQLAlchemy models (DDL lives in alembic/versions)
  schemas/           Pydantic request/response DTOs (= the API contract)
  repositories/      all SQL, including the stats and leaderboard queries
  services/          domain rules: accounts, cars and links, drive upload/history, stats and leaderboards
  routers/           thin HTTP layer
  domain/            mistake registry, periods/windows, regions
  cache.py           in-process leaderboard TTL cache
scripts/seed.py      demo data
tests/               pytest (real Postgres); snapshots/openapi.json is the contract snapshot
```

## Production notes

- The image's default command runs migrations, then Gunicorn with Uvicorn workers (`WEB_CONCURRENCY`, default 2). `docker-compose.yml` runs a single uvicorn process instead.
- Set `ENV=prod`, a strong `JWT_SECRET` (≥ 32 bytes) and `CAR_PROVISIONING_KEY`. Docs are hidden in prod unless `DOCS_ENABLED=true`. HSTS is sent in prod. Terminate TLS at a reverse proxy.
- The leaderboard cache and the login throttle are **per process**. With several workers each has its own copy (see spec §9.4 for the Redis path).
