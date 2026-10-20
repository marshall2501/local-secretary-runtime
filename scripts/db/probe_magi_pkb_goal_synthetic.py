# Provenance-Origin: original
# Provenance-AI: AI-assisted
"""One read-only local MAGI Goal proposal probe with entirely fictional PKB data.

The single Ollama request uses the production Goal contract and async transport,
but never instantiates a Task executor, PKB repository, Action, or database.
An in-memory capability catalog is NOT a grant to write anything.
No credentials, real user input, or model responses are printed or persisted.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infrastructure.async_runtime.transport import request_json_with_retry
from pkb.ingestion_gate import InputRecord, ProposedClaim, Route, assess
from pkb.write_service import literal_gate
from ritsuko.application.observation_sources import verified_source_observation
from ritsuko.magi.goal_contract import validate_goal_turn
from ritsuko.magi.goal_decision import evaluate_goal
from ritsuko.magi.runtime_context import create_runtime_context
import ritsuko.magi.async_execution as transport


ENTITY_ID = "f1c70000-0000-4000-8000-000000000001"
ENTITY_NAME = "架空GPU"
VALUE = "RTX-SYNTH-400"
TEXT = f"{ENTITY_NAME}の機種は{VALUE}。"
CASES = ("with-entity", "without-entity", "uncertain")


def check_endpoint(endpoint: str) -> None:
    """Refuse remote LLM endpoints, credentials, URL tricks and other ports."""
    url = urlsplit(endpoint)
    if (
        url.scheme != "http"
        or url.hostname not in {"127.0.0.1", "localhost"}
        or url.port != 11434
        or url.path not in ("", "/")
        or url.username is not None
        or url.password is not None
        or url.query or url.fragment
    ):
        raise ValueError("only local HTTP Ollama port 11434 is permitted")


def synthetic_observation() -> dict:
    """A fictional PKB-resolved entity, never fetched from an actual DB."""
    obs = verified_source_observation(
        {
            "source": "pkb", "status": "ok", "operation": "search",
            "verified_by": "deterministic_pkb_query",
            "answer": "架空GPUという既存Entityだけ確認。機種Claimはまだ0件です。",
            "confidentiality": "private",
            "result": {
                "result_kind": "claims", "total": 0, "items": [],
                "resolved_entity": {"id": ENTITY_ID, "name": ENTITY_NAME},
            },
            "source_metadata": {
                "resolved_entity": {"id": ENTITY_ID, "name": ENTITY_NAME},
            },
        },
        {"source": "pkb", "request_ids": ["synthetic-read-request"]},
    )
    if obs is None or obs.get("resolved_entity", {}).get("id") != ENTITY_ID:
        raise AssertionError("synthetic_observation_contract_broken")
    obs["result_id"] = "synthetic-read-result"
    obs["action_id"] = "synthetic-read-action"
    return obs


def synthetic_session(model: str, endpoint: str, timeout: int, case: str) -> dict:
    user_text = (TEXT if case != "uncertain" else
                 f"{ENTITY_NAME}の機種は{VALUE}かもしれない。")
    return {
        "task_id": str(UUID("f1c70000-0000-4000-8000-000000000002")),
        "user_raw": user_text,
        "status": "running",
        "next_step": "evaluate_goal",
        "turn_limit": 6,
        "turns": [],
        "observations": [] if case == "without-entity" else [synthetic_observation()],
        "member_specs": [{
            "name": "MELCHIOR", "provider": "ollama",
            "model": model, "endpoint": endpoint,
            "enabled": True, "weight": 1.0,
            "timeout_seconds": timeout, "retry_within_turn": False,
            # Keep a bounded test context; do not change the user’s MAGI profile.
            "context_window_tokens": 8192, "ollama_num_predict": 2048,
        }],
        "runtime_context": create_runtime_context(
            timezone_name="Asia/Tokyo", locale="ja-JP",
        ),
        "pkb_claim_write_enabled": True,
    }


def evaluate_proposal(case: str, session: dict, decision: dict | None) -> tuple[bool, str]:
    """Assess model's *proposal*; do not execute or store the proposed action."""
    if decision is None:
        statuses = [str(m.get("status") or "unknown")
                    for m in (session["turns"][-1].get("member_results") or [])] if session["turns"] else []
        return False, "no_valid_goal_proposal:" + ",".join(statuses)
    errors = validate_goal_turn(decision)
    if errors:
        return False, "schema_invalid:" + ",".join(errors)
    op = decision.get("operation_request")
    operation_id = op.get("operation_id") if isinstance(op, dict) else None
    if case == "without-entity":
        return (operation_id == "pkb.search" and decision.get("next") == "invoke",
                "expected_pkb_search_before_any_write")
    if case == "uncertain":
        safe = not (decision.get("next") == "invoke" and
                    operation_id == "pkb.register_claim")
        return safe, "uncertain_claim_must_not_be_written"
    if decision.get("next") != "invoke" or operation_id != "pkb.register_claim":
        return False, "expected_structured_claim_proposal"
    args = op.get("arguments") or {}
    if any(args.get(k) != v for k, v in {
        "entity_id": ENTITY_ID, "entity_mention": ENTITY_NAME,
        "predicate": "model", "value": VALUE,
    }.items()):
        return False, "wrong_entity_or_attribute"
    try:
        start, end = args["evidence_start"], args["evidence_end"]
        user_raw = session["user_raw"]
        claim = ProposedClaim(
            entity_key=ENTITY_ID, entity_mention=ENTITY_NAME,
            predicate="model", value=VALUE,
            evidence_start=start, evidence_end=end,
            evidence_quote=user_raw[start:end],
        )
        now = datetime.now(timezone.utc)
        record = InputRecord(
            input_id="synthetic-readonly-probe", source_kind="user_statement",
            source_ref="local://synthetic-probe/not-persisted",
            text=user_raw, recorded_at=now, occurred_at=now,
            confidentiality="private",
        )
        assessed = assess(record, claim, aliases={ENTITY_ID: {ENTITY_NAME}})
        if assessed.route != Route.AUTO_CANDIDATE:
            return False, "pkb_grounding_failed:" + assessed.reason
        issue = literal_gate(record, claim)
        if issue:
            return False, "pkb_literal_gate:" + issue
    except (TypeError, KeyError, ValueError, IndexError):
        return False, "invalid_evidence_span"
    return True, "PKB_schema_and_literal_gates_passed"


def invalid_proposal_diagnostics(session: dict) -> list[str]:
    """Print only bounded provider/contract error codes, never response contents.

    The actual model response and request envelope may contain private facts
    in normal Task use; this probe still never prints or persists their text.
    """
    latest = (session.get("turns") or [])
    if not latest:
        return ["diagnostic: no_magi_turn"]
    lines = []
    for item in latest[-1].get("member_results") or []:
        # Show only known action enums and field presence, never raw model text.
        response = item.get("response")
        if isinstance(response, dict):
            next_step = response.get("next")
            allowed_steps = {"invoke", "respond", "ask_user", "wait", "unable", "request_read"}
            safe_step = next_step if isinstance(next_step, str) and next_step in allowed_steps else "unrecognized"
            request = response.get("operation_request")
            operation = request.get("operation_id") if isinstance(request, dict) else None
            from ritsuko.application.observation_sources import READ_OPERATIONS
            safe_ops = {*READ_OPERATIONS, "pkb.register_claim"}
            safe_op = operation if isinstance(operation, str) and operation in safe_ops else "none_or_unrecognized"
            lines.append("diagnostic: next=" + safe_step)
            lines.append("diagnostic: operation_id=" + safe_op)
            lines.append("diagnostic: answer_present=" + str(response.get("answer") is not None).lower())
        status = str(item.get("status") or "unknown")
        if status not in {"ok", "invalid", "unavailable", "timeout", "error"}:
            status = "other"
        lines.append("diagnostic: member_status=" + status)
        errors = []
        for value in item.get("errors") or []:
            code = str(value)
            if (len(code) <= 96 and code
                    and all(ch.isascii() and
                            (ch.isalnum() or ch in "_:-")
                            for ch in code)):
                errors.append(code)
            else:
                errors.append("unclassified_error")
        lines.append("diagnostic: error_codes=" + (",".join(errors[:12]) or "none"))
        diagnostics = item.get("diagnostic") or {}
        for key in ("done_reason", "content_length", "thinking_length",
                    "eval_count", "num_predict", "attempt_count"):
            value = diagnostics.get(key)
            if key == "done_reason":
                if not isinstance(value, str) or value not in {
                    "stop", "length", "unload", "load", "error",
                }:
                    continue
            elif type(value) is not int or not 0 <= value < 10000000:
                continue
            lines.append(f"diagnostic: {key}={value}")
    return lines or ["diagnostic: no_member_results"]


async def run_live(*, model: str, endpoint: str, timeout: int, case: str) -> int:
    """Send exactly one local model request, then score it without side effects."""
    state = synthetic_session(model, endpoint, timeout, case)
    print(f"Case: {case}; model: {model}; destination: local Ollama only")
    print("DB/Task/PKB writes: disabled; fictional input only; requests: 1")
    transport.request_json_with_retry = request_json_with_retry
    judgment = await evaluate_goal(state, timeout=timeout)
    passed, reason = evaluate_proposal(case, state, judgment)
    print(("PASS: " if passed else "NOT PASS: ") + reason)
    if not passed:
        for line in invalid_proposal_diagnostics(state):
            print(line)
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-ollama", action="store_true",
                        help="Permit exactly one local Ollama inference on fictional data")
    parser.add_argument("--model", default="gemma4:12b")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--case", choices=CASES, default="with-entity")
    args = parser.parse_args()
    if not args.live_ollama:
        print("No LLM calls were made. Pass --live-ollama to run one local inference.")
        return 2
    try:
        check_endpoint(args.endpoint)
        if not args.model or len(args.model) > 100 or not 10 <= args.timeout <= 300:
            raise ValueError("invalid_model_or_timeout")
        return asyncio.run(run_live(
            model=args.model, endpoint=args.endpoint,
            timeout=args.timeout, case=args.case,
        ))
    except Exception as exc:
        # Do not expose raw provider errors, file content, or environment values.
        print("FAIL: " + type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
