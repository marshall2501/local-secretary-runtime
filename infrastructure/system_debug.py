"""Read-only runtime/DB/environment diagnostics for the daily portal.

This module never mutates the database and never returns secret values.
"""
from __future__ import annotations

import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import psycopg


PROCESS_STARTED_AT = datetime.now(timezone.utc)

SAFE_ENV_NAMES = (
    "LSA_PKB_DAILY_PORT",
    "LSA_DAILY_DB_NAME",
    "OLLAMA_HOST",
    "OPENAI_BASE_URL",
    "GEMINI_BASE_URL",
    "LSA_OLLAMA_CONTEXT_TOKENS",
    "LSA_MAGI_OLLAMA_NUM_PREDICT",
    "LSA_MAGI_CLOUD_ENABLED",
    "LSA_MAGI_MELCHIOR_PROVIDER",
    "LSA_MAGI_MELCHIOR_MODEL",
    "LSA_MAGI_MELCHIOR_ENDPOINT",
    "LSA_MAGI_MELCHIOR_CREDENTIAL_ENV",
    "LSA_MAGI_MELCHIOR_ENABLED",
    "LSA_MAGI_MELCHIOR_WEIGHT",
    "LSA_MAGI_MELCHIOR_TIMEOUT_SECONDS",
    "LSA_MAGI_MELCHIOR_CONTEXT_TOKENS",
    "LSA_MAGI_CASPER_PROVIDER",
    "LSA_MAGI_CASPER_MODEL",
    "LSA_MAGI_CASPER_ENDPOINT",
    "LSA_MAGI_CASPER_CREDENTIAL_ENV",
    "LSA_MAGI_CASPER_ENABLED",
    "LSA_MAGI_CASPER_WEIGHT",
    "LSA_MAGI_CASPER_TIMEOUT_SECONDS",
    "LSA_MAGI_CASPER_CONTEXT_TOKENS",
    "LSA_MAGI_BALTHASAR_PROVIDER",
    "LSA_MAGI_BALTHASAR_MODEL",
    "LSA_MAGI_BALTHASAR_ENDPOINT",
    "LSA_MAGI_BALTHASAR_CREDENTIAL_ENV",
    "LSA_MAGI_BALTHASAR_ENABLED",
    "LSA_MAGI_BALTHASAR_WEIGHT",
    "LSA_MAGI_BALTHASAR_TIMEOUT_SECONDS",
    "LSA_MAGI_BALTHASAR_CONTEXT_TOKENS",
    "LSA_PKB_DAILY_LATEST_MIGRATION",
    "LSA_PKB_DAILY_MIGRATION_COUNT",
)
SECRET_ENV_NAMES = (
    "OPENAI_API_KEY",
    "OPENAI_ADMIN_KEY",
    "GEMINI_API_KEY",
)
SECRET_PATH_ENV_NAMES = (
    "LSA_PKB_DAILY_SECRET",
)

KEY_RELATIONS = (
    ("Entities", "entities"),
    ("Claims", "claims"),
    ("Current Claims", "current_claims"),
    ("Pending Intake", "pkb_pending_intake"),
    ("Tasks", "tasks"),
    ("Actions", "actions"),
    ("Results", "results"),
    ("Service Connections", "service_connections"),
    ("LLM Profiles", "llm_profiles"),
    ("MAGI Assignments", "magi_member_assignments"),
    ("Service Billing Profiles", "service_billing_profiles"),
    ("Finance Transactions", "finance_transactions"),
)


def _git_dir(root: Path) -> Path | None:
    marker = root / ".git"
    if marker.is_dir():
        return marker
    if marker.is_file():
        try:
            line = marker.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if line.startswith("gitdir:"):
            candidate = Path(line.split(":", 1)[1].strip())
            return candidate if candidate.is_absolute() else (root / candidate).resolve()
    return None


def _read_git_ref(git_dir: Path, ref: str) -> str | None:
    loose = git_dir / ref
    try:
        if loose.is_file():
            value = loose.read_text(encoding="utf-8").strip()
            return value or None
    except OSError:
        pass

    packed = git_dir / "packed-refs"
    try:
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith(("#", "^")):
                    continue
                sha, _, name = line.partition(" ")
                if name == ref:
                    return sha
    except OSError:
        pass
    return None


def git_snapshot(root: Path) -> dict:
    result = {"branch": "unknown", "commit": "unknown"}
    git_dir = _git_dir(root)
    if git_dir is None:
        return result
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return result
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        result["branch"] = ref.rsplit("/", 1)[-1]
        result["commit"] = _read_git_ref(git_dir, ref) or "unknown"
    elif head:
        result["branch"] = "detached"
        result["commit"] = head
    return result


def runtime_snapshot(root: Path) -> dict:
    git = git_snapshot(root)
    return {
        "status": "ok",
        "branch": git["branch"],
        "commit": git["commit"],
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "pid": os.getpid(),
        "working_directory": str(Path.cwd()),
        "runtime_root": str(root),
        "process_started_at": PROCESS_STARTED_AT.isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def environment_snapshot(
    environ: Mapping[str, str] | None = None,
) -> list[dict]:
    env = os.environ if environ is None else environ
    rows: list[dict] = []

    for name in SAFE_ENV_NAMES:
        raw = str(env.get(name, "") or "")
        rows.append(
            {
                "name": name,
                "value": raw if raw else "NOT SET",
                "secret": False,
            }
        )

    for name in SECRET_ENV_NAMES:
        rows.append(
            {
                "name": name,
                "value": "SET" if env.get(name) else "NOT SET",
                "secret": True,
            }
        )

    for name in SECRET_PATH_ENV_NAMES:
        raw = str(env.get(name, "") or "")
        if not raw:
            value = "NOT SET"
        else:
            try:
                value = "SET / file OK" if Path(raw).is_file() else "SET / file missing"
            except OSError:
                value = "SET / file check failed"
        rows.append({"name": name, "value": value, "secret": True})

    return rows


def _safe_error(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    if len(message) > 160:
        message = message[:157] + "..."
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def database_snapshot(
    db,
    *,
    expected_database: str,
    expected_user: str,
    preflight_latest_migration: str | None = None,
    preflight_migration_count: str | None = None,
) -> dict:
    result = {
        "status": "ok",
        "boundary_ok": False,
        "error": None,
        "identity": {},
        "migration": {
            "latest": preflight_latest_migration or "unknown",
            "count": preflight_migration_count or "unknown",
            "source": "launcher preflight" if preflight_latest_migration else "unknown",
        },
        "counts": [],
        "relations": [],
    }

    try:
        with db.cursor() as cur:
            cur.execute(
                """SELECT current_database(), current_user,
                          current_setting('server_version'),
                          pg_size_pretty(pg_database_size(current_database())),
                          pg_backend_pid(),
                          (SELECT count(*) FROM pg_stat_activity
                           WHERE datname=current_database())"""
            )
            database, user, version, size, backend_pid, connection_count = cur.fetchone()
        info = db.info
        identity = {
            "database": database,
            "user": user,
            "host": info.host or "",
            "port": info.port,
            "server_version": version,
            "database_size": size,
            "backend_pid": backend_pid,
            "database_connections": connection_count,
        }
        result["identity"] = identity
        result["boundary_ok"] = (
            database == expected_database
            and user == expected_user
            and (info.host or "") in ("127.0.0.1", "localhost", "::1")
        )
    except Exception as exc:
        result["status"] = "error"
        result["error"] = _safe_error(exc)
        return result

    try:
        with db.cursor() as cur:
            cur.execute(
                """SELECT version
                   FROM secretary.schema_migrations
                   ORDER BY applied_at DESC, version DESC
                   LIMIT 1"""
            )
            latest = cur.fetchone()
            cur.execute("SELECT count(*) FROM secretary.schema_migrations")
            count = cur.fetchone()
        result["migration"] = {
            "latest": latest[0] if latest else "none",
            "count": int(count[0]) if count else 0,
            "source": "database",
        }
    except Exception as exc:
        if result["migration"]["source"] == "unknown":
            result["migration"]["error"] = _safe_error(exc)
        else:
            result["migration"]["database_read"] = "unavailable"
            result["migration"]["database_read_error"] = _safe_error(exc)

    try:
        with db.cursor() as cur:
            cur.execute(
                """SELECT table_name, table_type
                   FROM information_schema.tables
                   WHERE table_schema='secretary'
                   ORDER BY table_name"""
            )
            result["relations"] = [
                {"name": row[0], "type": row[1]} for row in cur.fetchall()
            ]
    except Exception as exc:
        result["relations_error"] = _safe_error(exc)

    for label, relation in KEY_RELATIONS:
        item = {"label": label, "relation": f"secretary.{relation}"}
        try:
            with db.cursor() as cur:
                cur.execute(
                    "SELECT to_regclass(%s)",
                    (f"secretary.{relation}",),
                )
                exists = cur.fetchone()[0]
                if exists is None:
                    item.update({"status": "missing", "count": None})
                else:
                    # relation is selected only from the constant KEY_RELATIONS tuple.
                    cur.execute(f"SELECT count(*) FROM secretary.{relation}")
                    item.update({"status": "ok", "count": int(cur.fetchone()[0])})
        except Exception as exc:
            item.update(
                {
                    "status": "unavailable",
                    "count": None,
                    "error": _safe_error(exc),
                }
            )
        result["counts"].append(item)

    return result
