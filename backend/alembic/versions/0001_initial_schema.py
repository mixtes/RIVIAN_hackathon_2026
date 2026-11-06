"""Initial schema (backend spec §6).

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE users (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username             CITEXT      NOT NULL UNIQUE,
    email                CITEXT      UNIQUE,
    password_hash        TEXT        NOT NULL,
    display_name         VARCHAR(40) NOT NULL,
    region_code          CHAR(2)     NOT NULL,
    avatar_color         CHAR(7)     NOT NULL DEFAULT '#4F7DF3',
    leaderboard_visible  BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at        TIMESTAMPTZ
);
CREATE INDEX ix_users_region ON users (region_code);

CREATE TABLE cars (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vin             VARCHAR(17) UNIQUE,
    name            VARCHAR(60) NOT NULL,
    model           VARCHAR(60),
    token_hash      CHAR(64)    NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ
);

CREATE TABLE car_drivers (
    car_id          UUID NOT NULL REFERENCES cars(id)  ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    linked_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at    TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ,
    PRIMARY KEY (car_id, user_id)
);
CREATE INDEX ix_car_drivers_user ON car_drivers (user_id);

CREATE TABLE refresh_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      CHAR(64)    NOT NULL UNIQUE,
    family_id       UUID        NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_agent      TEXT
);
CREATE INDEX ix_refresh_tokens_user ON refresh_tokens (user_id);
CREATE INDEX ix_refresh_tokens_family ON refresh_tokens (family_id);

CREATE TABLE drives (
    id               UUID PRIMARY KEY,
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    car_id           UUID NOT NULL REFERENCES cars(id)  ON DELETE RESTRICT,
    started_at       TIMESTAMPTZ  NOT NULL,
    ended_at         TIMESTAMPTZ  NOT NULL,
    duration_s       INTEGER      NOT NULL CONSTRAINT ck_drives_duration CHECK (duration_s >= 0),
    distance_km      NUMERIC(8,3) CONSTRAINT ck_drives_distance CHECK (distance_km >= 0),
    score            NUMERIC(5,2) NOT NULL CONSTRAINT ck_drives_score CHECK (score BETWEEN 0 AND 100),
    engine_version   VARCHAR(40),
    raw_payload      JSONB        NOT NULL,
    received_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_drives_times CHECK (ended_at >= started_at)
);
CREATE INDEX ix_drives_user_ended ON drives (user_id, ended_at DESC);
CREATE INDEX ix_drives_ended      ON drives (ended_at);
CREATE INDEX ix_drives_car        ON drives (car_id, ended_at DESC);

CREATE TABLE drive_mistakes (
    drive_id          UUID NOT NULL REFERENCES drives(id) ON DELETE CASCADE,
    type              VARCHAR(40) NOT NULL,
    count             INTEGER     NOT NULL CONSTRAINT ck_drive_mistakes_count CHECK (count >= 0),
    total_duration_s  NUMERIC(10,2) CONSTRAINT ck_drive_mistakes_total CHECK (total_duration_s >= 0),
    max_duration_s    NUMERIC(10,2) CONSTRAINT ck_drive_mistakes_max CHECK (max_duration_s >= 0),
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
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS drive_events;
DROP TABLE IF EXISTS drive_mistakes;
DROP TABLE IF EXISTS drives;
DROP TABLE IF EXISTS refresh_tokens;
DROP TABLE IF EXISTS car_drivers;
DROP TABLE IF EXISTS cars;
DROP TABLE IF EXISTS users;
"""


def _statements(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(UPGRADE_SQL):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _statements(DOWNGRADE_SQL):
        op.execute(stmt)
