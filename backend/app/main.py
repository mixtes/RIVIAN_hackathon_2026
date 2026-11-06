"""FastAPI app factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.cache import LeaderboardCache
from app.clock import Clock, SystemClock
from app.config import Settings, get_settings
from app.db import make_engine, make_sessionmaker
from app.errors import install_error_handlers
from app.logging_setup import configure_logging
from app.middleware import RequestContextMiddleware
from app.routers import auth, cars, drives, meta, stats, users
from app.security.passwords import PasswordService
from app.security.throttle import LoginThrottle

API_VERSION = "0.1.0"


def create_app(settings: Settings | None = None, *, clock: Clock | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.env != "test":
        configure_logging(settings.log_level)

    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(
        title="Driving Score Platform API",
        version=API_VERSION,
        description=(
            "Users, cars, drives, personal statistics and leaderboards.\n\n"
            "Auth: `Authorization: Bearer <access JWT>` for users; `Authorization: Car <car_token>` "
            "(+ `X-Driver-Id`) for the car app; `X-Provisioning-Key` for car registration."
        ),
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_visible else None,
        redoc_url="/redoc" if settings.docs_visible else None,
        openapi_url="/openapi.json" if settings.docs_visible else None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.clock = clock or SystemClock()
    app.state.passwords = PasswordService(settings)
    app.state.throttle = LoginThrottle(
        max_failures=settings.login_max_failures,
        window=timedelta(minutes=settings.login_failure_window_min),
        lockout=timedelta(minutes=settings.login_lockout_min),
    )
    app.state.leaderboard_cache = LeaderboardCache(settings.leaderboard_cache_ttl_s)

    install_error_handlers(app)
    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-Id"],
        )
    app.add_middleware(
        RequestContextMiddleware, max_body_bytes=settings.max_body_bytes, hsts=settings.env == "prod"
    )

    for router in (
        meta.router,
        auth.router,
        users.router,
        cars.router,
        drives.router,
        stats.stats_router,
        stats.leaderboard_router,
    ):
        app.include_router(router)
    return app


_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:  # pragma: no cover - exercised by uvicorn/gunicorn
    """`uvicorn app.main:app` resolves `app` lazily (PEP 562), so importing app.main in tests
    doesn't require production environment variables."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
