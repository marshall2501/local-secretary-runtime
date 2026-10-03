"""Approved local database/login pairs for daily runtime and isolated regression."""

PRODUCTION_DB = "secretary"
PRODUCTION_USER = "secretary_daily_runtime"
ISOLATED_DB = "secretary_pkb_proto_20260927"
ISOLATED_USER = "secretary_pkb_proto_writer_20260927"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def connection_mode(db) -> str | None:
    info = db.info
    host = info.host or ""
    if host not in LOCAL_HOSTS:
        return None
    pair = (info.dbname or "", info.user or "")
    if pair == (PRODUCTION_DB, PRODUCTION_USER):
        return "production"
    if pair == (ISOLATED_DB, ISOLATED_USER):
        return "isolated"
    return None


def allowed_daily_connection(db) -> bool:
    return connection_mode(db) is not None


def source_ref(db, namespace: str, identifier: str) -> str:
    mode = connection_mode(db)
    if mode is None:
        raise ValueError("Refusing non-isolated DB or non-dedicated writer")
    scheme = "local" if mode == "production" else "fixture"
    return f"{scheme}://{namespace}/{identifier}"


def source_ref_allowed(db, value: str) -> bool:
    mode = connection_mode(db)
    if mode == "production":
        return value.startswith("local://")
    if mode == "isolated":
        return value.startswith("fixture://")
    return False
