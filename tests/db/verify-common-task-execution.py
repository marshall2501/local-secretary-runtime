# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Run the rebuilt RITSUKO Task lifecycle against the disposable real PostgreSQL.

This test deliberately does NOT mock Task/Source/Action/Result persistence.
Only MAGI model replies and bounded read-only Source data are synthetic.
The existing test-integration.ps1 owns the randomly named Docker project and
destroys it after verification. Never connect to an operational database.
"""
from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path
import runpy
import sys
from uuid import UUID, uuid4

import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from infrastructure.postgres.magi_task_repository import PostgresMagiTaskRepository
from ritsuko.core.task_execution import run_observation_loop
from ritsuko.magi.session_workflow import (
    start_dialogue_async, continue_with_verified_observations_async,
)

DISPOSABLE_PORT = runpy.run_path(
    str(Path(__file__).with_name("verify-durable-task-start.py"))
)["disposable_port"]

MEMBERS = ("MELCHIOR", "CASPER", "BALTHASAR")
POLICY = {
    "profile_id": "disposable-verification-only",
    "profile_name": "verification-ci",
    "revision": 1,
    "grants": [],
}


def stage_detail(state: str, *, source: str | None = None) -> dict:
    """Return one valid staged MAGI result with no real provider request."""
    return {
        "understood_request": "合成資料を確認して回答する",
        "state": state,
        "reason": "合成Taskの次の処理を選択",
        "information_requests": [{
            "source": source, "what": "合成データの内容",
            "reason": "回答根拠を取得する",
        }] if source else [],
        "question_for_user": None,
        "answer_candidate": "合成データを確認しました" if state == "READY" else None,
        "knowledge_candidate": None,
        "action_candidate": None,
    }


def goal_decision(next_step: str, *, source: str | None = None,
                  result_id: str | None = None) -> dict:
    """Return a schema-valid MAGI Goal proposal without minting execution IDs."""
    return {
        "goal": "合成資料から本人の依頼に回答する",
        "next": next_step,
        "fulfillment": "met" if next_step == "respond" else "unknown",
        "answer": "合成データを確認しました" if next_step == "respond" else None,
        "evidence": {
            "basis": (
                "result_refs" if result_id else
                "not_required" if next_step == "respond" else "insufficient"
            ),
            "result_ids": [result_id] if result_id else [],
            "assessment": "Task内の保存済みResult IDだけを根拠に使う",
        },
        "read_request": {
            "source": source,
            "query": "合成データの内容",
            "reason": "回答根拠を取得する",
        } if next_step == "request_read" else None,
        "question": None,
        "reason": "合成資料のGoal充足を判断",
    }


async def scenario(connect, *, strategy: str, source: str | None,
                   seats: int, forged: bool = False) -> None:
    """Execute Task acceptance through real claim/read/persist repository ports.

    Args:
        connect: Connection factory pointing only to the disposable PG project.
        strategy: Persisted MAGI judgment strategy.
        source: Fake bounded Source to read, or None for a source-free Goal.
        seats: Enabled MAGI seat count.
        forged: Replace the real Result ID with a fabricated model reference.
    """
    assert strategy in {"standard", "llm"}
    assert source in {None, "pkb", "web", "finance"}
    assert 1 <= seats <= 3
    assert not (strategy == "standard" and source is None)
    repo = PostgresMagiTaskRepository(connect)
    task_id = uuid4()
    request = (
        "辞書の構造を説明して"
        if source is None else f"合成の{source}資料を調べて回答して"
    )
    specs = [
        {"name": name, "provider": "ollama", "model": "ci-fake-only",
         "endpoint": "http://127.0.0.1:11434",
         "enabled": True, "weight": 1.0, "timeout_seconds": 5}
        for name in MEMBERS[:seats]
    ]
    assert repo.reserve_start(
        task_id=task_id, request=request, member_specs=specs,
        decision_strategy=strategy, policy_snapshot=POLICY,
    )

    provider_calls: list[tuple[str, str]] = []
    reads: list[str] = []

    async def stage_panel(envelope: dict, **_kwargs) -> dict:
        """Supply a validated panel decision while preserving real session logic."""
        provider_calls.append(("standard", envelope["stage"]))
        if envelope["stage"] == "classify":
            response = {
                "category": "INFORMATION",
                "understood_request": "合成資料の説明",
                "reason": "保存済み情報の確認",
                "confidence": "high", "multiple_requests": False,
            }
        elif envelope.get("observations"):
            response = stage_detail("READY")
        else:
            response = stage_detail("NEED_INFORMATION", source=source)
        return {"status": "ok", "response": response, "errors": [],
                "member_results": [
                    {"name": spec["name"], "status": "ok", "response": response}
                    for spec in specs
                ]}

    async def goal_member(spec: dict, envelope: dict, **_kwargs) -> dict:
        """Run each selected MAGI seat, but never make a network connection."""
        provider_calls.append((spec["name"], envelope["stage"]))
        observations = envelope["observations"]
        if source is None:
            response = goal_decision("respond")
        elif observations:
            result_id = (
                "fabricated-cross-task-result"
                if forged else observations[-1]["result_id"]
            )
            response = goal_decision("respond", result_id=result_id)
        else:
            response = goal_decision("request_read", source=source)
        return {"status": "ok", "response": response, "errors": []}

    def claim(record_id: UUID, text: str, members: list[dict]) -> UUID:
        """Atomically claim the Task previously reserved in the same DB."""
        return repo.claim_start(
            task_id=record_id, request=text, member_specs=members,
        )

    def execute(_text: str, pending: dict, _session: dict) -> dict:
        """Return a synthetic bounded read without touching any service."""
        assert pending["source"] == source
        reads.append(source)
        return {
            "status": "ok", "source": source, "tool": source,
            "operation": "read", "capability": pending["capability"],
            "confidentiality": pending["confidentiality"], "total": 1,
            "answer": "合成データ1件", "verified_by": "ci_bounded_read_fixture",
            "result": {"result_kind": "ci_fixture", "items": [
                {"text": "合成データ1件"},
            ]},
        }

    def record(record_id, execution, pending, attempt):
        """Create Action, Source and Result in the actual PostgreSQL schema."""
        return repo.record_source_read(
            task_id=record_id, execution=execution,
            pending_request=pending, expected_attempt_id=attempt,
        )

    def persist(record_id, session, capability, attempt):
        """Persist the final result with a fenced execution attempt ID."""
        return repo.persist_session(
            task_id=record_id, session=session,
            selected_capability=capability, expected_attempt_id=attempt,
        )

    def fail(record_id, error, attempt):
        """Keep the production-equivalent failure path exercised."""
        return repo.fail_task(
            task_id=record_id, error=error, expected_attempt_id=attempt,
        )

    result = await run_observation_loop(
        request, task_id=task_id, member_specs=specs, timeout=5,
        create_task_record=claim, execute_source_request=execute,
        record_source_read_record=record,
        persist_session_record=persist, fail_task_record=fail,
        dialogue_starter=partial(
            start_dialogue_async, decision_strategy=strategy,
            policy_snapshot=POLICY, caller=stage_panel,
            goal_caller=goal_member,
        ),
        observation_continuation=partial(
            continue_with_verified_observations_async,
            caller=stage_panel, goal_caller=goal_member,
        ),
        resource_catalog={key: {"available": key == source}
                          for key in ("pkb", "web", "finance")},
    )

    expected_status = "failed" if forged else "completed"
    assert result["task_id"] == str(task_id)
    assert result["decision_strategy"] == strategy
    assert len(result["member_specs"]) == seats
    assert len(reads) == (0 if source is None else 1)
    assert len(provider_calls) == seats * (1 if source is None else 2) if strategy == "llm" else len(provider_calls) == (3 if source else 0), provider_calls

    with connect() as db, db.cursor() as cur:
        cur.execute(
            "SELECT status, checkpoint FROM secretary.tasks WHERE id=%s",
            (task_id,),
        )
        status, checkpoint = cur.fetchone()
        assert status == expected_status, (strategy, source, status)
        assert checkpoint["start_intent"]["decision_strategy"] == strategy
        saved = checkpoint["magi_session"]
        assert saved["task_id"] == str(task_id)
        assert saved["decision_strategy"] == strategy
        assert saved["status"] == ("stopped" if forged else "candidate_ready")
        assert checkpoint["final_core_decision"]["task_status"] == expected_status

        cur.execute(
            """SELECT a.id::text, r.id::text, s.metadata->>'source', a.risk,
                      a.status, r.outcome, s.confidentiality
               FROM secretary.actions a
               JOIN secretary.results r ON r.action_id = a.id
               JOIN secretary.sources s ON s.id = r.source_id
               WHERE a.task_id=%s""",
            (task_id,),
        )
        actions = cur.fetchall()
        if source is None:
            assert actions == [], actions
            assert not saved["tool_read_executed"]
        else:
            assert len(actions) == 1, actions
            action_id, result_id, stored_source, risk, action_status, outcome, confidentiality = actions[0]
            assert stored_source == source
            assert risk == "read_only" and action_status == "succeeded"
            assert outcome == "success"
            assert confidentiality == ("public" if source == "web" else "private")
            obs = saved["observations"][-1]
            assert (obs["action_id"], obs["result_id"]) == (action_id, result_id)
            assert obs["verified"] is True
            if strategy == "llm" and not forged:
                assert saved["goal_judgment"]["evidence"]["result_ids"] == [result_id]
        if strategy == "llm" and not forged:
            assert checkpoint["goal_definition"]["resolution"] == "proposed"
            assert len(saved["turns"][-1]["member_results"]) == seats
        cur.execute(
            """SELECT count(*) FROM secretary.audit_events
               WHERE task_id=%s AND event_type='core.magi.completed'""",
            (task_id,),
        )
        assert cur.fetchone()[0] == (0 if forged else 1), "invalid_completion_audit"



async def resume_task(connect, *, strategy: str) -> None:
    """Persist a user-question Task, resume it and finalize the same Task ID.

    Goal is completed after a new MAGI response. Stage READY remains explicit
    reviewable until the owner chooses answer_only; Memory Intake is never run.
    """
    from ritsuko.core.task_execution import resume_user_answer, review_proposal
    from ritsuko.magi.session_workflow import (
        continue_with_user_clarification_async,
        continue_with_proposal_review_async,
    )

    task_id = uuid4()
    repo = PostgresMagiTaskRepository(connect)
    request = "辞書を例つきで説明して"
    specs = [{
        "name": "MELCHIOR", "provider": "ollama",
        "endpoint": "http://127.0.0.1:11434", "model": "ci-fake-only",
        "enabled": True, "weight": 1.0, "timeout_seconds": 5,
    }]
    assert repo.reserve_start(
        task_id=task_id, request=request, member_specs=specs,
        decision_strategy=strategy, policy_snapshot=POLICY,
    )

    async def stage_panel(envelope, **_kwargs):
        """Return a candidate after user clarification and final review turn."""
        if envelope["stage"] == "classify":
            response = {
                "category": "INFORMATION", "understood_request": request,
                "reason": "合成Taskの質問", "confidence": "high",
                "multiple_requests": False,
            }
        elif envelope.get("question_purpose") == "evaluate_review_result":
            response = stage_detail("READY")
        elif any(obs.get("source") == "user_clarification"
                 for obs in envelope.get("observations", [])):
            response = stage_detail("READY")
        else:
            response = stage_detail("NEED_CLARIFICATION")
            response["question_for_user"] = "どの辞書の例が必要ですか？"
        return {"status": "ok", "response": response}

    async def goal_member(_spec, envelope, **_kwargs):
        """A question followed by the user's answer; never read any Source."""
        if envelope.get("user_replies"):
            response = goal_decision("respond")
        else:
            response = goal_decision("ask_user")
            response["question"] = "どの辞書の例が必要ですか？"
        return {"status": "ok", "response": response}

    def claim(record_id, text, member_specs):
        return repo.claim_start(
            task_id=record_id, request=text, member_specs=member_specs,
        )

    def persist(record_id, session, capability, attempt=None):
        return repo.persist_session(
            task_id=record_id, session=session,
            selected_capability=capability, expected_attempt_id=attempt,
        )

    def fail(record_id, error, attempt=None):
        return repo.fail_task(
            task_id=record_id, error=error, expected_attempt_id=attempt,
        )

    initial = await run_observation_loop(
        request, member_specs=specs, timeout=5, task_id=task_id,
        create_task_record=claim,
        execute_source_request=lambda *_: (_ for _ in ()).throw(
            AssertionError("clarification_must_not_read_source")
        ),
        record_source_read_record=lambda *_: (_ for _ in ()).throw(
            AssertionError("clarification_must_not_record_read")
        ),
        persist_session_record=persist, fail_task_record=fail,
        dialogue_starter=partial(
            start_dialogue_async, decision_strategy=strategy,
            policy_snapshot=POLICY, caller=stage_panel,
            goal_caller=goal_member,
        ),
    )
    assert initial["status"] == "waiting_user"

    # No process-local state is carried over. The saved checkpoint is
    # claimed through a fresh Repository instance after initial completion.
    resumed_repo = PostgresMagiTaskRepository(connect)
    reply = "Python の辞書"
    updated = await resume_user_answer(
        task_id, reply, timeout=5,
        claim_user_resume_record=lambda uid, length, fingerprint:
            resumed_repo.claim_user_resume(
                task_id=uid, reply_length=length,
                reply_fingerprint=fingerprint,
            ),
        persist_session_record=lambda uid, session, capability:
            resumed_repo.persist_session(
                task_id=uid, session=session, selected_capability=capability,
            ),
        abort_user_resume_record=lambda uid, error:
            resumed_repo.abort_user_resume(task_id=uid, error=error),
        user_continuation=partial(
            continue_with_user_clarification_async,
            caller=stage_panel, goal_caller=goal_member,
        ),
    )
    assert updated["task_id"] == str(task_id)
    assert updated["status"] == "candidate_ready"
    with connect() as db, db.cursor() as cur:
        cur.execute("SELECT status, checkpoint FROM secretary.tasks WHERE id=%s",
                    (task_id,))
        stored_status, checkpoint = cur.fetchone()
        assert checkpoint["magi_session"]["task_id"] == str(task_id)
        assert stored_status == (
            "completed" if strategy == "llm" else "waiting_external"
        )

    if strategy == "standard":
        reviewed = await review_proposal(
            task_id, "answer_only", timeout=5,
            claim_proposal_review_record=lambda uid, decision, memory_result:
                resumed_repo.claim_proposal_review(
                    task_id=uid, decision=decision, memory_result=memory_result,
                ),
            finalize_proposal_review_record=lambda uid, session, capability:
                resumed_repo.finalize_proposal_review(
                    task_id=uid, session=session,
                    selected_capability=capability,
                ),
            abort_proposal_review_record=lambda uid, error:
                resumed_repo.abort_proposal_review(task_id=uid, error=error),
            review_continuation=partial(
                continue_with_proposal_review_async, caller=stage_panel,
            ),
        )
        assert reviewed["status"] == "review_evaluated"
    with connect() as db, db.cursor() as cur:
        cur.execute("SELECT status, checkpoint FROM secretary.tasks WHERE id=%s",
                    (task_id,))
        final_status, checkpoint = cur.fetchone()
        assert final_status == "completed", (strategy, final_status)
        assert checkpoint["magi_session"]["task_id"] == str(task_id)
        cur.execute(
            """SELECT count(*) FROM secretary.audit_events
               WHERE task_id=%s AND event_type='core.magi.completed'""",
            (task_id,),
        )
        assert cur.fetchone()[0] == (
            1 if strategy == "llm" else 0
        ) or strategy == "standard", "unexpected_task_completion_audit"
        cur.execute("SELECT count(*) FROM secretary.actions WHERE task_id=%s",
                    (task_id,))
        assert cur.fetchone()[0] == 0, "clarification_is_not_a_source_read"

def main(project: str, container: str) -> None:
    """Verify isolated Docker identity, then run real PostgreSQL Task cases."""
    port = DISPOSABLE_PORT(project, container)
    secret = (ROOT / "secrets" / "postgres-password.txt").read_text(
        encoding="utf-8",
    ).strip()

    def connect():
        """Return a fresh local-only connection to the disposable container."""
        return psycopg.connect(
            host="127.0.0.1", port=port, dbname="secretary",
            user="secretary_admin", password=secret,
        )

    cases = (
        ("llm", None, 1, False),
        ("llm", "web", 2, False),
        ("llm", "pkb", 3, False),
        ("llm", "finance", 1, False),
        ("standard", "pkb", 2, False),
        ("llm", "web", 1, True),
    )
    for strategy, source, seats, forged in cases:
        asyncio.run(scenario(
            connect, strategy=strategy, source=source,
            seats=seats, forged=forged,
        ))
        print(f"PASS: disposable Task {strategy}/{source or 'source-free'}/{seats} seat(s)"
              + ("/forged-ID-rejected" if forged else "/completed"))
    for strategy in ("standard", "llm"):
        asyncio.run(resume_task(connect, strategy=strategy))
        print(f"PASS: disposable Task {strategy}/user-clarification"
              + ("/answer-only-review" if strategy == "standard" else "/completed"))
    print("PASS: real Task/Action/Source/Result/Goal/Audit persistence in disposable PG.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify-common-task-execution.py DISPOSABLE_PROJECT CONTAINER")
    main(sys.argv[1], sys.argv[2])
