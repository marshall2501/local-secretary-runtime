"""Small PostgreSQL identity summary used by the developer UI."""
from __future__ import annotations

import os


def debug_database_summary(connection_factory, *, expected_database: str,
                           expected_user: str) -> dict:
    try:
        with connection_factory() as db:
            with db.cursor() as cur:
                cur.execute("SELECT current_database(), current_user")
                database, user = cur.fetchone()
            info = db.info
        boundary_ok = (
            database == expected_database
            and user == expected_user
            and (info.host or "") in ("127.0.0.1", "localhost", "::1")
        )
        return {
            "status": "ok" if boundary_ok else "warning",
            "database": database, "user": user, "host": info.host or "",
            "port": info.port, "boundary_ok": boundary_ok,
            "latest_migration": os.environ.get("LSA_PKB_DAILY_LATEST_MIGRATION") or "unknown",
        }
    except Exception:
        return {
            "status": "error", "database": "unavailable", "user": "unavailable",
            "host": "", "port": None, "boundary_ok": False,
            "latest_migration": os.environ.get("LSA_PKB_DAILY_LATEST_MIGRATION") or "unknown",
        }
