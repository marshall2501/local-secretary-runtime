# Provenance-Origin: original
# Provenance-AI: AI-generated
"""実DBでTask受付→再送→競合claim→停止を検証する使い捨て専用検査。

呼出し元はtests/db/test-integration.ps1が作ったランダムCompose project。
本人のproduction DBやSecretへ接続しない。外部LLMやSource呼出しも行わない。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from infrastructure.postgres.magi_task_repository import (
    claim_start, list_pending_starts, request_stop, reserve_start,
)


def disposable_port(project: str, container: str) -> int:
    """呼出し先Dockerが今回生成の使い捨てDBでlocalhost公開か照合する。"""
    if not re.fullmatch(r"local-secretary-test-[a-f0-9]{12}", project):
        raise RuntimeError("non_disposable_project")
    if not re.fullmatch(r"[a-f0-9]{12,64}", container):
        raise RuntimeError("invalid_container_id")
    inspected = json.loads(subprocess.check_output(
        ["docker", "inspect", container], text=True,
    ))[0]
    if (
        inspected["Config"]["Labels"].get("com.docker.compose.project") != project
        or inspected["Config"]["Labels"].get("com.docker.compose.service")
        != "secretary-postgres"
        or inspected["State"]["Status"] != "running"
    ):
        raise RuntimeError("not_isolated_postgres")
    bindings = inspected["NetworkSettings"]["Ports"]["5432/tcp"]
    if len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
        raise RuntimeError("non_loopback_postgres")
    port = int(bindings[0]["HostPort"])
    if (ROOT / ".env.postgres").read_text(encoding="utf-8").strip() != (
        f"LSA_DB_PORT={port}"
    ):
        raise RuntimeError("isolated_port_mismatch")
    return port


def main(project: str, container: str) -> None:
    """本人以外の使い捨てDBで同じTask IDの安全な再送と一回claimを確かめる。"""
    port = disposable_port(project, container)
    secret = (ROOT / "secrets" / "postgres-password.txt").read_text(
        encoding="utf-8",
    ).strip()

    def connect():
        """同一使い捨てDBへ別Connectionを張り、claim競合を再現する。"""
        return psycopg.connect(
            host="127.0.0.1", port=port, dbname="secretary",
            user="secretary_admin", password=secret,
        )

    task_id = uuid4()
    members = [{
        "name": "MELCHIOR", "model": "synthetic", "provider": "ollama",
        "enabled": True, "timeout_seconds": 5,
    }]
    policy = {"profile_id": "synthetic-test", "revision": 1, "grants": []}
    with connect() as db:
        assert reserve_start(
            db, task_id=task_id, request="synthetic durable intake",
            member_specs=members, decision_strategy="llm",
            policy_snapshot=policy,
        )
        assert reserve_start(
            db, task_id=task_id, request="synthetic durable intake",
            member_specs=members, decision_strategy="llm",
            policy_snapshot=policy,
        ) is False
        try:
            reserve_start(
                db, task_id=task_id, request="different request",
                member_specs=members, decision_strategy="llm",
                policy_snapshot=policy,
            )
        except ValueError as exc:
            assert str(exc) == "task_id_intent_mismatch"
        else:
            raise AssertionError("mismatched_replay_was_accepted")
        with db.cursor() as cur:
            cur.execute(
                """SELECT status, checkpoint FROM secretary.tasks WHERE id=%s""",
                (task_id,),
            )
            status, checkpoint = cur.fetchone()
            assert status == "pending"
            assert checkpoint["member_specs"] == members
            assert checkpoint["start_intent"]["policy_snapshot"] == policy
            assert sum(row["task_id"] == task_id
                       for row in list_pending_starts(db)) == 1

    def attempt_claim() -> str:
        """独立Connectionで同時claimし、一方だけがMAGI実行権を得る。"""
        with connect() as db:
            try:
                claim_start(
                    db, task_id=task_id, request="synthetic durable intake",
                    member_specs=deepcopy(members),
                )
            except ValueError as exc:
                if str(exc) != "task_start_not_pending":
                    raise
                return "rejected"
            return "claimed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: attempt_claim(), range(2)))
    assert sorted(results) == ["claimed", "rejected"], results

    with connect() as db:
        with db.cursor() as cur:
            cur.execute(
                """SELECT status, checkpoint FROM secretary.tasks WHERE id=%s""",
                (task_id,),
            )
            status, checkpoint = cur.fetchone()
            assert status == "running"
            assert checkpoint["start_intent"]["state"] == "claimed"
            assert not any(x["task_id"] == task_id for x in list_pending_starts(db))
        assert request_stop(db, task_id=task_id) == "running"
        with db.cursor() as cur:
            cur.execute(
                "SELECT checkpoint FROM secretary.tasks WHERE id=%s", (task_id,),
            )
            assert cur.fetchone()[0]["stop_requested"] is True

    untouched = uuid4()
    with connect() as db:
        assert reserve_start(
            db, task_id=untouched, request="synthetic stop before worker",
            member_specs=members, decision_strategy="standard",
            policy_snapshot=policy,
        )
        assert request_stop(db, task_id=untouched) == "paused"
        assert not any(x["task_id"] == untouched for x in list_pending_starts(db))
        try:
            claim_start(
                db, task_id=untouched, request="synthetic stop before worker",
                member_specs=members,
            )
        except ValueError as exc:
            assert str(exc) == "task_start_not_pending"
        else:
            raise AssertionError("stopped_pending_task_was_claimed")

    print("PASS: disposable PG durable intake, replay, 2-worker claim, stop-before-start")
    # The owning integration harness destroys this whole randomized Compose
    # project after testing. Never target any other project or runtime.


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify-durable-task-start.py DISPOSABLE_PROJECT CONTAINER")
    main(sys.argv[1], sys.argv[2])
