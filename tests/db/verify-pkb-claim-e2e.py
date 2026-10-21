# Provenance-Origin: original
# Provenance-AI: AI-assisted
"""Disposable PostgreSQL PKB Claim → Task Action/Result acceptance.

The default mode uses a synthetic MAGI. Explicit --live-ollama opt-in uses one
local Ollama model, never a cloud provider or normal MAGI profile. The owning
PowerShell harness binds only its random test PostgreSQL project to loopback.
No daily Runtime connection, production secrets, or real user memory are used.
RITSUKO and PKB persistence ports remain the actual PostgreSQL adapters.
"""
from __future__ import annotations

import argparse
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


def prepare(connect, *, predicate="driver_updated", value="DRV-200",
            entity_name=None, member_specs=None, explicit_memory_request=False):
    """One synthetic Entity and a Task granted Write only inside disposable DB."""
    name = entity_name if entity_name is not None else "架空GPU" + uuid4().hex[:10]
    text = (
        f"{name}の機種は{value}。" if predicate == "model"
        else f"{name}を{value}へ更新した。"
    )
    if explicit_memory_request:
        if predicate != "model":
            raise ValueError("live_memory_fixture_requires_model_attribute")
        # A fact statement alone is not necessarily a request to remember it.
        # Only the live acceptance Task states an unambiguous registration Goal.
        text += "この機種情報をPKBに記録して。"
    with connect() as db, db.transaction(), db.cursor() as cur:
        cur.execute(
            """INSERT INTO secretary.entities(name,entity_type,domain)
               VALUES (%s,'gpu','computer') RETURNING id""", (name,),
        )
        entity_id = cur.fetchone()[0]
    repo = PostgresMagiTaskRepository(connect)
    task_id = uuid4()
    assert repo.reserve_start(
        task_id=task_id, request=text, member_specs=member_specs or MEMBERS,
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
                   expected_predicate="driver_updated", expected_value="DRV-200",
                   prior_read_count=0):
    """Database constraints, source identity, final Result and no duplicates."""
    with connect() as db, db.cursor() as cur:
        cur.execute(
            """SELECT a.status,r.outcome,r.verified_by,r.source_id,
                      r.evidence->>'claim_id',r.id
               FROM secretary.actions a
               JOIN secretary.results r ON r.action_id=a.id
               WHERE a.task_id=%s AND a.operation='pkb.register_claim'""", (task_id,),
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
        assert cur.fetchone()[0] == 1 + prior_read_count
        cur.execute(
            "SELECT count(*) FROM secretary.pkb_input_receipts WHERE claim_id=%s",
            (receipt_claim,),
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT id FROM secretary.actions WHERE task_id=%s AND operation='pkb.register_claim'", (task_id,),
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



def report_live_goal_diagnostics(session: dict, reads: list, writes: list,
                                 turns: list) -> None:
    """実モデルの各判断を列挙値と件数だけで報告し、本文やIDは漏らさない。

    実モデルが予期した経路に進まなかった理由を検証するために、
    Goalの文字列フィールドではなく既知の次操作・状態だけを出力する。
    """
    status = session.get("status")
    if not isinstance(status, str) or status not in {"running", "waiting_information", "waiting_user",
                      "candidate_ready", "stopped", "proposal_ready"}:
        status = "other"
    print(f"diagnostic: goal_status={status}")
    print(f"diagnostic: goal_turn_count={len(turns)}")
    print(f"diagnostic: pkb_read_count={len(reads)}")
    print(f"diagnostic: pkb_write_count={len(writes)}")
    next_choices = {"invoke", "respond", "ask_user", "wait", "unable",
                    "request_read"}
    operation_choices = {"pkb.search", "pkb.register_claim"}
    result_choices = {"ok", "invalid", "unavailable", "timeout",
                      "error", "disagreement"}
    for index, turn in enumerate((session.get("turns") or [])[:3], 1):
        if not isinstance(turn, dict):
            print(f"diagnostic: goal_turn_{index}=invalid_record")
            continue
        proposal = turn.get("response")
        if not isinstance(proposal, dict):
            proposal = {}
        decision = proposal.get("next")
        if not isinstance(decision, str) or decision not in next_choices:
            decision = "none_or_other"
        operation = proposal.get("operation_request")
        operation = operation.get("operation_id") if isinstance(operation, dict) else None
        if not isinstance(operation, str) or operation not in operation_choices:
            operation = "none_or_other"
        result = turn.get("status")
        if not isinstance(result, str) or result not in result_choices:
            result = "other"
        print(
            f"diagnostic: goal_turn_{index}_status={result}"
            f" next={decision} operation={operation}"
        )


async def live_goal(connect, pkb, store, *, model: str, timeout: int) -> None:
    """架空の実Entityで、実MAGI→PKB読取→実Write→再判断を一Taskで試す。

    この関数は使い捨てDB専用入口からのみ起動する。モデル本文と本人情報を
    標準出力へ書かず、3 Turnを超えて推論を自動反復しない。
    """
    from infrastructure.async_runtime.transport import request_json_with_retry
    from pkb.application.daily import entity_map, search_text
    from ritsuko.application.pkb_observation import execute_magi_pkb_request
    from ritsuko.application.read_dispatch import execute_core_read
    from ritsuko.core.request_scope import core_answer
    import ritsuko.magi.async_execution as transport

    # Existing provider transport is bound explicitly in this disposable test,
    # never through the daily settings or any external/cloud profile.
    transport.request_json_with_retry = request_json_with_retry
    members = [{
        "name": "MELCHIOR", "provider": "ollama", "model": model,
        "endpoint": "http://127.0.0.1:11434", "enabled": True,
        "weight": 1.0, "timeout_seconds": timeout,
        "retry_within_turn": False,
        "context_window_tokens": 8192, "ollama_num_predict": 2048,
    }]
    repo, task_id, text, _args, entity_id = prepare(
        connect, predicate="model", value="RTX-SYNTH-400",
        entity_name="架空装置", member_specs=members,
        explicit_memory_request=True,
    )
    reads, writes, turns = [], [], []

    def check_turn(number: int) -> None:
        """モデル呼出し回数を制限し、予期しない追加推論を拒否する。"""
        if number > 3:
            raise ValueError("live_magi_turn_limit")
        turns.append(number)

    def pkb_read(user_raw: str, pending: dict, _session: dict) -> dict:
        """本人原文で既存Entityを解決し、実PKBのClaim検索を使う。"""
        if pending.get("source") != "pkb":
            raise ValueError("live_magi_non_pkb_source_not_allowed")

        def bounded_search(capability: str, query: str) -> dict:
            """共通RITSUKO read adapterだけでテストDBへ検索する。"""
            if capability != "pkb_search":
                raise ValueError("live_magi_unexpected_read_capability")
            return execute_core_read(
                capability, query,
                pkb_search=lambda q: search_text(q, repository=pkb),
                pkb_answer=core_answer,
                finance_read=None, web_research=None,
                finance_answer=None, web_answer=None,
            )

        execution = execute_magi_pkb_request(
            user_raw, pending, pkb_repository=pkb,
            entity_map=lambda: entity_map(pkb),
            execute_read=bounded_search,
        )
        resolved = (execution.get("source_metadata") or {}).get("resolved_entity") or {}
        if str(resolved.get("id")) != str(entity_id):
            raise ValueError("live_magi_verified_entity_missing")
        reads.append(execution.get("operation"))
        return execution

    def record_read(uid, execution, pending, attempt):
        """実Source/Action/Result IDを使い捨てDBに保存する。"""
        return repo.record_source_read(
            task_id=uid, execution=execution,
            pending_request=pending, expected_attempt_id=attempt,
        )

    def write(user_raw, uid, attempt, pending, _session):
        """実RITSUKOが予約したActionで一度だけPKBへ登録する。"""
        if writes:
            raise ValueError("live_magi_duplicate_write")
        result = invoke_pkb_claim(
            task_store=store, pkb_repository=pkb,
            task_id=uid, attempt_id=attempt,
            request_id=pending["request_id"], user_text=user_raw,
            arguments=pending["arguments"],
        )
        writes.append(result)
        return result

    def checkpoint(uid, session, observations, cycles, attempt):
        """次のLLM呼出し前に同一TaskのSource checkpointを確定する。"""
        repo.save_source_resume(
            task_id=uid, session=session, observations=observations,
            cycles=cycles, expected_attempt_id=attempt,
        )

    session = await run_observation_loop(
        text, task_id=task_id, member_specs=members, timeout=timeout,
        create_task_record=lambda uid, request, specs: repo.claim_start(
            task_id=uid, request=request, member_specs=specs,
        ),
        execute_source_request=pkb_read,
        record_source_read_record=record_read,
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
            policy_snapshot=POLICY,
        ),
        observation_continuation=continue_with_verified_observations_async,
        on_turn_start=check_turn,
        max_observation_cycles=3,
    )
    if reads != ["search"] or len(writes) != 1 or turns != [1, 2, 3]:
        report_live_goal_diagnostics(session, reads, writes, turns)
        raise AssertionError("live_magi_read_write_rejudge_sequence_failed")
    if session.get("status") != "candidate_ready":
        report_live_goal_diagnostics(session, reads, writes, turns)
        raise AssertionError("live_magi_goal_not_ready")
    judgment = session.get("goal_judgment") or {}
    if (judgment.get("next") != "respond"
            or writes[0]["result_id"] not in (
                judgment.get("evidence") or {}).get("result_ids", [])):
        report_live_goal_diagnostics(session, reads, writes, turns)
        raise AssertionError("live_magi_result_not_grounded")
    with connect() as db, db.cursor() as cur:
        cur.execute(
            "SELECT status FROM secretary.tasks WHERE id=%s", (task_id,),
        )
        if cur.fetchone()[0] != "completed":
            raise AssertionError("live_magi_task_not_completed")
    verify_receipt(
        connect, task_id, action_id=writes[0]["action_id"],
        result_id=writes[0]["result_id"], entity_id=entity_id,
        expected_predicate="model", expected_value="RTX-SYNTH-400",
        prior_read_count=1,
    )
    print("PASS: disposable real Ollama -> real PKB search -> Action/Claim/Receipt/Result -> real MAGI rejudgment -> completed")

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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    parser.add_argument("container")
    parser.add_argument("role_secret")
    parser.add_argument("--live-ollama", action="store_true")
    parser.add_argument("--model", default="gemma4:12b")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    if args.live_ollama and (
        not 10 <= args.timeout <= 300
        or not 1 <= len(args.model) <= 100
        or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:." for ch in args.model)
    ):
        raise ValueError("invalid_live_ollama_arguments")
    port = DISPOSABLE_PORT(args.project, args.container)
    secret_file = Path(args.role_secret)
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
    if args.live_ollama:
        asyncio.run(live_goal(
            connect, pkb, store, model=args.model, timeout=args.timeout,
        ))
        return
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
