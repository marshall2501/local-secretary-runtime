"""Explicit, bounded synthetic MAGI cloud acceptance probe.

Requires --live-synthetic. Only the active LAN Policy and MAGI/Connection
settings are read from local PostgreSQL. No PKB/Task/Finance records are read,
and no DB writes or Ollama calls are performed. Exactly one HTTP attempt is
allowed per configured OpenAI/Gemini member, with no retry, through the real
MAGI async panel path. Never print secrets, request envelopes, model responses,
or exception text from external providers.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import anyio
import psycopg

from scripts.db.probe_runtime_policy_readonly import (
    configured_port, read_runtime_secret,
)
from infrastructure.postgres.runtime_policy_repository import (
    PostgresRuntimePolicyRepository,
)
from infrastructure.postgres.magi_settings_repository import (
    PostgresMagiSettingsRepository,
)
from infrastructure.postgres.service_connection_repository import (
    PostgresServiceConnectionRepository,
)
from infrastructure.async_runtime.transport import request_json_with_retry
from integrations.connections.credential_resolver import (
    register_connection_credential_loader,
)
from ritsuko.application.context_policy import restrict_members, endpoint_scope
from ritsuko.magi.dialogue import PROMPT_VERSION
import ritsuko.magi.async_execution as magi

APPROVED_ENDPOINTS = {
    "openai": ("api.openai.com", "/v1"),
    "gemini": ("generativelanguage.googleapis.com", "/v1beta"),
}
EXPECTED_NAMES = {"openai": "CASPER", "gemini": "BALTHASAR"}
REQUEST_TIMEOUT_SECONDS = 60


class ProbeError(RuntimeError):
    """A sanitized error safe to display without leaking database details."""


def validate_endpoint(spec: dict) -> None:
    provider = spec["provider"]
    endpoint = str(spec.get("endpoint") or "")
    parsed = urlsplit(endpoint)
    expected_host, expected_path = APPROVED_ENDPOINTS[provider]
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.path.rstrip("/") != expected_path
        or parsed.username is not None or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.query or parsed.fragment
    ):
        raise ProbeError("configured endpoint is outside approved HTTPS API")


def select_cloud_specs(member_specs: list[dict], policy: dict) -> tuple[list[dict], dict]:
    if policy.get("profile_name") != "verification-lan" or policy.get("exposure") != "lan":
        raise ProbeError("active LAN Policy is not verification-lan")
    by_provider = {}
    for spec in member_specs:
        provider = spec.get("provider")
        if provider in APPROVED_ENDPOINTS and spec.get("enabled"):
            if provider in by_provider:
                raise ProbeError("duplicate configured cloud provider")
            if spec.get("name") != EXPECTED_NAMES[provider]:
                raise ProbeError("unexpected MAGI member assignment")
            validate_endpoint(spec)
            by_provider[provider] = dict(spec)
    if set(by_provider) != set(APPROVED_ENDPOINTS):
        raise ProbeError("both cloud MAGI members must be configured and enabled")

    # Never read personal PKB or previous Task state, even if the user has
    # real private information stored in the production database.
    session = synthetic_session(member_specs, policy)
    allowed, context_policy = restrict_members(session, policy)
    selected_names = {item["name"] for item in (allowed or [])}
    required = set(EXPECTED_NAMES.values())
    if not required.issubset(selected_names):
        raise ProbeError("current Policy does not allow both cloud members")
    cloud_specs = []
    for provider in ("openai", "gemini"):
        spec = by_provider[provider]
        if endpoint_scope(spec) != "remote":
            raise ProbeError("expected a remote cloud endpoint")
        spec["retry_within_turn"] = False
        spec["retry_http_codes"] = []
        spec["timeout_seconds"] = REQUEST_TIMEOUT_SECONDS
        cloud_specs.append(spec)
    return cloud_specs, context_policy


def synthetic_session(specs: list[dict], policy: dict) -> dict:
    return {
        "task_id": "synthetic-magi-cloud-probe",
        "user_raw": "Fictional connectivity test. Do not use personal information.",
        "prompt_version": PROMPT_VERSION,
        "model": "",
        "status": "running",
        "next_step": "evaluate_observation",
        "turns": [],
        "turn_limit": 1,
        "member_specs": specs,
        "policy_snapshot": policy,
        "runtime_context": None,
        "classification": {
            "category": "INFORMATION",
            "understood_request": "Classify fictional connectivity-test observations",
        },
        "detail": {"state": "NEED_INFORMATION"},
        "pending_requests": [],
        "observations": [
            {"source": "pkb", "verified": True, "confidentiality": "private",
             "text": "FICTIONAL-PRIVATE-PROBE-001"},
            {"source": "finance", "verified": True,
             "confidentiality": "sensitive",
             "text": "FICTIONAL-SENSITIVE-PROBE-002"},
        ],
    }


def readonly_connection_factory():
    port = configured_port(ROOT / ".env.postgres")
    password = read_runtime_secret(
        ROOT / "secrets" / "secretary-daily-runtime-password.txt"
    )

    def connect():
        db = psycopg.connect(
            host="127.0.0.1", port=port, dbname="secretary",
            user="secretary_daily_runtime", password=password,
            connect_timeout=5,
            options="-c default_transaction_read_only=on",
        )
        info = db.info
        if (
            info.dbname != "secretary"
            or info.user != "secretary_daily_runtime"
            or info.host not in {"127.0.0.1", "localhost", "::1"}
        ):
            db.close()
            raise ProbeError("unexpected local database identity")
        try:
            with db.cursor() as cur:
                cur.execute("SHOW transaction_read_only")
                if cur.fetchone()[0] != "on":
                    raise ProbeError("database read-only protection not active")
        except Exception:
            db.close()
            raise
        return db

    return connect


async def run_live_probe() -> bool:
    connect = readonly_connection_factory()
    policy = PostgresRuntimePolicyRepository(connect).load_snapshot("lan")
    specs = PostgresMagiSettingsRepository(connect).load_member_specs()
    selected, context_policy = select_cloud_specs(specs, policy)
    authorized_ids = {str(item["connection_id"]) for item in selected}
    repo = PostgresServiceConnectionRepository(connect)

    def load_credential(connection_id: str) -> str | None:
        if connection_id not in authorized_ids:
            return None
        return repo.get_connection_auth_value(connection_id, "api_key")

    # Preflight DB-authenticated credentials before any external request.
    # No environment fallback: the purpose is validating configured daily
    # MAGI connections, not whichever key an interactive shell may contain.
    for member in selected:
        if not member.get("connection_id") or not load_credential(
            str(member["connection_id"])
        ):
            raise ProbeError("configured cloud MAGI credential is missing")

    print("Active policy: verification-lan")
    print("Cloud members: CASPER (OpenAI), BALTHASAR (Gemini)")
    print("Payload: fictional private/sensitive observations only")
    print("Attempts: one per provider, retries disabled, 60s maximum each")
    sys.stdout.flush()

    register_connection_credential_loader(load_credential)
    magi.request_json_with_retry = request_json_with_retry
    state = synthetic_session(selected, policy)
    try:
        await magi._send_async(
            state,
            "analyze",
            "evaluate_observation",
            "Evaluate only the fictional synthetic observations. "
            "Return a brief structured answer appropriate to the provided schema.",
            timeout=REQUEST_TIMEOUT_SECONDS,
            member_specs_override=selected,
            context_policy=context_policy,
        )
    finally:
        register_connection_credential_loader(None)

    turns = state.get("turns") or []
    if len(turns) != 1:
        raise ProbeError("expected one synthetic MAGI turn")
    results = turns[0].get("member_results") or []
    if len(results) != 2:
        raise ProbeError("expected one result for each cloud member")
    passed = True
    for item in sorted(results, key=lambda entry: entry.get("name") or ""):
        member = item.get("name")
        if member not in set(EXPECTED_NAMES.values()):
            raise ProbeError("unexpected provider response")
        status = str(item.get("status") or "unavailable")
        usage = item.get("diagnostic") or {}
        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
        output_tokens = usage.get("output_tokens", usage.get("candidate_tokens"))
        print(
            f"{member}: {status}; "
            f"input_tokens={input_tokens if isinstance(input_tokens, int) else 'n/a'}; "
            f"output_tokens={output_tokens if isinstance(output_tokens, int) else 'n/a'}"
        )
        passed = passed and status == "ok"
    if passed:
        print("PASS: real HTTPS cloud MAGI requests with fictional context")
    else:
        print("NOT PASS: at least one provider did not return a valid MAGI response")
    print("No PKB read, DB write, or real personal context was used.")
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-synthetic", action="store_true",
        help="Explicitly authorize one real API call each to OpenAI and Gemini",
    )
    args = parser.parse_args()
    if not args.live_synthetic:
        print("No network requests made. Supply --live-synthetic to run.")
        return 2
    try:
        return 0 if anyio.run(run_live_probe) else 1
    except ProbeError as error:
        # Only locally authored errors, never provider/SQL/secret exception text.
        print("FAIL:", error)
    except Exception as error:
        print("FAIL: unexpected", type(error).__name__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
