"""Approved local database/login pairs for daily runtime and isolated regression."""
from __future__ import annotations

import os
import re

PRODUCTION_DB = "secretary"
PRODUCTION_USER = "secretary_daily_runtime"
ISOLATED_DB = "secretary_pkb_proto_20260927"
ISOLATED_USER = "secretary_pkb_proto_writer_20260927"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_REBUILD_DB = re.compile(r"^secretary_rebuild_[a-z0-9_]{1,40}$")


def approved_production_database_name(value: str) -> bool:
    value = str(value or "").strip()
    return value == PRODUCTION_DB or bool(_REBUILD_DB.fullmatch(value))


def production_database_name() -> str:
    """Return the canonical DB, or one explicitly named replacement DB for rehearsal."""
    value = os.environ.get("LSA_DAILY_DB_NAME", "").strip()
    if not value:
        return PRODUCTION_DB
    if approved_production_database_name(value):
        return value
    raise RuntimeError("LSA_DAILY_DB_NAME is not an approved production database name")


def connection_mode(db) -> str | None:
    info = db.info
    host = getattr(info, "host", "") or ""
    if host not in LOCAL_HOSTS:
        return None
    pair = (
        getattr(info, "dbname", "") or "",
        getattr(info, "user", "") or "",
    )
    if pair == (production_database_name(), PRODUCTION_USER):
        return "production"
    if pair == (ISOLATED_DB, ISOLATED_USER):
        return "isolated"
    return None


def allowed_daily_connection(db) -> bool:
    return connection_mode(db) is not None


def source_ref(db, namespace: str, identifier: str) -> str:
    mode = connection_mode(db)
    if mode is None:
        raise ValueError("Refusing an unapproved daily runtime database")
    scheme = "local" if mode == "production" else "fixture"
    return f"{scheme}://{namespace}/{identifier}"


def source_ref_allowed(db, value: str) -> bool:
    mode = connection_mode(db)
    if mode == "production":
        return value.startswith("local://")
    if mode == "isolated":
        return value.startswith("fixture://")
    return False
