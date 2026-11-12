from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, Text
from sqlalchemy.dialects.mysql import (
    BIGINT as MYSQL_BIGINT,
    DATETIME as MYSQL_DATETIME,
    MEDIUMTEXT as MYSQL_MEDIUMTEXT,
    TINYINT as MYSQL_TINYINT,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_uuid_hex() -> str:
    return uuid.uuid4().hex


PKBigInt = (
    BigInteger()
    .with_variant(MYSQL_BIGINT(unsigned=True), "mysql")
    .with_variant(Integer, "sqlite")
)
FKBigInt = BigInteger().with_variant(MYSQL_BIGINT(unsigned=True), "mysql")
MediumText = Text().with_variant(MYSQL_MEDIUMTEXT(), "mysql")
DateTimeMs = DateTime().with_variant(MYSQL_DATETIME(fsp=3), "mysql")
TinyInt1 = Integer().with_variant(MYSQL_TINYINT(1), "mysql")
