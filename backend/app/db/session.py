from __future__ import annotations

from collections.abc import Generator
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings

_engine_cache: dict[str, Engine] = {}


def build_engine(database_url: str) -> Engine:
    kwargs: dict[str, Any] = {"future": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if database_url in ("sqlite://", "sqlite:///:memory:") or "mode=memory" in database_url:
            kwargs["poolclass"] = StaticPool
    else:
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 1800
    return create_engine(database_url, **kwargs)


def get_engine(settings: Settings | None = None) -> Engine:
    cfg = settings or get_settings()
    engine = _engine_cache.get(cfg.database_url)
    if engine is None:
        engine = build_engine(cfg.database_url)
        _engine_cache[cfg.database_url] = engine
    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    engine = get_engine()
    factory = build_session_factory(engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
