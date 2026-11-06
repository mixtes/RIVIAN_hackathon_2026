# Backend Specification: Driving Score Platform

> Status: v1.1 (2026-11-06), **implemented** in this folder (see `README.md`). §17 lists the decisions made during implementation. · Scope: Hackathon MVP, built to be close to production quality
> Companion document: [`../car_frontend/frontend_specification.md`](../car_frontend/frontend_specification.md) (Android Automotive car app)

---

## 1. Overview

The backend is the system of record for **users, cars, and drives**. It does three things:

1. Stores the result of every drive. The car app uploads each result after the on-board **Game engine** produces it.
2. Calculates **personal statistics** (averages, trends, mistake breakdowns) for each user.
3. Calculates **leaderboards**, ranked by **region** (the user's home region, or global) and by **time window** (last day, week, month, year, all time).

It serves two kinds of clients:

| Client | How it authenticates | Status |
|---|---|---|
| **Car app** (Android Automotive, Kotlin) | A **car token**, acting for a driver who is linked to that car | MVP |
| **Phone apps** (Android, iOS), web | User **JWT** (username + password login) | Later. The API is designed for them now. |

### 1.1 Key domain rules

- A **user** can drive **many cars**. A **car** can be driven by **many users**. The link between the two is `car_drivers`.
- A user is linked to a car by logging in **once** on that car with username and password (internet required).
- The **PIN never reaches the backend.** It exists only on the car, which uses it to unlock a driver profile offline (see the frontend spec).
- Each user has one **home region** (ISO 3166-1 alpha-2 country code). All of that user's drives count toward that region's leaderboard.
- A drive's **score is 0–100** and is calculated by the Game engine, not by the backend. The backend only stores scores and aggregates them.

---

## 2. Scope

### In scope (MVP)
- User registration, login, profile management, password change.
- Car registration (provisioning) and car tokens.
- Linking drivers to cars and unlinking them.
- Idempotent upload of drives, including mistake summaries and raw events.
- Drive history (paginated) and drive detail.
- Personal statistics per time window, with a time series for charts.
- Leaderboards per time window and region, with the requesting user's own position.
- OpenAPI docs, Docker Compose deployment, database migrations, seed data for demos, automated tests.

### Out of scope (see §15 Future work)
- Email verification, password reset by email, social login.
- Rate limiting and brute-force protection beyond basic measures.
- Pushing real-time alerts to the backend. Alerts stay on the car.
- Admin UI, analytics dashboards, multi-tenant fleets.

---

## 3. Technology Stack and Rationale

| Concern | Choice | Why |
|---|---|---|
| Language | **Python 3.12** | Fast to iterate. Clients are Kotlin and later Swift, so sharing a language with any one client buys little. |
| Web framework | **FastAPI** (≥ 0.115) | Async. Pydantic validation. OpenAPI is generated automatically, so the Kotlin and Swift clients can be code-generated from `/openapi.json`. |
| Server | **Uvicorn** (behind Gunicorn workers in production) | Standard ASGI. |
| Validation / DTOs | **Pydantic v2** | Strict schemas that also act as the API contract. |
| Database | **PostgreSQL 16** | Relational data (users ↔ cars ↔ drives). Window functions (`RANK() OVER`) and `FILTER` aggregates make the leaderboard and stats queries simple and fast. `CITEXT` gives case-insensitive usernames. `JSONB` stores the raw engine payload. |
| ORM / DB access | **SQLAlchemy 2.0 (async)** + **asyncpg** | Typed models, plus raw SQL where the aggregation queries need it. |
| Migrations | **Alembic** | Versioned schema. |
| Password hashing | **argon2-cffi** (Argon2id) | Current best practice. |
| JWT | **PyJWT** (HS256) | Simple. Can move to RS256 later. |
| Config | **pydantic-settings** | Reads environment variables and `.env`. |
| Testing | **pytest**, **pytest-asyncio**, **httpx** `AsyncClient`, a Postgres service in Compose (or testcontainers) | Tests run against a real database because the SQL is Postgres-specific. |
| Lint / format | **ruff**, **mypy** (strict on `app/`) | |
| Packaging | **uv** (or Poetry) with `pyproject.toml` | |
| Deployment | **Docker** + **docker-compose** (`api`, `db`) | One command brings up the whole stack for the hackathon. |

---

## 4. Architecture

### 4.1 Layers

```
HTTP (FastAPI routers)  →  Services (business logic)  →  Repositories (SQLAlchemy / SQL)  →  PostgreSQL
        ↑ Pydantic schemas (request/response DTOs)
        ↑ Dependencies: auth principal resolution, DB session, settings
```

- **Routers** are thin. They parse and validate input, resolve the authenticated principal, call a service, and map the result to a response schema.
- **Services** hold the domain rules: drive validation, linking, ranking parameters.
- **Repositories** hold all SQL, including the stats and leaderboard queries.
- There is no global state except a small in-process **TTL cache** for leaderboards (§9.4).

### 4.2 Project layout

```
backend/
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── alembic.ini
├── alembic/versions/…
├── app/
│   ├── main.py                 # FastAPI app factory, routers, exception handlers, CORS
│   ├── config.py               # Settings (pydantic-settings)
│   ├── db.py                   # async engine, session dependency
│   ├── security/
│   │   ├── passwords.py        # argon2id hash/verify
│   │   ├── jwt.py              # access-token encode/decode
│   │   ├── tokens.py           # opaque token generation + sha256 hashing (refresh, car)
│   │   └── principals.py       # FastAPI deps: get_user_principal, get_car_principal, get_driver_principal
│   ├── models/                 # SQLAlchemy ORM models
│   ├── schemas/                # Pydantic DTOs (auth, users, cars, drives, stats, leaderboard, errors)
│   ├── repositories/
│   ├── services/
│   ├── routers/
│   │   ├── auth.py  users.py  cars.py  drives.py  stats.py  leaderboards.py  meta.py
│   ├── domain/
│   │   ├── mistakes.py         # MistakeType registry (known types, labels)
│   │   └── periods.py          # Period enum, window + bucket definitions, thresholds
│   └── errors.py               # AppError hierarchy + handlers
├── scripts/
│   └── seed.py                 # demo users, cars, drives across regions/periods
└── tests/
```

---

## 5. Domain Model

### 5.1 Entities

| Entity | Description |
|---|---|
| **User** | A person who drives. Has a unique username, a password, a display name, and a home region. |
| **Car** | A physical vehicle (one installation of the car app). Has an ID and a secret car token. VIN is optional. |
| **CarDriver** | Link meaning "user U has logged in on car C". It authorizes car C to act for U. Can be revoked. |
| **Drive** | One driving session's result: start and end time (and therefore duration), score, and mistakes. Belongs to one user and one car. There is **no distance data**. The engine doesn't provide it, so all weighting uses driving time. |
| **DriveMistake** | Per-drive summary for one mistake type: count, total duration, longest single occurrence. |
| **DriveEvent** | Optional raw occurrences inside a drive (type, start time, duration), used for timelines. |
| **RefreshToken** | Rotating refresh token for user JWT sessions (phone and iOS apps). |

### 5.2 Mistake types

Mistake types are **strings**, so the Game engine can add new types without a database migration. The backend accepts any value matching `^[A-Z][A-Z0-9_]{1,39}$`. It keeps a registry of **known** types with labels and units for display:

| `type` | Meaning | `count` | `total_duration_s` / `max_duration_s` |
|---|---|---|---|
| `EYES_OFF_ROAD` | Gaze away from the road beyond the engine's threshold | ✔ | ✔ |
| `PHONE_USE` | Looking at or handling a phone | ✔ | ✔ |
| `EYES_CLOSED` | Eyes closed beyond the threshold (microsleep) | ✔ | ✔ |
| `YAWN` | Yawn detected | ✔ | optional (usually null) |
| `HANDS_OFF_WHEEL` | No hands on the steering wheel | ✔ | ✔ |
| `PHONE_CALL` | Talking on a hand-held phone | ✔ | ✔ |
| `EATING_DRINKING` | Eating or drinking while driving | ✔ | ✔ |
| `SMOKING` | Smoking while driving | ✔ | ✔ |

`GET /api/v1/meta/mistake-types` returns this registry, so clients can show labels and ignore types they don't know (§8.9).

### 5.3 Regions
- Stored as `CHAR(2)`, an uppercase ISO 3166-1 alpha-2 code (`RS`, `US`, `DE`…). Validated with `pycountry`.
- The special value `GLOBAL` is accepted **only** as a leaderboard query parameter and means "all regions".
- Leaderboards use the user's **current** home region. If a user changes region, all their past drives move with them.

### 5.4 Time windows (periods)

All windows are **rolling** and computed in UTC from the request time. A drive belongs to a window by its **`ended_at`**.

| `period` | Window | Stats time-series bucket | Default leaderboard minimum driving time (`min_minutes`) |
|---|---|---|---|
| `day` | last 24 h | 1 hour | 10 min |
| `week` | last 7 days | 1 day | 45 min |
| `month` | last 30 days | 1 day | 120 min |
| `year` | last 365 days | 1 month (calendar, UTC) | 480 min |
| `all` | all time | 1 month (calendar, UTC) | 480 min |

All thresholds can be changed through settings (§11).

---

## 6. Data Model (PostgreSQL)

```sql
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

-- USERS ---------------------------------------------------------------
CREATE TABLE users (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username             CITEXT      NOT NULL UNIQUE,      -- 3–32 chars, [a-z0-9_.], case-insensitive
    email                CITEXT      UNIQUE,               -- optional for MVP (needed later for reset)
    password_hash        TEXT        NOT NULL,             -- argon2id
    display_name         VARCHAR(40) NOT NULL,
    region_code          CHAR(2)     NOT NULL,             -- ISO 3166-1 alpha-2
    avatar_color         CHAR(7)     NOT NULL DEFAULT '#4F7DF3', -- hex, used for car profile icon
    leaderboard_visible  BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at        TIMESTAMPTZ
);
CREATE INDEX ix_users_region ON users (region_code);

-- CARS ----------------------------------------------------------------
CREATE TABLE cars (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vin             VARCHAR(17) UNIQUE,                    -- optional
    name            VARCHAR(60) NOT NULL,                  -- e.g. "Family R1S"
    model           VARCHAR(60),
    token_hash      CHAR(64)    NOT NULL UNIQUE,           -- sha256(car_token) hex
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ
);

-- CAR ↔ DRIVER LINK ------------------------------------------------------
CREATE TABLE car_drivers (
    car_id          UUID NOT NULL REFERENCES cars(id)  ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    linked_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at    TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ,                           -- soft revoke (by car or, later, phone app)
    PRIMARY KEY (car_id, user_id)
);
CREATE INDEX ix_car_drivers_user ON car_drivers (user_id);

-- REFRESH TOKENS (user JWT sessions: phone/iOS/web) ------------------------
CREATE TABLE refresh_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      CHAR(64)    NOT NULL UNIQUE,
    family_id       UUID        NOT NULL,                  -- rotation family (reuse detection)
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_agent      TEXT
);
CREATE INDEX ix_refresh_tokens_user ON refresh_tokens (user_id);

-- DRIVES ----------------------------------------------------------------
CREATE TABLE drives (
    id               UUID PRIMARY KEY,                      -- client-generated session_id (idempotency key)
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    car_id           UUID NOT NULL REFERENCES cars(id)  ON DELETE RESTRICT,
    started_at       TIMESTAMPTZ  NOT NULL,
    ended_at         TIMESTAMPTZ  NOT NULL,
    duration_s       INTEGER      NOT NULL CHECK (duration_s >= 0),
    distance_km      NUMERIC(8,3) CHECK (distance_km >= 0),   -- reserved: always NULL today (engine has no distance); kept for future
    score            NUMERIC(5,2) NOT NULL CHECK (score BETWEEN 0 AND 100),
    engine_version   VARCHAR(40),
    raw_payload      JSONB        NOT NULL,                  -- full Game-engine result, for forward compatibility
    received_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK (ended_at >= started_at)
);
CREATE INDEX ix_drives_user_ended ON drives (user_id, ended_at DESC);
CREATE INDEX ix_drives_ended      ON drives (ended_at);
CREATE INDEX ix_drives_car        ON drives (car_id, ended_at DESC);

CREATE TABLE drive_mistakes (
    drive_id          UUID NOT NULL REFERENCES drives(id) ON DELETE CASCADE,
    type              VARCHAR(40) NOT NULL,
    count             INTEGER     NOT NULL CHECK (count >= 0),
    total_duration_s  NUMERIC(10,2) CHECK (total_duration_s >= 0),
    max_duration_s    NUMERIC(10,2) CHECK (max_duration_s >= 0),
    PRIMARY KEY (drive_id, type)
);
CREATE INDEX ix_drive_mistakes_type ON drive_mistakes (type);

CREATE TABLE drive_events (
    id           BIGSERIAL PRIMARY KEY,
    drive_id     UUID NOT NULL REFERENCES drives(id) ON DELETE CASCADE,
    type         VARCHAR(40) NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL,
    duration_s   NUMERIC(10,2)
);
CREATE INDEX ix_drive_events_drive ON drive_events (drive_id, started_at);
```

Notes:
- `drives.id` is the **session ID the car generates** when a session starts. Uploads are therefore naturally idempotent (§8.6).
- `raw_payload` keeps the exact engine output, so new engine fields aren't lost before the schema supports them.
- All timestamps are `TIMESTAMPTZ`, and the API exchanges them as ISO-8601 with an offset (UTC `Z` recommended).

---

## 7. Authentication and Authorization

### 7.1 Principals

| Principal | Credential | Used by | Can do |
|---|---|---|---|
| **User** | `Authorization: Bearer <access JWT>` | Phone, iOS, web, Swagger | Anything on their own account and data, plus reading leaderboards |
| **Car** | `Authorization: Car <car_token>` | Car app | Car-level operations: get car info, link a driver by password, list linked drivers |
| **Driver-via-car** | `Authorization: Car <car_token>` **+** `X-Driver-Id: <user_uuid>` | Car app | Act as that user (upload drives, read history, stats, leaderboard). Allowed **only if** an active `car_drivers` link exists. |
| **Provisioning** | `X-Provisioning-Key: <secret>` | Car app, first launch | Register a new car |

Why the car acts for the driver instead of holding user tokens:
- The car is often **offline for a long time**, and the PIN is a local, offline unlock. If the car held per-user JWT and refresh tokens, they would expire while the car sits offline, and queued drives could not be uploaded.
- Logging in once with a password **proves consent** and creates the `car_drivers` link. After that the car token is enough to act for that driver.
- **Revocation** stays easy. Removing the driver on the car, or later in the phone app, sets `car_drivers.revoked_at`, and the car immediately loses access to that user.
- Trust model: whoever controls the car app can act for that car's linked drivers. This is the same trust level as storing user tokens on the car. The PIN protects profiles from **other people in the car**, not from someone who has compromised the device.

### 7.2 Tokens

| Token | Format | Lifetime | Storage |
|---|---|---|---|
| Access JWT (users) | HS256. Claims: `sub` (user id), `iat`, `exp`, `jti`, `typ="access"` | 15 min (`ACCESS_TOKEN_TTL_MIN`) | Client memory |
| Refresh token (users) | Opaque, 32 random bytes, base64url | 30 days (`REFRESH_TOKEN_TTL_DAYS`). Rotated on every use. | DB stores only the SHA-256 hash |
| Car token | Opaque, 32 random bytes, base64url, prefixed `car_` | No expiry. Revocable. | DB stores SHA-256 hash. Car stores it in Keystore-encrypted storage. |

**Refresh rotation and reuse detection:** every `/auth/refresh` call revokes the presented token and issues a new one in the same `family_id`. If a token that was **already revoked** is presented again, the whole family is revoked and the user must log in again.

### 7.3 Passwords
- Argon2id with the argon2-cffi defaults (time_cost=3, memory_cost=64 MiB, parallelism=4), rehashed on login if the parameters change.
- Policy: 8–128 characters, and not equal to the username. There are no composition rules (follows NIST 800-63B).

### 7.4 Username rules
- 3–32 characters, `^[a-z0-9_.]+$` after lowercasing, unique and case-insensitive (`CITEXT`).
- Reserved names: `admin`, `root`, `system`, `me`, `global`.

### 7.5 Car provisioning
- On first launch the car calls `POST /cars/register` with `X-Provisioning-Key` set to `CAR_PROVISIONING_KEY` (a shared secret, configured in the car app build for the hackathon).
- The response contains `car_id` and `car_token` **exactly once**. If the car loses the token (for example after a reinstall), it registers again as a new car and its drivers log in again.

---

## 8. REST API

### 8.1 Conventions
- Base path: **`/api/v1`**. JSON only (`Content-Type: application/json; charset=utf-8`).
- Field names in **snake_case**. Clients map them with serializer annotations.
- Timestamps in ISO-8601 (`2026-11-06T12:34:56Z`). IDs are UUIDv4 strings.
- Numbers: `score` is returned as a float with 2 decimal places. Durations are integers in seconds (`*_s`). Mistake durations are floats.
- Pagination: **cursor-based** for drive history (`?limit=20&cursor=<opaque>`, response `next_cursor`). **Offset-based** for leaderboards (`?limit=50&offset=0`).
- OpenAPI: `GET /openapi.json`. Swagger UI is at `/docs` and ReDoc at `/redoc`.
- CORS: configurable allow-list (only needed for future web clients).

### 8.2 Error format

Every error response has this shape:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Human-readable summary",
    "details": [ { "field": "score", "issue": "must be between 0 and 100" } ],
    "request_id": "9f1c…"
  }
}
```

| HTTP | `code` examples |
|---|---|
| 400 | `BAD_REQUEST` |
| 401 | `UNAUTHENTICATED`, `INVALID_CREDENTIALS`, `TOKEN_EXPIRED`, `TOKEN_REUSED` |
| 403 | `DRIVER_NOT_LINKED`, `CAR_REVOKED`, `FORBIDDEN` (e.g. a car token on a user-only endpoint, or the reverse) |
| 404 | `NOT_FOUND` |
| 409 | `USERNAME_TAKEN`, `EMAIL_TAKEN`, `DRIVE_CONFLICT`, `CAR_DRIVER_LIMIT`, `VIN_TAKEN` |
| 413 | `PAYLOAD_TOO_LARGE` (body > 1 MB) |
| 422 | `VALIDATION_ERROR` (Pydantic errors mapped to `details`) |
| 429 | `TOO_MANY_ATTEMPTS` (with a `Retry-After` header) |
| 500 | `INTERNAL_ERROR` |

Every response carries an `X-Request-Id` header. If the client sent one, the same value is echoed back.

### 8.3 Endpoint summary

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | none | Liveness. Includes a DB check. |
| GET | `/api/v1/meta/mistake-types` | none | Registry of known mistake types |
| POST | `/api/v1/auth/register` | none | Create a user |
| POST | `/api/v1/auth/login` | none | Username + password → access + refresh tokens |
| POST | `/api/v1/auth/refresh` | none (refresh token in body) | Rotate tokens |
| POST | `/api/v1/auth/logout` | User | Revoke the refresh token family |
| GET | `/api/v1/users/me` | User **or** Driver-via-car | Own profile |
| PATCH | `/api/v1/users/me` | User | Update display name, region, avatar color, leaderboard visibility |
| PUT | `/api/v1/users/me/password` | User | Change password |
| POST | `/api/v1/cars/register` | Provisioning | Register a car → `car_id`, `car_token` |
| GET | `/api/v1/cars/me` | Car | Car info and active linked drivers |
| PATCH | `/api/v1/cars/me` | Car | Update name, model, VIN |
| POST | `/api/v1/cars/me/drivers` | Car | **Link a driver**: username + password → user profile |
| POST | `/api/v1/cars/me/drivers/register` | Car | **Create an account on the car** and link it in one step |
| GET | `/api/v1/meta/regions` | none | List of selectable regions (ISO code + name), for the sign-up form |
| DELETE | `/api/v1/cars/me/drivers/{user_id}` | Car | Unlink a driver (soft revoke) |
| GET | `/api/v1/users/me/cars` | User | Cars linked to me (for the future phone app) |
| DELETE | `/api/v1/users/me/cars/{car_id}` | User | Revoke a car's access to me (for the future phone app) |
| POST | `/api/v1/drives` | Driver-via-car | Upload a drive (idempotent) |
| GET | `/api/v1/drives` | User **or** Driver-via-car | My drive history (all cars), cursor-paginated |
| GET | `/api/v1/drives/{drive_id}` | User **or** Driver-via-car | Drive detail, including mistakes and events |
| GET | `/api/v1/stats/me` | User **or** Driver-via-car | Personal stats for a period |
| GET | `/api/v1/leaderboards` | User **or** Driver-via-car | Leaderboard for a period and region, plus my position |

### 8.4 Auth endpoints

**`POST /api/v1/auth/register`**
```json
// request
{ "username": "mixa", "password": "correct horse battery", "display_name": "Mihajlo",
  "region_code": "RS", "email": null, "avatar_color": "#E8603C" }
// 201 response
{ "user": { /* UserProfile */ }, "tokens": { /* TokenPair */ } }
```

**`POST /api/v1/auth/login`** `{ "username": "mixa", "password": "…" }` → `200 { "user": UserProfile, "tokens": TokenPair }`

**`POST /api/v1/auth/refresh`** `{ "refresh_token": "…" }` → `200 TokenPair`

**`POST /api/v1/auth/logout`** (User auth) `{ "refresh_token": "…" }` → `204`. Revokes that token's family if it belongs to the caller. Unknown tokens are ignored (still `204`).

**`PUT /api/v1/users/me/password`** `{ "current_password": "…", "new_password": "…" }` → `204`. A wrong current password → `401 INVALID_CREDENTIALS`. On success, **all** of the user's refresh tokens are revoked.

**`PATCH /api/v1/users/me`** accepts any subset of `display_name`, `region_code`, `avatar_color`, `leaderboard_visible`. Unknown fields or `null` values → `422`. Any change clears the leaderboard cache.

**`GET /api/v1/users/me/cars`** → `[ { "car_id", "name", "model", "linked_at", "last_used_at" } ]` (active links only).

```json
// TokenPair
{ "access_token": "eyJ…", "token_type": "bearer", "expires_in": 900, "refresh_token": "…" }

// UserProfile
{ "id": "uuid", "username": "mixa", "display_name": "Mihajlo", "region_code": "RS",
  "avatar_color": "#E8603C", "leaderboard_visible": true, "created_at": "…" }
```

### 8.5 Car endpoints

**`POST /api/v1/cars/register`** (header `X-Provisioning-Key`)
```json
// request
{ "name": "Hackathon R1S", "model": "R1S", "vin": null }
// 201 response
{ "car_id": "uuid", "car_token": "car_9Jx…", "name": "Hackathon R1S" }
```

**`POST /api/v1/cars/me/drivers`** (header `Authorization: Car …`). The car's "Add driver" flow calls this.
```json
// request
{ "username": "mixa", "password": "…" }
// 200 response (201 if newly linked; 200 if re-linking or already linked)
{ "user": { /* UserProfile */ }, "linked_at": "…" }
```
- Wrong credentials → `401 INVALID_CREDENTIALS`. The message is the same whether the username or the password is wrong.
- A previously revoked link is reactivated (`revoked_at = NULL`).
- The service enforces a **maximum of 8 active drivers per car** → `409 CAR_DRIVER_LIMIT`.

**`POST /api/v1/cars/me/drivers/register`** (header `Authorization: Car …`). The car's "Create account" flow calls this.
```json
// request: same body and validation as POST /auth/register
{ "username": "mixa", "password": "correct horse battery", "display_name": "Mihajlo",
  "region_code": "RS", "email": null, "avatar_color": "#E8603C" }
// 201 response
{ "user": { /* UserProfile */ }, "linked_at": "…" }
```
- Creates the user **and** the `car_drivers` link in **one transaction**. No JWT tokens are returned, because the car acts through its car token.
- Errors: `409 USERNAME_TAKEN` / `EMAIL_TAKEN`, `409 CAR_DRIVER_LIMIT` (checked **before** the user is created), `422 VALIDATION_ERROR`.
- The created account is a normal account. The same username and password work later in the phone and iOS apps via `/auth/login`.

**`DELETE /api/v1/cars/me/drivers/{user_id}`** → `204`. This sets `revoked_at`. Drives already uploaded are kept. It is idempotent: an already-revoked link also returns `204`, so queued offline unlinks can be retried safely. A user who was never linked → `404`.

**`PATCH /api/v1/cars/me`** accepts `name`, `model`, `vin` and returns the same body as `GET /cars/me`. A VIN used by another car → `409 VIN_TAKEN`. VINs are upper-cased, 11–17 characters, `A-Z` (no I, O, Q) and `0-9`.

**`GET /api/v1/cars/me`** → `{ "car_id", "name", "model", "vin", "drivers": [UserProfile + "linked_at", "last_used_at"] }`

### 8.6 Drive upload

**`POST /api/v1/drives`** (headers `Authorization: Car …`, `X-Driver-Id: <uuid>`)

The body is the Game engine's `drive.result` payload (frontend spec §8) **plus** `id`, which is the session ID:

```json
{
  "id": "6b1e6f0a-3c55-4d0b-9f9d-2f8c3f0d6a11",
  "started_at": "2026-11-06T08:01:12Z",
  "ended_at":   "2026-11-06T08:34:40Z",
  "distance_km": null,          // optional, reserved; engine currently sends null or omits it
  "score": 87.5,
  "engine_version": "game-engine/1.3.0",
  "mistakes": [
    { "type": "EYES_OFF_ROAD",   "count": 4, "total_duration_s": 9.6,  "max_duration_s": 3.1 },
    { "type": "YAWN",            "count": 2, "total_duration_s": null, "max_duration_s": null },
    { "type": "HANDS_OFF_WHEEL", "count": 1, "total_duration_s": 4.0,  "max_duration_s": 4.0 }
  ],
  "events": [
    { "type": "EYES_OFF_ROAD", "started_at": "2026-11-06T08:05:03Z", "duration_s": 3.1 }
  ],
  "extra": { }     // optional free-form engine fields, stored in raw_payload only
}
```

Behavior:
- `user_id` comes from `X-Driver-Id` and `car_id` comes from the car token. The body is never used as the source for them.
- The car forwards the engine's `drive.result` verbatim, so the body may also contain `session_id` and `user_id`. They are accepted; if present, `session_id` must equal `id` and `user_id` must equal `X-Driver-Id` (else `422`). Unknown top-level fields are accepted and kept in `raw_payload`.
- `raw_payload` is the **request body exactly as received**. "Identical payload" below means JSON-equal to the stored `raw_payload` (key order and whitespace don't matter).
- **Idempotent on `id`:**
  - If no drive with this `id` exists, insert it → `201 Created` with a `Drive` body.
  - If a drive with this `id` exists with the same user, car, and an identical payload hash, return it → `200 OK`. The client treats this as success.
  - If a drive with this `id` exists with a different user, car, or payload → `409 DRIVE_CONFLICT`. This is a permanent failure and the client must not retry.
- `duration_s` is computed on the server as `ended_at - started_at`.
- If the same `type` appears more than once in `mistakes` → `422`.
- `car_drivers.last_used_at` and `cars.last_seen_at` are updated.
- Invalidates the leaderboard cache entries for the user's region and `GLOBAL` (§9.4).

**Validation rules** (each failure → `422` with field details):

| Field | Rule |
|---|---|
| `id` | UUID |
| `started_at`, `ended_at` | `ended_at ≥ started_at`. `ended_at ≤ now + 10 min` (allows for car clock skew). Duration ≤ 24 h. `started_at ≥ 2024-01-01`. |
| `score` | 0 ≤ score ≤ 100 |
| `distance_km` | optional. null, or 0 ≤ distance ≤ 2000. Stored, but not used in any calculation. |
| `mistakes[].type` | `^[A-Z][A-Z0-9_]{1,39}$`. Unknown types are accepted and stored. |
| `mistakes[].count` | integer 0–100000 |
| `mistakes[].*_duration_s` | null, or 0 ≤ value ≤ drive duration (+1 s slack for rounding) |
| `events` | ≤ 5000 items. Each `started_at` is within [started_at, ended_at + 1 min]. |
| Body size | ≤ 1 MB |

### 8.7 Drive history

**`GET /api/v1/drives?limit=20&cursor=…&car_id=…`**. `car_id` is an optional filter.
```json
{
  "items": [
    { "id": "uuid", "car": { "id": "uuid", "name": "Hackathon R1S" },
      "started_at": "…", "ended_at": "…", "duration_s": 2008,
      "score": 87.5,
      "mistakes_summary": { "total_count": 7, "types": ["EYES_OFF_ROAD", "YAWN", "HANDS_OFF_WHEEL"] } }
  ],
  "next_cursor": "eyJlbmRlZF9hdCI6…" // null when no more
}
```
The list is ordered by `ended_at DESC, id DESC`. The cursor is a base64url encoding of `(ended_at, id)` (keyset pagination).

**`GET /api/v1/drives/{id}`** → the full `Drive`: list fields plus `mistakes[]`, `events[]`, `engine_version`, `received_at`. Requesting another user's drive returns `404`.

### 8.8 Personal statistics

**`GET /api/v1/stats/me?period=week`**
```json
{
  "period": "week",
  "window_start": "2026-10-30T12:00:00Z",
  "window_end":   "2026-11-06T12:00:00Z",
  "drive_count": 9,
  "total_duration_s": 31440,
  "avg_score": 84.1,               // simple mean
  "weighted_score": 86.3,          // driving-time-weighted (the ranking metric)
  "best_score": 97.0,
  "worst_score": 61.5,
  "mistakes": [
    { "type": "EYES_OFF_ROAD", "count": 31, "total_duration_s": 74.2, "per_hour": 3.55 },
    { "type": "YAWN",          "count": 12, "total_duration_s": null, "per_hour": 1.37 }
  ],
  "series": [
    { "bucket_start": "2026-10-31T00:00:00Z", "drive_count": 2, "duration_s": 4210,
      "weighted_score": 88.0, "mistake_count": 5 }
  ],
  "rank": {
    "region": { "code": "RS", "rank": 14, "total_ranked": 233, "qualified": true },
    "global": { "rank": 412, "total_ranked": 5120, "qualified": true }
  }
}
```
- `per_hour` = count ÷ (total_duration_s / 3600). It is `null` when the total driving time is 0.
- `series` buckets follow §5.4. Empty buckets are **included** with zeros and nulls, so charts get a continuous x-axis. `generate_series` produces the buckets.
- `rank` uses the same computation as the leaderboard (§9.2). If the user has not reached the period's minimum driving time, `qualified=false` and `rank=null`. A user with `leaderboard_visible=false` also gets `rank=null`.
- `mistakes` lists only types with a total count > 0, sorted by count (descending). The engine reports zero-count types too; they are stored but not listed here.
- The window is `[window_start, window_end)` on `ended_at`. For `period=all`, `window_start` is the month of the user's first drive (see §9.3).

### 8.9 Leaderboard

**`GET /api/v1/leaderboards?period=month&region=RS&limit=50&offset=0`**. Set `region=GLOBAL` for everyone. `region` is case-insensitive and **defaults to the caller's home region**. `period` defaults to `week`. `limit` is 1–200 (default 50).
```json
{
  "period": "month",
  "region": "RS",
  "window_start": "…", "window_end": "…",
  "min_minutes": 120,
  "total_ranked": 233,
  "entries": [
    { "rank": 1, "user_id": "uuid", "username": "ana", "display_name": "Ana",
      "avatar_color": "#2BB673", "weighted_score": 96.42, "drive_count": 31, "driving_time_s": 61200 }
  ],
  "me": {
    "rank": 14, "weighted_score": 88.10, "drive_count": 12, "driving_time_s": 19800,
    "qualified": true, "minutes_needed": 0
  },
  "generated_at": "…"
}
```
- `me.rank` is `null` when the user is not qualified, has `leaderboard_visible=false`, or isn't in the requested region. When not qualified, `minutes_needed` shows how many more minutes the user must drive (rounded up). `me.weighted_score` is the user's own weighted score in the window, ranked or not (`null` with no driving time).
- `window_start` is `null` for `period=all`.
- Leaderboard entries expose only `username`, `display_name`, and `avatar_color`. No car or drive data.

**`GET /api/v1/meta/mistake-types`**
```json
[ { "type": "EYES_OFF_ROAD", "label": "Eyes off road", "has_duration": true }, … ]
```

**`GET /api/v1/meta/regions`**
```json
[ { "code": "RS", "name": "Serbia" }, { "code": "US", "name": "United States" }, … ]
```
All ISO 3166-1 countries from `pycountry`, sorted by name. The car's sign-up form uses this list, and the car app also bundles a static copy for when the call fails.

---

## 9. Statistics and Ranking Logic

### 9.1 Metric

The **weighted score** for a user in a window is:

```
weighted_score = Σ(score_i × w_i) / Σ(w_i)      where w_i = duration_s_i  (driving time)
```

- **Weighting is by driving time**, because the Game engine has no distance data. A 60-minute drive counts six times as much as a 10-minute drive.
- Drives with `duration_s = 0` have zero weight. They still show in history and `avg_score`.
- **Qualification:** Σ(duration_s) ≥ the period's `min_minutes × 60` (§5.4). This stops a single short perfect drive from topping the board.
- **Ordering:** `weighted_score DESC`, then total driving time `DESC` (more driving wins ties), then `user_id` (deterministic). The rank function is `RANK()`, so ties share a rank.
- If the engine provides distance in the future, distance weighting can be added as an alternative (see Future work). `distance_km` is already stored when sent.

### 9.2 Leaderboard query (reference)

```sql
WITH agg AS (
    SELECT d.user_id,
           SUM(d.score * d.duration_s) / NULLIF(SUM(d.duration_s), 0) AS weighted_score,
           SUM(d.duration_s) AS driving_time_s,
           COUNT(*)          AS drive_count
    FROM drives d
    JOIN users u ON u.id = d.user_id
    WHERE (:window_start IS NULL OR d.ended_at >= :window_start)
      AND d.ended_at < :window_end
      AND u.leaderboard_visible
      AND (:region = 'GLOBAL' OR u.region_code = :region)
    GROUP BY d.user_id
    HAVING SUM(d.duration_s) >= :min_minutes * 60
),
ranked AS (
    SELECT agg.*,
           RANK() OVER (ORDER BY weighted_score DESC, driving_time_s DESC) AS rank,
           COUNT(*) OVER () AS total_ranked
    FROM agg
)
SELECT r.*, u.username, u.display_name, u.avatar_color
FROM ranked r JOIN users u ON u.id = r.user_id
ORDER BY rank, r.user_id
LIMIT :limit OFFSET :offset;
```
The `me` row is computed by the same CTE filtered to `user_id = :me`. When the user is not qualified, a separate aggregation without `HAVING` fills `minutes_needed`.

### 9.3 Personal stats query
- Totals: one aggregate over `drives` for user and window, using `FILTER` clauses.
- Mistakes: `SUM(count)`, `SUM(total_duration_s)` from `drive_mistakes` joined to the window's drives, grouped by `type`.
- Series: `generate_series(window_start_truncated, window_end, bucket_interval)` LEFT JOINed with drives bucketed by `date_trunc` (hour, day, or month).
- `all` period: `window_start` is the user's first drive (truncated to the month), or `now` if there are no drives.

### 9.4 Performance and caching
- Expected hackathon scale is fewer than 10k users and 1M drives. The on-the-fly queries above run in tens of milliseconds with the indexes in §6.
- **Leaderboard cache:** an in-process TTL cache keyed by `(period, region, limit, offset)` with a 60 s TTL (`LEADERBOARD_CACHE_TTL_S`). The `me` row is never cached. A new drive upload invalidates its region and `GLOBAL`.
- **Scale-out path (future):** materialized views per period, refreshed every few minutes (`REFRESH MATERIALIZED VIEW CONCURRENTLY`), or a Redis-backed cache shared across workers.

---

## 10. Cross-Cutting Concerns

| Concern | Approach |
|---|---|
| Logging | Structured JSON logs (stdlib `logging` + `python-json-logger`), one line per request: method, path, status, latency, request_id, principal type. **Never** log passwords, tokens, or `X-Provisioning-Key`. |
| Request ID | Middleware sets `X-Request-Id` (generated if absent). Included in error bodies. |
| Time | All server logic is in UTC. `now()` comes from an injectable clock so tests are deterministic. |
| Transactions | Drive upload (drive + mistakes + events) runs in **one transaction**. |
| Security headers | HTTPS terminated at a reverse proxy or the platform. `Strict-Transport-Security` set in production. |
| Input limits | 1 MB body limit. Pydantic strict types. Bounded list sizes. |
| Brute force (basic) | Per-username in-memory counter for failed logins on both `/auth/login` and `/cars/me/drivers`: 10 failures in 15 min → `429 TOO_MANY_ATTEMPTS` for 15 min. Proper rate limiting is future work. |
| Privacy | Leaderboards show only display data. Users can opt out with `leaderboard_visible=false`. Raw events are visible only to the owner. |

---

## 11. Configuration (environment variables)

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://drive:drive@db:5432/drive` | |
| `JWT_SECRET` | *(required)* | ≥ 32 random bytes |
| `ACCESS_TOKEN_TTL_MIN` | `15` | |
| `REFRESH_TOKEN_TTL_DAYS` | `30` | |
| `CAR_PROVISIONING_KEY` | *(required)* | Shared secret for `POST /cars/register` |
| `MAX_DRIVERS_PER_CAR` | `8` | |
| `MIN_MINUTES_DAY` / `_WEEK` / `_MONTH` / `_YEAR` / `_ALL` | `10 / 45 / 120 / 480 / 480` | Leaderboard qualification (minimum driving time per period) |
| `LEADERBOARD_CACHE_TTL_S` | `60` | |
| `CORS_ORIGINS` | `` | Comma-separated |
| `LOG_LEVEL` | `INFO` | |
| `ENV` | `dev` | `dev` / `test` / `prod`. `prod` hides `/docs`, `/redoc` and `/openapi.json` unless `DOCS_ENABLED=true`, and sends HSTS. |
| `DOCS_ENABLED` | `false` | Show the API docs in `prod` |
| `ARGON2_TIME_COST` / `ARGON2_MEMORY_COST_KIB` / `ARGON2_PARALLELISM` | `3 / 65536 / 4` | Password hashing parameters (tests lower them) |
| `LOGIN_MAX_FAILURES` / `LOGIN_FAILURE_WINDOW_MIN` / `LOGIN_LOCKOUT_MIN` | `10 / 15 / 15` | Basic brute-force protection (§10) |
| `MAX_BODY_BYTES` | `1048576` | Request body limit |

---

## 12. Deployment

### 12.1 docker-compose (dev and hackathon)
```yaml
services:
  db:
    image: postgres:16-alpine
    environment: { POSTGRES_USER: drive, POSTGRES_PASSWORD: drive, POSTGRES_DB: drive }
    ports: ["${DB_PORT:-5432}:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U drive"], interval: 5s, retries: 10 }
  api:
    build: .
    env_file: .env
    environment: { DATABASE_URL: postgresql+asyncpg://drive:drive@db:5432/drive }
    depends_on: { db: { condition: service_healthy } }
    ports: ["${API_PORT:-8000}:8000"]
    command: sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"
volumes: { pgdata: {} }
```
- The image's own default command (outside compose) runs migrations, then Gunicorn with `uvicorn_worker.UvicornWorker` workers (`WEB_CONCURRENCY`, default 2).
- The Android emulator reaches the host API at `http://10.0.2.2:8000`. A real head unit needs the host's LAN IP or a public deployment.
- **Public demo deployment:** any container host (Fly.io, Render, Railway) with managed Postgres. No platform-specific code.

### 12.2 Seed data (`python -m scripts.seed`)
- Creates about 40 users across 4 regions (`RS`, `US`, `DE`, `NO`), 3 cars, and about 2,000 drives spread over the last 400 days with realistic score distributions and mistakes. Every leaderboard period then has content for the demo.
- Creates the known demo user `demo` / `demo-password-123` in region `RS`.
- Idempotent. Drops and re-creates the seed rows (fixed usernames and car IDs, plus all drives on the seed cars) with a fixed random seed. Car tokens are new on every run and printed.
- Other seed users log in with `<username>` / `seed-password-123`. At most 8 links per car are active; the rest are stored as revoked (historic) links.
- Run it with `python -m scripts.seed [--now <ISO-8601>] [--drives-per-user N]`.

---

## 13. Testing Strategy

| Level | What |
|---|---|
| Unit | Password hashing, token generation and hashing, period window and bucket math, weighted score math, mistake type validation. |
| API (integration, real Postgres) | Register, login, refresh (including the reuse-detection path); car register, link driver, unlink; upload drive (201, idempotent 200, 409 conflict, 422 validation, 403 not linked); history pagination; stats for each period, including empty series buckets; leaderboard ranking, ties, qualification, region filter, hidden users, `me` row. |
| Contract | A snapshot test of `/openapi.json`, so breaking changes are deliberate. The Android client's DTOs are checked against it. |
| Fixtures | Factory helpers for users, cars, and drives. A frozen clock fixture. |

Target: about 80% line coverage on `services/` and `repositories/`, and every endpoint covered by at least one happy-path test and one error-path test.

---

## 14. Implementation Milestones (hackathon)

1. **M1 Skeleton:** FastAPI app, settings, Docker Compose, Alembic baseline, `/health`, error handling.
2. **M2 Auth and cars:** users, register/login/refresh, car register, link and unlink drivers, principal dependencies.
3. **M3 Drives:** upload (idempotent), history, detail.
4. **M4 Stats and leaderboard:** queries, cache, endpoints.
5. **M5 Seed, tests, docs:** seed script, test suite, OpenAPI polish, README with curl examples.

---

## 15. Future Work

- Email verification and password reset. Optional 2FA for phone apps.
- Proper rate limiting (e.g. Redis-based) and account lockout policies.
- Phone and iOS apps: view stats, manage linked cars, edit profile, register.
- Sub-national regions (`US-CA`), city or club leaderboards, friends-only boards.
- Achievements, streaks, badges ("7 days without phone use").
- Storing real-time alerts for coaching insights. Score trends per mistake type.
- Materialized leaderboards and a horizontally scaled cache.
- GDPR: data export, account deletion (hard delete with cascade), retention policy for raw events.
- Car provisioning per vehicle (signed device certificates) instead of a shared provisioning key.
- Fleet or family accounts (one owner manages several cars and drivers).

---

## 16. Open Questions and Assumptions

| # | Topic | Assumption made | To confirm |
|---|---|---|---|
| 1 | User registration | ✅ **Resolved:** accounts can be created on the car (`POST /cars/me/drivers/register`) as well as via `/auth/register`. | – |
| 2 | Distance from Game engine | ✅ **Resolved:** no distance data. Ranking and stats are weighted by driving time. | – |
| 3 | Rolling vs calendar windows | Rolling windows (last 24 h, 7 d, 30 d, 365 d). | Is "this calendar week/month" preferred? |
| 4 | Region granularity | Country (ISO alpha-2). | Is country enough? |
| 5 | Score precision | Stored with 2 decimals, displayed as an integer. | Is the engine score an integer or a float? |
| 6 | VIN | Optional. | Is the VIN available to the app on the target head unit? |

---

## 17. Implementation Notes (v1.1)

These decisions were made while implementing, and the sections above have been updated to match:

| Topic | Decision |
|---|---|
| Upload body | The car forwards the raw `drive.result`, so `session_id` and `user_id` may appear in the body. If present, they must match `id` and `X-Driver-Id`. Unknown fields are allowed and kept. `raw_payload` = the request body as received. |
| Idempotency check | "Same payload" = JSON-equal to the stored `raw_payload`. No extra hash column. |
| Unlink | `DELETE /cars/me/drivers/{id}` is idempotent (`204` when already revoked) and `404` only if the user was never linked. |
| Leaderboard defaults | `region` defaults to the caller's home region. `period` defaults to `week`. |
| New error codes | `CAR_DRIVER_LIMIT` and `VIN_TAKEN` (409), `PAYLOAD_TOO_LARGE` (413), `TOO_MANY_ATTEMPTS` (429). |
| Zero-weight users | Users whose total driving time in the window is 0 are never ranked, even when `min_minutes` is 0. |
| Stats mistakes | Only types with count > 0 are listed. |
| `app.main:app` | Built lazily, so importing the module in tests needs no production environment variables. Tests use `create_app(settings, clock=…)`. |
| Coverage | `coverage` runs with `concurrency = greenlet` (SQLAlchemy's asyncio layer runs in greenlets). |
| Compose ports | Host ports are configurable (`DB_PORT`, `API_PORT`) for machines that already run Postgres on 5432. |
