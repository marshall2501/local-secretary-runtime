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
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

import psycopg


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from infrastructure.postgres.core_task_query_repository import PostgresCoreTaskQueryRepository
from ritsuko.application.task_commands import (
    TaskCommandConflict, TaskCommandNotFound, TaskCommands,
)
from infrastructure.postgres.magi_task_repository import (
    PostgresMagiTaskRepository, claim_start, list_pending_starts, request_stop,
    reserve_start,
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
    if Path(os.environ["LSA_TEST_DB_ENV_FILE"]).read_text(encoding="utf-8").strip() != (
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
            assert checkpoint["goal_definition"]["resolution"] == "unresolved"
            assert checkpoint["goal_definition"]["criteria"] == []
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

    # APIプロセスのOperation辞書を引き継がず、実RepositoryだけでTaskを再照会する。
    # 物理プロセス再起動／実MAGIを伴わない隔離縦断検証。
    def new_commands() -> TaskCommands:
        """APIインスタンスを再生成し、既存DBへStopとQueryを接続する。"""
        return TaskCommands(
            None, PostgresCoreTaskQueryRepository(connect), lambda: [],
            request_stop=PostgresMagiTaskRepository(connect).request_stop,
        )

    pending_id = uuid4()
    with connect() as db:
        assert reserve_start(
            db, task_id=pending_id, request="synthetic pending after restart",
            member_specs=members, decision_strategy="standard",
            policy_snapshot=policy,
        )
    first = new_commands()
    assert first.operation(pending_id)["task_status"] == "pending"
    del first
    stopped = new_commands().stop(pending_id)
    assert stopped["source"] == "persisted_task"
    assert stopped["task_status"] == "paused"
    assert stopped["stop_requested"] is True
    assert stopped["execution_reconciliation_required"] is False
    retrieved = new_commands().operation(pending_id)
    assert retrieved["task_status"] == "paused"
    assert retrieved["stop_requested"] is True
    try:
        new_commands().stop(pending_id)
    except TaskCommandConflict:
        pass
    else:
        raise AssertionError("paused_task_was_stopped_twice")

    running_id = uuid4()
    with connect() as db:
        assert reserve_start(
            db, task_id=running_id, request="synthetic running after restart",
            member_specs=members, decision_strategy="standard",
            policy_snapshot=policy,
        )
        claim_start(
            db, task_id=running_id, request="synthetic running after restart",
            member_specs=members,
        )
    running_before = new_commands().operation(running_id)
    assert running_before["task_status"] == "running"
    assert running_before["stop_requested"] is None

    def concurrent_stop() -> dict:
        """別TaskCommands・別DB接続から同じrunning Taskへ停止を記録する。"""
        return new_commands().stop(running_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        stop_results = list(executor.map(lambda _: concurrent_stop(), range(2)))
    for result in stop_results:
        assert result["task_id"] == str(running_id)
        assert result["source"] == "persisted_task"
        assert result["task_status"] == "running"
        assert result["stop_requested"] is True
        assert result["execution_reconciliation_required"] is True
    running_after = new_commands().operation(running_id)
    assert running_after["task_status"] == "running"
    assert running_after["stop_requested"] is True
    assert running_after["execution_reconciliation_required"] is True
    with connect() as db, db.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM secretary.audit_events
               WHERE task_id=%s AND event_type='core.magi.stop_requested'""",
            (running_id,),
        )
        assert cur.fetchone()[0] == 1, "duplicate_stop_audit_event"

    try:
        new_commands().stop(uuid4())
    except TaskCommandNotFound:
        pass
    else:
        raise AssertionError("unknown_persisted_task_not_rejected")

    # Stopを保存した後、古いMAGIが「完了候補」を返す実DB競合の再現。
    # 表示だけではなく、Repositoryの同一行lockがpausedを確定すること。
    repository = PostgresMagiTaskRepository(connect)
    late_candidate = {
        "status": "candidate_ready",
        "next_step": "review_answer_candidate",
        "tool_read_executed": True,
        "observations": [{"source": "pkb", "verified": True, "text": "synthetic"}],
        "detail": {"answer_candidate": "stale worker would complete the Task"},
        "turns": [],
    }
    stopped_projection = repository.persist_session(
        task_id=running_id, session=late_candidate,
    )
    assert stopped_projection["task_status"] == "paused"
    repository.fail_task(task_id=running_id, error="LateWorkerException")
    assert new_commands().operation(running_id)["task_status"] == "paused"
    with connect() as db, db.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM secretary.audit_events
               WHERE task_id=%s AND event_type='core.magi.completed'""",
            (running_id,),
        )
        assert cur.fetchone()[0] == 0, "stale_worker_marked_completed"
    try:
        repository.persist_session(task_id=running_id, session=late_candidate)
    except ValueError as exc:
        assert str(exc) == "task_not_running"
    else:
        raise AssertionError("paused_task_accepted_stale_worker")

    goal_id = uuid4()
    with connect() as db:
        assert reserve_start(
            db, task_id=goal_id, request="synthetic custom Goal",
            member_specs=members, decision_strategy="llm",
            policy_snapshot=policy,
        )
        claim_start(
            db, task_id=goal_id, request="synthetic custom Goal",
            member_specs=members,
        )
    proposed_goal = {
        "task_id": str(goal_id),
        "status": "candidate_ready",
        "control_mode": "goal_loop_v1",
        "requires_source": False,
        "requires_web": False,
        "observations": [],
        "turns": [],
        "goal_judgment": {
            "goal": "Compare synthetic monthly budget and give a supported summary",
            "next": "respond", "fulfillment": "met",
            "answer": "Synthetic explanation; no source needed",
            "evidence": {"basis": "not_required", "result_ids": [],
                         "assessment": "Synthetic source-free example"},
            "reason": "synthetic_goal_assessed",
        },
    }
    projection = repository.persist_session(task_id=goal_id, session=proposed_goal)
    assert projection["task_status"] == "completed"
    goal_trace = new_commands().operation(goal_id)
    assert goal_trace["task_status"] == "completed"
    with connect() as db, db.cursor() as cur:
        cur.execute(
            """SELECT request, checkpoint FROM secretary.tasks WHERE id=%s""",
            (goal_id,),
        )
        request_text, goal_checkpoint = cur.fetchone()
        goal = goal_checkpoint["goal_definition"]
        assert request_text == "synthetic custom Goal"
        assert goal["resolution"] == "proposed", goal
        assert goal["interpreted_goal"] == proposed_goal["goal_judgment"]["goal"]
        assert goal["version"] == 2
        assert goal["criteria"] == []
        cur.execute(
            """SELECT count(*) FROM secretary.audit_events
               WHERE task_id=%s AND event_type='core.magi.goal_interpreted'""",
            (goal_id,),
        )
        assert cur.fetchone()[0] == 1
    assert new_commands()._queries.trace(goal_id)["task"]["goal_definition"] == goal

    for terminal_status in ("completed", "failed"):
        terminal_id = uuid4()
        with connect() as db:
            assert reserve_start(
                db, task_id=terminal_id,
                request="synthetic terminal " + terminal_status,
                member_specs=members, decision_strategy="standard",
                policy_snapshot=policy,
            )
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE secretary.tasks SET status=%s, completed_at=CASE WHEN %s = 'completed' THEN now() ELSE NULL END WHERE id=%s",
                    (terminal_status, terminal_status, terminal_id),
                )
        try:
            new_commands().stop(terminal_id)
        except TaskCommandConflict:
            pass
        else:
            raise AssertionError("terminal_task_mutated_by_stop")
        terminal_after = new_commands().operation(terminal_id)
        assert terminal_after["task_status"] == terminal_status
        assert terminal_after["stop_requested"] is None

    print("PASS: disposable PG Task Goal interpretation, revision and read-only query")
    print("PASS: disposable PG late MAGI completion/error cannot overwrite acknowledged Stop")
    print("PASS: disposable PG TaskCommands new-instance stop, query, concurrent idempotence and terminal guards")

    print("PASS: disposable PG durable intake, replay, 2-worker claim, stop-before-start")
    # The owning integration harness destroys this whole randomized Compose
    # project after testing. Never target any other project or runtime.


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify-durable-task-start.py DISPOSABLE_PROJECT CONTAINER")
    main(sys.argv[1], sys.argv[2])
