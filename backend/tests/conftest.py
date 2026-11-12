from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.common.auth_deps import create_access_token
from app.config import Settings, get_settings
from app.db.base import Base
from app.db.models import AdminUser
from app.db.seed import seed_defaults
from app.db.session import build_engine, build_session_factory, get_db
from app.main import create_app


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    db_file = tmp_path / "test_kd_eval.db"
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=f"sqlite:///{db_file.as_posix()}",
        jwt_secret="test-jwt-secret-key-at-least-32-bytes-long",
        master_encryption_key="test-master-encryption-key-32-bytes",
        default_admin_username="admin",
        default_admin_password="TestAdminPassword!123",
        web_dist_dir=str(tmp_path / "nonexistent_web_dist"),
        auto_init_db=False,
    )


@pytest.fixture
def db_engine(test_settings: Settings) -> Generator[Engine, None, None]:
    engine = build_engine(test_settings.database_url)
    Base.metadata.create_all(bind=engine)
    factory = build_session_factory(engine)
    with factory() as session:
        seed_defaults(session, settings=test_settings)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Generator[Session, None, None]:
    factory = build_session_factory(db_engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def app(test_settings: Settings, db_engine: Engine) -> FastAPI:
    application = create_app(settings=test_settings)
    factory = build_session_factory(db_engine)

    def _override_get_db() -> Generator[Session, None, None]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_db] = _override_get_db
    application.dependency_overrides[get_settings] = lambda: test_settings
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient, None]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
def admin_headers(db_session: Session, test_settings: Settings) -> dict[str, str]:
    admin = db_session.scalar(select(AdminUser).where(AdminUser.username == "admin"))
    assert admin is not None
    token = create_access_token(
        subject=admin.username,
        user_id=admin.id,
        role=admin.role,
        settings=test_settings,
    )
    return {"Authorization": f"Bearer {token}"}
