from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import httpx
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.config import REPO_ROOT, Settings
from app.db.session import build_session_factory, get_db
from app.main import create_app


async def test_single_port_spa_hosting_serves_console_routes_and_preserves_api_404_envelope(
    tmp_path: Path,
    db_engine: Engine,
) -> None:
    dist_dir = tmp_path / "dist"
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir(parents=True)
    index_html = (
        "<!doctype html>"
        '<html lang="zh-CN"><head><title>AI 评测中心 · 确定性校准实验台</title></head>'
        '<body><div id="app"></div><script type="module" src="/assets/index-test.js"></script></body>'
        "</html>"
    )
    (dist_dir / "index.html").write_text(index_html, encoding="utf-8")
    (assets_dir / "index-test.js").write_text(
        "window.__AI_EVAL_CONSOLE_MOUNTED__ = true;",
        encoding="utf-8",
    )

    spa_settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=f"sqlite:///{(tmp_path / 'spa_hosting.db').as_posix()}",
        web_dist_dir=str(dist_dir),
        auto_init_db=False,
    )
    spa_app = create_app(settings=spa_settings)
    factory = build_session_factory(db_engine)

    def _override_db() -> Generator[Session, None, None]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    spa_app.dependency_overrides[get_db] = _override_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=spa_app),
        base_url="http://testserver",
    ) as client:
        # 1. Root "/" serves index.html
        root_resp = await client.get("/")
        assert root_resp.status_code == 200
        assert "text/html" in root_resp.headers.get("content-type", "")
        assert 'id="app"' in root_resp.text

        # 2. All Vue Router deep history routes fall back to index.html
        for spa_route in (
            "/login",
            "/providers",
            "/dimensions",
            "/datasets",
            "/runs",
            "/reports",
            "/reports/RUN_20261112_0001",
        ):
            route_resp = await client.get(spa_route)
            assert route_resp.status_code == 200, f"Failed SPA fallback on {spa_route}"
            assert "text/html" in route_resp.headers.get("content-type", "")
            assert "AI 评测中心" in route_resp.text

        # 3. Hashed static bundle under /assets/* is served directly
        js_resp = await client.get("/assets/index-test.js")
        assert js_resp.status_code == 200
        assert "__AI_EVAL_CONSOLE_MOUNTED__" in js_resp.text

        # 4. Unknown /api/v1/* endpoint NEVER returns HTML; always returns unified 404 JSON envelope
        api_missing = await client.get("/api/v1/nonexistent")
        assert api_missing.status_code == 404
        assert "application/json" in api_missing.headers.get("content-type", "")
        err_payload = api_missing.json()
        assert err_payload["code"] == 404
        assert err_payload["request_id"].startswith("req_")
        assert err_payload["data"] is None


async def test_real_vite_dist_bundle_when_present_serves_single_port_console(
    tmp_path: Path,
    db_engine: Engine,
) -> None:
    real_dist = REPO_ROOT / "web" / "dist"
    if not (real_dist / "index.html").is_file():
        return

    spa_settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=f"sqlite:///{(tmp_path / 'real_dist_spa.db').as_posix()}",
        web_dist_dir=str(real_dist),
        auto_init_db=False,
    )
    spa_app = create_app(settings=spa_settings)
    factory = build_session_factory(db_engine)

    def _override_db() -> Generator[Session, None, None]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    spa_app.dependency_overrides[get_db] = _override_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=spa_app),
        base_url="http://testserver",
    ) as client:
        root_resp = await client.get("/")
        assert root_resp.status_code == 200
        assert 'id="app"' in root_resp.text

        deep_resp = await client.get("/reports/RUN_20261112_0001")
        assert deep_resp.status_code == 200
        assert 'id="app"' in deep_resp.text

        built_assets = list((real_dist / "assets").glob("*.js"))
        assert len(built_assets) >= 1
        asset_resp = await client.get(f"/assets/{built_assets[0].name}")
        assert asset_resp.status_code == 200

        api_missing = await client.get("/api/v1/nonexistent")
        assert api_missing.status_code == 404
        assert api_missing.json()["code"] == 404
