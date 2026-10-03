"""PostgreSQL connection adapter for the daily Local Secretary runtime."""
from __future__ import annotations

import os
from pathlib import Path

import psycopg

from config.runtime_database import (
    ISOLATED_DB,
    ISOLATED_USER,
    PRODUCTION_DB,
    PRODUCTION_USER,
)

HOST = "127.0.0.1"
MODE = os.environ.get("LSA_DAILY_DB_MODE", "isolated").strip().lower()
if MODE == "production":
    DBNAME = PRODUCTION_DB
    WRITER = PRODUCTION_USER
elif MODE == "isolated":
    DBNAME = ISOLATED_DB
    WRITER = ISOLATED_USER
else:
    raise RuntimeError("LSA_DAILY_DB_MODE must be production or isolated")


def _port() -> int:
    raw = os.environ.get("LSA_PKB_DAILY_PORT", "")
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("LSA_PKB_DAILY_PORT is missing or invalid") from exc
    if not 1024 <= value <= 65535:
        raise RuntimeError("Daily DB port is outside the allowed range")
    return value


def _secret_path() -> Path:
    raw = os.environ.get("LSA_PKB_DAILY_SECRET", "")
    path = Path(raw) if raw else Path()
    if not raw or not path.is_file():
        raise RuntimeError("LSA_PKB_DAILY_SECRET does not point to the daily runtime secret")
    return path


def connect_pkb_database():
    password = _secret_path().read_text(encoding="utf-8").strip()
    if len(password) < 32:
        raise RuntimeError("Invalid daily runtime writer secret")
    db = psycopg.connect(
        dbname=DBNAME,
        host=HOST,
        port=_port(),
        user=WRITER,
        password=password,
        connect_timeout=5,
        autocommit=True,
    )
    info = db.info
    if ((info.dbname or "") != DBNAME
            or (info.user or "") != WRITER
            or (info.host or "") not in ("127.0.0.1", "localhost", "::1")):
        db.close()
        raise RuntimeError("Refusing an unexpected daily runtime database")
    return db
