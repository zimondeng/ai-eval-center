from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.common.envelope import ok_response
from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def get_health(db: Session = Depends(get_db)) -> dict[str, Any]:
    db.execute(text("SELECT 1"))
    return ok_response(
        {
            "status": "healthy",
            "service": "ai-eval-center",
            "db_ok": True,
        }
    )
