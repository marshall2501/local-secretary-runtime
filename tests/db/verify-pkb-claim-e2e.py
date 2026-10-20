# Provenance-Origin: original
# Provenance-AI: AI-assisted
"""Real PKB Claim → Task Action/Result → synthetic MAGI, in disposable PG only.

The owning PowerShell harness verifies the random Docker project and binds its
PostgreSQL to 127.0.0.1. This test never uses the daily Runtime connection,
secrets, real providers, or a real user's memory. The PKB Repository and all
RITSUKO persistence ports are actual PostgreSQL adapters.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
import runpy
import sys
from uuid import UUID, uuid4

import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from infrastructure.postgres.magi_task_repository import PostgresMagiTaskRepository
from infrastructure.postgres.pkb_claim_action_repository import PostgresPkbClaimActionStore
from infrastructure.postgres.pkb_repository import PostgresPkbRepository
from pkb.application.structured_claim import register_structured_claim
from ritsuko.application.pkb_claim_operation import (
    invoke_pkb_claim, reconcile_pkb_claim,
)
from ritsuko.core.task_execution import run_observation_loop
from ritsuko.magi.session_workflow import (
    start_dialogue_async, continue_with_verified_observations_async,
)

DISPOSABLE_PORT = runpy.run_path(
    str(Path(__file__).with_name("verify-durable-task-start.py"))
)["disposable_port"]
MEMBERS = [{
    "name": "MELCHIOR", "provider": "ollama", "model": "synthetic",
    "endpoint": "http://127.0.0.1:11434",
    "enabled": True, "weight": 1.0, "timeout_seconds": 5,
}]
POLICY = {
    "profile_id": "disposable", "profile_name": "disposable",
    "revision": 1, "grants": [{
        "label": "llm.send_context.private",
        "destination_scope": "local", "decision": "allow",
        "mask_rules": {"tokens": []},
    }],
}


def make_turn(next_step, text, entity_id, *, result_id=None,
              predicate="driver_updated", value="DRV-200"):
    """Synthetic MAGI: it only suggests args, never Action/Receipt identities."""
    return {
        "goal": "架空GPU更新を記録する",
        "next": next_step,
        "fulfillment": "met" if next_step == "respond" else "partial",
        "answer": "架空GPUを記録しました" if next_step == "respond" else None,
        "evidence": {
            "basis": "result_refs" if result_id else "not_required",
            "result_ids": [result_id] if result_id else [],
            "assessment": "保存されたResultを参照",
        },
        "read_request": None,
        "operation_request": {
            "operation_id": "pkb.register_claim",
            "arguments": {
                "entity_id": str(entity_id),
                "entity_mention": (
                    text.split("の機種は")[0] if predicate == "model"
                    else text.split("を")[0]
                ),
                "predicate": predicate,
                "value": value,
                "evidence_start": 0, "evidence_end": len(text),
            },
            "reason": "本人の明示更新イベント",
        } if next_step == "invoke" else None,
        "question": None, "reason": "架空の受入例",
    }


def prepare(connect, *, predicate="driver_updated", value="DRV-200"):
    """One synthetic Entity and a Task granted Write only inside disposable DB."""
    name = "架空GPU" + uuid4().hex[:10]
    text = (
        f"{name}の機種は{value}。" if predicate == "model"
        else f"{name}を{value}へ更新した。"
    )
    with connect() as db, db.transaction(), db.cursor() as cur:
        cur.execute(
            """INSERT INTO secretary.entities(name,entity_type,domain)
               VALUES (%s,'gpu','computer') RETURNING id""", (name,),
        )
        entity_id = cur.fetchone()[0]
    repo = PostgresMagiTaskRepository(connect)
    task_id = uuid4()
    assert repo.reserve_start(
        task_id=task_id, request=text, member_specs=MEMBERS,
        decision_strategy="llm", policy_snapshot=POLICY,
    )
    with connect() as db, db.transaction(), db.cursor() as cur:
        cur.execute(
            """UPDATE secretary.tasks
               SET permission_scope=jsonb_set(
                   permission_scope, '{pkb_write}', 'true'::jsonb)
               WHERE id=%s""", (task_id,),
        )
    args = make_turn(
        "invoke", text, entity_id, predicate=predicate, value=value,
    )["operation_request"]["arguments"]
    return repo, task_id, text, args, entity_id


def verify_receipt(connect, task_id, *, action_id, result_id, entity_id,
                   expected_predicate="driver_updated", expected_value="DRV-200"):
    """Database constraints, source identity, final Result and no duplicates."""
    with connect() as db, db.cursor() as cur:
        cur.execute(
            """SELECT a.status,r.outcome,r.verified_by,r.source_id,
                      r.evidence->>'claim_id',r.id
               FROM secretary.actions a
               JOIN secretary.results r ON r.action_id=a.id
               WHERE a.task_id=%s""", (task_id,),
        )
        rows = cur.fetchall()
        assert len(rows) == 1, rows
        status, outcome, verifier, source_id, claim_id, stored_result_id = rows[0]
        assert status == "succeeded" and outcome == "success", rows
        assert verifier in {
            "pkb_repository_receipt", "pkb_repository_receipt_reconciled",
        }, rows
        assert str(stored_result_id) == str(result_id), rows
        cur.execute(
            """SELECT p.claim_id,p.source_id,c.entity_id,c.predicate,
                      c.semantic_kind,c.value,s.source_type
               FROM secretary.pkb_input_receipts p
               JOIN secretary.claims c ON c.id=p.claim_id
               JOIN secretary.sources s ON s.id=p.source_id
               WHERE p.source_id=%s""", (source_id,),
        )
        receipts = cur.fetchall()
        assert len(receipts) == 1, receipts
        (receipt_claim, receipt_source, target, predicate,
         semantic_kind, saved_value, kind) = receipts[0]
        assert (str(receipt_claim), str(receipt_source)) == (
            claim_id, str(source_id),
        )
        assert target == entity_id and predicate == expected_predicate
        assert semantic_kind == ("attribute" if expected_predicate == "model" else "event")
        assert saved_value == expected_value
        assert kind == "user_statement"
        cur.execute(
            "SELECT count(*) FROM secretary.actions WHERE task_id=%s", (task_id,),
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT count(*) FROM secretary.pkb_input_receipts WHERE claim_id=%s",
            (receipt_claim,),
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT id FROM secretary.actions WHERE task_id=%s", (task_id,),
        )
        assert str(cur.fetchone()[0]) == str(action_id)


async def normal_goal(connect, pkb, store, *, predicate="driver_updated", value="DRV-200", truncated_evidence=False):
    """End-to-end with real RITSUKO DB and real PKB, only MAGI is synthetic."""
    repo, task_id, text, args, entity_id = prepare(
        connect, predicate=predicate, value=value,
    )
    calls = []
    records = []

    async def member(_spec, envelope, **_kwargs):
        assert "pkb.register_claim" in envelope["operation_catalog"]
        if envelope["turn"] == 1:
            calls.append("invoke")
            proposal = make_turn(
                "invoke", text, entity_id, predicate=predicate, value=value,
            )
            if truncated_evidence:
                assert predicate == "model"
                # Reproduce the real MAGI mistake: label is present but
                # the claimed value is cut off before the end.
                proposal["operation_request"]["arguments"]["evidence_end"] = len(text) - 7
            return {"status": "ok", "response": proposal}
        observations = envelope["observations"]
        assert len(observations) == 1 and observations[0]["verified"] is True
        assert observations[0]["source"] == "pkb_write"
        calls.append("rejudge")
        return {"status": "ok", "response": make_turn(
            "respond", text, entity_id,
            result_id=observations[0]["result_id"],
        )}

    def write(_user, current_task, attempt, pending, _session):
        assert current_task == task_id and _user == text
        observation = invoke_pkb_claim(
            task_store=store, pkb_repository=pkb,
            task_id=current_task, attempt_id=attempt,
            request_id=pending["request_id"], user_text=_user,
            arguments=pending["arguments"],
        )
        records.append(observation)
        return observation

    def checkpoint(uid, session, observations, cycles, attempt):
        repo.save_source_resume(
            task_id=uid, session=session, observations=observations,
            cycles=cycles, expected_attempt_id=attempt,
        )

    session = await run_observation_loop(
        text, task_id=task_id, member_specs=MEMBERS, timeout=5,
        create_task_record=lambda uid, t, m: repo.claim_start(
            task_id=uid, request=t, member_specs=m,
        ),
        execute_source_request=lambda *_: (_ for _ in ()).throw(
            AssertionError("no read expected")),
        record_source_read_record=lambda *_: (_ for _ in ()).throw(
            AssertionError("write is not a read")),
        execute_write_operation=write,
        checkpoint_source_progress=checkpoint,
        persist_session_record=lambda uid, s, cap, att: repo.persist_session(
            task_id=uid, session=s, selected_capability=cap,
            expected_attempt_id=att,
        ),
        fail_task_record=lambda uid, err, att: repo.fail_task(
            task_id=uid, error=err, expected_attempt_id=att,
        ),
        dialogue_starter=partial(
            start_dialogue_async, decision_strategy="llm",
            policy_snapshot=POLICY, goal_caller=member,
        ),
        observation_continuation=partial(
            continue_with_verified_observations_async, goal_caller=member,
        ),
    )
    assert calls == ["invoke", "rejudge"], calls
    assert len(records) == 1
    assert session["status"] == "candidate_ready", session
    with connect() as db, db.cursor() as cur:
        cur.execute("SELECT status,checkpoint FROM secretary.tasks WHERE id=%s", (task_id,))
        status, checkpoint = cur.fetchone()
        assert status == "completed", (status, checkpoint)
        assert checkpoint["magi_session"]["goal_judgment"]["evidence"]["result_ids"] == [
            records[0]["result_id"]
        ]
    verify_receipt(
        connect, task_id,
        action_id=records[0]["action_id"],
        result_id=records[0]["result_id"], entity_id=entity_id,
        expected_predicate=predicate, expected_value=value,
    )
    if truncated_evidence:
        with connect() as db, db.cursor() as cur:
            cur.execute(
                """SELECT parameters->'arguments'->>'evidence_end'
                   FROM secretary.actions WHERE task_id=%s""",
                (task_id,),
            )
            assert int(cur.fetchone()[0]) == len(text) - 1, (
                "reserved Action did not preserve the normalized source span"
            )
    if predicate == "model":
        # Existing attributes are not overwritten by a new model proposal.
        name = text.split("の機種は")[0]
        newer = f"{name}の機種はRTX-SYNTH-999。"
        fixed_time = datetime.now(timezone.utc)
        contested = register_structured_claim(
            pkb, task_id=str(task_id), invocation_id=str(uuid4()),
            user_text=newer, entity_id=str(entity_id),
            entity_mention=name, predicate="model", value="RTX-SYNTH-999",
            evidence_start=0, evidence_end=len(newer),
            recorded_at=fixed_time, occurred_at=fixed_time,
        )
        assert contested["status"] == "review", contested
        assert contested["reason"] == "existing_claim_requires_conflict_resolution"
        with connect() as db, db.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM secretary.claims WHERE entity_id=%s AND predicate='model'",
                (entity_id,),
            )
            assert cur.fetchone()[0] == 1, "conflicting model was written"


def interrupted_commit(connect, pkb, store):
    """Loss after PKB commit; reconcile by old Action without another write."""
    repo, task_id, text, arguments, entity_id = prepare(connect)
    attempt = repo.claim_start(task_id=task_id, request=text, member_specs=MEMBERS)
    request_id = f"REQ-{str(task_id)[:8]}-01-01"

    class LostResultStore:
        def reserve(self, **kw):
            return store.reserve(**kw)

        def finish(self, **_kw):
            raise RuntimeError("simulated_result_persistence_failure")

        def mark_unknown(self, **kw):
            return store.mark_unknown(**kw)

    try:
        invoke_pkb_claim(
            task_store=LostResultStore(), pkb_repository=pkb,
            task_id=task_id, attempt_id=attempt,
            request_id=request_id, user_text=text, arguments=arguments,
        )
    except RuntimeError as exc:
        assert str(exc) == "simulated_result_persistence_failure"
    else:
        raise AssertionError("Expected PKB commit/Result failure")

    with connect() as db, db.cursor() as cur:
        cur.execute(
            """SELECT a.id,a.status,r.id,r.outcome
               FROM secretary.actions a JOIN secretary.results r
                    ON r.action_id=a.id
               WHERE a.task_id=%s""", (task_id,),
        )
        action_id, action_status, old_result_id, outcome = cur.fetchone()
        assert action_status == "unknown" and outcome == "inconclusive"
        cur.execute(
            "SELECT count(*) FROM secretary.pkb_input_receipts", (),
        )
        before = cur.fetchone()[0]

    recovered = reconcile_pkb_claim(
        task_store=store, pkb_repository=pkb,
        task_id=task_id, action_id=action_id,
    )
    assert recovered is not None and recovered["verified"] is True
    assert recovered["result_id"] == str(old_result_id)
    assert recovered["responds_to"] == [request_id]
    repeated = reconcile_pkb_claim(
        task_store=store, pkb_repository=pkb,
        task_id=task_id, action_id=action_id,
    )
    assert repeated == recovered, (repeated, recovered)
    verify_receipt(
        connect, task_id, action_id=action_id,
        result_id=old_result_id, entity_id=entity_id,
    )
    with connect() as db, db.cursor() as cur:
        cur.execute("SELECT count(*) FROM secretary.pkb_input_receipts")
        assert cur.fetchone()[0] == before, "recovery dispatched a second write"
        cur.execute(
            """SELECT count(*) FROM secretary.audit_events
               WHERE action_id=%s AND event_type='core.magi.pkb_write_unknown'""",
            (action_id,),
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            """SELECT count(*) FROM secretary.audit_events
               WHERE action_id=%s AND event_type='core.magi.pkb_write_reconciled'""",
            (action_id,),
        )
        assert cur.fetchone()[0] == 1

    # A reserved-but-never-dispatched Action has no receipt. In particular,
    # the read-only reconciliation method must not write a Claim here.
    missing_repo, missing_task, missing_text, missing_args, _ = prepare(connect)
    missing_attempt = missing_repo.claim_start(
        task_id=missing_task, request=missing_text, member_specs=MEMBERS,
    )
    missing_request_id = f"REQ-{str(missing_task)[:8]}-01-01"
    missing_action = UUID(store.reserve(
        task_id=missing_task, attempt_id=missing_attempt,
        request_id=missing_request_id,
        user_text=missing_text, arguments=missing_args,
    )["action_id"])
    assert reconcile_pkb_claim(
        task_store=store, pkb_repository=pkb,
        task_id=missing_task, action_id=missing_action,
    ) is None
    with connect() as db, db.cursor() as cur:
        cur.execute(
            "SELECT status FROM secretary.actions WHERE id=%s", (missing_action,),
        )
        assert cur.fetchone()[0] == "running"
        cur.execute("SELECT count(*) FROM secretary.pkb_input_receipts")
        assert cur.fetchone()[0] == before


def main():
    if len(sys.argv) != 4:
        raise RuntimeError("requires_disposable_project_container_and_role_secret")
    port = DISPOSABLE_PORT(sys.argv[1], sys.argv[2])
    secret_file = Path(sys.argv[3])
    if (secret_file.parent.resolve() != (ROOT / "secrets").resolve()
            or not secret_file.name.startswith(".pkb-e2e-local-secretary-test-")
            or not secret_file.is_file()):
        raise RuntimeError("not_disposable_role_secret")
    password = secret_file.read_text(encoding="utf-8").strip()
    assert len(password) >= 32

    def connect():
        return psycopg.connect(
            host="127.0.0.1", port=port, dbname="secretary",
            user="secretary_daily_runtime", password=password,
            connect_timeout=5,
        )

    with connect() as db:
        assert db.info.user == "secretary_daily_runtime"
        assert db.info.dbname == "secretary"
    pkb = PostgresPkbRepository(connect)
    store = PostgresPkbClaimActionStore(connect)
    asyncio.run(normal_goal(connect, pkb, store))
    asyncio.run(normal_goal(
        connect, pkb, store, predicate="model", value="RTX-SYNTH-400",
    ))
    asyncio.run(normal_goal(
        connect, pkb, store, predicate="model", value="RTX-SYNTH-400",
        truncated_evidence=True,
    ))
    interrupted_commit(connect, pkb, store)
    print("PASS: disposable real PKB Claim -> receipt -> Action/Result -> MAGI rejudgment -> completed")
    print("PASS: explicit model attribute -> actual PKB attribute Claim -> Task Result -> MAGI rejudgment")
    print("PASS: truncated MAGI quote -> normalized Action reservation -> real PKB receipt -> Task completed")
    print("PASS: conflicting existing model is review-only, no second model Claim")
    print("PASS: lost Result reconciliation preserves Action/Result identity, no second PKB write")
    print("PASS: missing receipt stays unresolved and does not re-dispatch")


if __name__ == "__main__":
    main()
