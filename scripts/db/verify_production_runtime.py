"""Verify the production daily runtime against a disposable promoted database.

This script intentionally performs write probes only against the caller-supplied
target. It never prints row values, authentication payloads, or secret contents.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib import request
from uuid import UUID, uuid4

import psycopg


SETTING_TABLES = (
    "service_connections",
    "llm_profiles",
    "magi_member_assignments",
    "service_billing_profiles",
)


def _read_secret(path: Path) -> str:
    if not path.is_file():
        raise RuntimeError("required local secret file is missing")
    value = path.read_text(encoding="utf-8").strip()
    if len(value) < 32:
        raise RuntimeError("required local secret is invalid")
    return value


def _source_setting_counts(port: int, admin_secret_file: Path) -> dict[str, int]:
    password = _read_secret(admin_secret_file)
    with psycopg.connect(
        host="127.0.0.1",
        port=port,
        dbname="secretary_pkb_proto_20260927",
        user="secretary_admin",
        password=password,
        connect_timeout=5,
    ) as db:
        with db.cursor() as cur:
            cur.execute(
                """SELECT count(*) FROM secretary.schema_migrations
                   WHERE version='026_service_billing.sql'"""
            )
            if cur.fetchone()[0] != 1:
                raise RuntimeError("settings source is not at isolated migration 026")
            result = {}
            for table in SETTING_TABLES:
                cur.execute(f"SELECT count(*) FROM secretary.{table}")
                result[table] = int(cur.fetchone()[0])
            return result


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _json_request(url: str, payload: dict | None = None):
    body = None
    headers = {}
    method = "GET"
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    req = request.Request(url, data=body, headers=headers, method=method)
    with request.urlopen(req, timeout=3) as response:
        raw = response.read()
        if response.status != 200:
            raise RuntimeError(f"unexpected HTTP status: {response.status}")
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return json.loads(raw.decode("utf-8"))
        return raw


def _wait_json(url: str, timeout_seconds: float = 20.0):
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    while time.monotonic() < deadline:
        try:
            return _json_request(url)
        except Exception as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError("production-mode web runtime did not become ready") from last_error


def _no_model(_text: str, _entities: set[str]):
    return SimpleNamespace(
        status="unavailable",
        candidate=None,
        reason="production_rehearsal_no_model",
        model=None,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-port", type=int, required=True)
    parser.add_argument("--runtime-secret-file", type=Path, required=True)
    parser.add_argument("--settings-source-port", type=int)
    parser.add_argument("--admin-secret-file", type=Path)
    args = parser.parse_args()

    if not 1024 <= args.target_port <= 65535:
        raise RuntimeError("invalid target PostgreSQL port")
    if args.settings_source_port is not None and not 1024 <= args.settings_source_port <= 65535:
        raise RuntimeError("invalid settings source PostgreSQL port")
    if (args.settings_source_port is None) != (args.admin_secret_file is None):
        raise RuntimeError("settings source port and admin secret must be supplied together")
    _read_secret(args.runtime_secret_file)

    os.environ["LSA_DAILY_DB_MODE"] = "production"
    os.environ["LSA_PKB_DAILY_PORT"] = str(args.target_port)
    os.environ["LSA_PKB_DAILY_SECRET"] = str(args.runtime_secret_file)

    from infrastructure.postgres.pkb_runtime import connect_pkb_database
    from pkb.application.daily import register_text, search_text
    from pkb.pending_service import list_pending
    from capabilities.finance.finance_preview import analyze_moneyforward_csv
    from capabilities.finance.finance_import import commit_import, load_finance_dashboard
    from integrations.connections.service_connections import (
        LLM_INFERENCE,
        SERVICE_BILLING_READ,
        list_service_connections,
        upsert_service_connection,
    )
    from ritsuko.magi.settings import (
        list_llm_profiles,
        load_member_specs,
        upsert_llm_profile,
    )
    from capabilities.service_billing.settings import (
        list_service_billing_profiles,
        upsert_service_billing_profile,
    )
    from ritsuko.application.task_records import create_task_record

    expected_settings = None
    if args.settings_source_port is not None:
        expected_settings = _source_setting_counts(
            args.settings_source_port, args.admin_secret_file
        )

    with connect_pkb_database() as db:
        info = db.info
        if (info.dbname, info.user) != ("secretary", "secretary_daily_runtime"):
            raise RuntimeError("production runtime connected with the wrong database identity")
        with db.cursor() as cur:
            cur.execute(
                """SELECT count(*) FROM secretary.schema_migrations
                   WHERE version='008_runtime_privileges.sql'"""
            )
            if cur.fetchone()[0] != 1:
                raise RuntimeError("production runtime target is missing migration 008")

        if expected_settings is not None:
            actual_settings = {
                "service_connections": len(
                    list_service_connections(db, include_disabled=True)
                ),
                "llm_profiles": len(list_llm_profiles(db, include_disabled=True)),
                "magi_member_assignments": len(load_member_specs(db)),
                "service_billing_profiles": len(
                    list_service_billing_profiles(db, include_disabled=True)
                ),
            }
            if actual_settings != expected_settings:
                raise RuntimeError("runtime settings counts differ from the isolated source")

    suffix = uuid4().hex[:10]
    entity_name = "RehearsalPC-" + suffix
    with connect_pkb_database() as db:
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO secretary.entities(name, entity_type, domain)
                   VALUES (%s,'computer','rehearsal')
                   RETURNING id""",
                (entity_name,),
            )
            entity_id = cur.fetchone()[0]

    write_result = register_text(
        f"{entity_name}をDRV-RH1へ更新した。",
        connection_factory=connect_pkb_database,
        interpreter=_no_model,
    )
    if write_result.get("status") != "inserted":
        raise RuntimeError("production PKB write probe did not insert a claim")

    with connect_pkb_database() as db:
        with db.cursor() as cur:
            cur.execute(
                """SELECT s.uri, (s.metadata ? 'fictional_only')
                   FROM secretary.claims c
                   JOIN secretary.sources s ON s.id=c.source_id
                   WHERE c.id=%s AND c.entity_id=%s""",
                (UUID(write_result["claim_id"]), entity_id),
            )
            row = cur.fetchone()
            if row is None or not str(row[0]).startswith("local://daily-pkb/") or bool(row[1]):
                raise RuntimeError("production PKB write used an invalid source boundary")

    search_result = search_text(
        f"{entity_name}の現在のドライバー",
        connection_factory=connect_pkb_database,
    )
    if int(search_result.get("total") or 0) < 1:
        raise RuntimeError("production PKB read probe did not find the written state")

    pending_result = register_text(
        "production rehearsal ambiguous input " + suffix,
        connection_factory=connect_pkb_database,
        interpreter=_no_model,
    )
    pending_id = pending_result.get("pending_id")
    if pending_result.get("status") != "review" or not pending_id:
        raise RuntimeError("production Pending probe did not create a review item")
    with connect_pkb_database() as db:
        if not any(str(row["id"]) == str(pending_id) for row in list_pending(db)):
            raise RuntimeError("production Pending probe could not read its review item")

    task_id = uuid4()
    create_task_record(
        connect_pkb_database,
        task_id,
        "production runtime rehearsal task",
        [],
    )
    with connect_pkb_database() as db:
        with db.cursor() as cur:
            cur.execute("SELECT status FROM secretary.tasks WHERE id=%s", (task_id,))
            row = cur.fetchone()
            if row is None or row[0] != "running":
                raise RuntimeError("RITSUKO task persistence probe failed")
            cur.execute(
                """SELECT count(*) FROM secretary.audit_events
                   WHERE task_id=%s AND event_type='core.magi.task_started'""",
                (task_id,),
            )
            if cur.fetchone()[0] != 1:
                raise RuntimeError("RITSUKO audit persistence probe failed")

    external_id = "rehearsal-" + suffix
    csv_text = (
        "計算対象,日付,内容,金額（円）,保有金融機関,大項目,中項目,メモ,振替,ID\n"
        f"1,2026/10/03,Production rehearsal,-321,Rehearsal Bank,Test,Runtime,,0,{external_id}\n"
    )
    preview = analyze_moneyforward_csv(
        csv_text.encode("utf-8"), "production-rehearsal.csv"
    )
    with connect_pkb_database() as db:
        finance_result = commit_import(
            db, preview, csv_text.encode("utf-8"), "production-rehearsal.csv"
        )
        if finance_result.status != "committed" or finance_result.inserted != 1:
            raise RuntimeError("Finance production import probe failed")
        dashboard = load_finance_dashboard(
            db, search_text="Production rehearsal", row_mode="all"
        )
        if dashboard.transaction_count != 1:
            raise RuntimeError("Finance production read probe failed")

    with connect_pkb_database() as db:
        llm_connection = upsert_service_connection(
            db,
            adapter_key="ollama",
            display_name="Rehearsal Ollama " + suffix,
            endpoint="http://127.0.0.1:11434",
            capabilities=[LLM_INFERENCE],
            connection_type="none",
            connection_role="llm",
            enabled=True,
        )
        profile = upsert_llm_profile(
            db,
            connection_id=llm_connection["id"],
            model="rehearsal-" + suffix,
            display_name="Rehearsal Profile " + suffix,
            context_window_tokens=4096,
            ollama_num_predict=512,
            enabled=True,
        )
        if not profile.get("id"):
            raise RuntimeError("MAGI settings production write probe failed")

        billing_connection = upsert_service_connection(
            db,
            adapter_key="openai",
            display_name="Rehearsal Billing " + suffix,
            endpoint="https://api.openai.com/v1",
            credential_ref="env:OPENAI_API_KEY",
            capabilities=[SERVICE_BILLING_READ],
            auth_data={},
            connection_type="api_key",
            connection_role="service_billing",
            enabled=True,
        )
        billing_profile = upsert_service_billing_profile(
            db,
            display_name="Rehearsal Billing Profile " + suffix,
            connection_id=billing_connection["id"],
            enabled=True,
        )
        if not billing_profile.get("id"):
            raise RuntimeError("Service Billing production write probe failed")

    web_port = _free_port()
    web_env = dict(os.environ)
    web_env["LSA_DAILY_WEB_PORT"] = str(web_port)
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.Popen(
        [sys.executable, "-m", "interfaces.web.app"],
        cwd=str(root),
        env=web_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        entities = _wait_json(f"http://127.0.0.1:{web_port}/api/pkb/entities")
        if not any(item.get("name") == entity_name for item in entities):
            raise RuntimeError("production Web API entity read probe failed")

        api_search = _json_request(
            f"http://127.0.0.1:{web_port}/api/pkb/search",
            {"text": f"{entity_name}の現在のドライバー"},
        )
        if int(api_search.get("total") or 0) < 1:
            raise RuntimeError("production Web API PKB search probe failed")

        pending_rows = _json_request(
            f"http://127.0.0.1:{web_port}/api/pkb/pending"
        )
        if not any(str(item.get("id")) == str(pending_id) for item in pending_rows):
            raise RuntimeError("production Web API Pending probe failed")

        open_tasks = _json_request(
            f"http://127.0.0.1:{web_port}/api/core/tasks/open"
        )
        if not any(str(item.get("id")) == str(task_id) for item in open_tasks.get("items", [])):
            raise RuntimeError("production Web API RITSUKO task probe failed")

        raw = _json_request(f"http://127.0.0.1:{web_port}/debug")
        if not raw:
            raise RuntimeError("production debug page probe failed")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    counts_text = "settings=not-copied"
    if expected_settings is not None:
        counts_text = (
            "settings="
            + "/".join(str(expected_settings[name]) for name in SETTING_TABLES)
        )
    print(
        "Production runtime rehearsal: "
        + counts_text
        + " pkb=write/read/pending ritsuko=task/audit "
          "finance=import/read connections=write magi=write billing=write web=ok"
    )
    print("PASS: production daily runtime works against the disposable promoted database.")


if __name__ == "__main__":
    main()
