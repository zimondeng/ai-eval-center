from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WEB_DIST_DIR = str(REPO_ROOT / "web" / "dist")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "AI Evaluation Center"
    app_env: str = "dev"
    host: str = "0.0.0.0"
    port: int = 18200

    database_url: str = "mysql+pymysql://root:root@127.0.0.1:3306/kd_eval?charset=utf8mb4"

    jwt_secret: str = "ai-eval-center-dev-jwt-secret-key-2026"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720

    master_encryption_key: str = "ai-eval-center-dev-master-key-32"

    default_admin_username: str = "admin"
    default_admin_password: str = "admin123456"
    default_admin_display_name: str = "系统管理员"

    desensitize_base_url: str = "http://127.0.0.1:18100"
    desensitize_api_key: str = ""

    proxy_candidates: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:10808", "http://127.0.0.1:7897"]
    )

    web_dist_dir: str = DEFAULT_WEB_DIST_DIR
    auto_init_db: bool = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
