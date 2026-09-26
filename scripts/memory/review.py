"""Trusted, interactive local Memory Review CLI.

This is NOT an HTTP endpoint and must NEVER run with the secretary_api DSN.
AI/API users can only propose candidates; a separate human-operated DB login
reviews each candidate and records memory approval and the resulting claim.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

DECISIONS = {"accepted", "rejected", "needs_edit"}
ALLOWED_SOURCE_TYPES = {"user_statement"}
ALLOWED_CLAIM_TYPES = {"fact", "observation", "attribute", "unconfirmed"}


def checked_dsn() -> str:
    dsn = os.environ.get("LSA_REVIEW_DSN", "")
    if not dsn:
        raise ValueError("Set LSA_REVIEW_DSN for the independent human reviewer.")
    parsed = conninfo_to_dict(dsn)
    if parsed.get("user") in (None, "", "secretary_admin", "postgres", "secretary_api"):
        raise ValueError("Never use the admin or candidate API login for reviews.")
    if parsed.get("host") not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Human review must connect to localhost only.")
    if parsed.get("dbname") != "secretary":
        raise ValueError("Human review accepts only the dedicated secretary DB.")
    return dsn


def connect():
    db = psycopg.connect(checked_dsn(), row_factory=dict_row, connect_timeout=5)
    try:
        with db.cursor() as cur:
            cur.execute(
                """SELECT
                   (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS superuser,
                   pg_has_role(current_user, 'secretary_memory_writer', 'MEMBER') AS memory_writer,
                   pg_has_role(current_user, 'secretary_review_writer', 'MEMBER') AS reviewer,
                   pg_has_role(current_user, 'secretary_audit_writer', 'MEMBER') AS auditor"""
            )
            row = cur.fetchone()
            if (
                row["superuser"] or not row["memory_writer"]
                or not row["reviewer"] or not row["auditor"]
            ):
                raise ValueError("Reviewer login lacks expected restricted roles.")
        return db
    except BaseException:
        db.close()
        raise


def require_text(value: str, label: str) -> str:
    value = value.strip()
    if not value or len(value) > 4000:
        raise ValueError(f"{label} must contain 1 to 4000 characters.")
    return value


def print_json(value) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def add_entity(args) -> None:
    name = require_text(args.name, "Entity name")
    domain = require_text(args.domain, "Domain")
    entity_type = require_text(args.entity_type, "Entity type")
    with connect() as db, db.cursor() as cur:
        cur.execute(
            """SELECT id FROM secretary.entities
               WHERE domain=%s AND entity_type=%s AND lower(name)=lower(%s)
                 AND retired_at IS NULL LIMIT 1""",
            (domain, entity_type, name),
        )
        existing = cur.fetchone()
        if existing:
            print(f"Existing entity (not duplicated): {existing['id']}")
            return
        cur.execute(
            """INSERT INTO secretary.entities (name, domain, entity_type)
               VALUES (%s,%s,%s) RETURNING id""",
            (name, domain, entity_type),
        )
        entity_id = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO secretary.audit_events
               (actor, event_type, object_type, object_id, details)
               VALUES (%s,'memory.entity_created','entity',%s,%s)""",
            (args.reviewer, entity_id, Jsonb({"domain": domain, "entity_type": entity_type})),
        )
    print(f"Created entity: {entity_id}")


def add_source(args) -> None:
    if args.source_type not in ALLOWED_SOURCE_TYPES:
        raise ValueError("Only user_statement metadata is supported in this MVP.")
    citation = require_text(args.citation, "Citation")
    uri = require_text(args.uri, "URI")
    with connect() as db, db.cursor() as cur:
        cur.execute(
            """INSERT INTO secretary.sources
               (source_type, uri, citation, retrieved_at, confidentiality, metadata)
               VALUES (%s,%s,%s,now(),%s,%s)
               RETURNING id""",
            (args.source_type, uri, citation, args.confidentiality,
             Jsonb({"archive_status": "metadata_only_mvp"})),
        )
        source_id = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO secretary.audit_events
               (actor,event_type,object_type,object_id,details)
               VALUES (%s,'memory.source_created','source',%s,%s)""",
            (args.reviewer, source_id, Jsonb({"source_type": args.source_type})),
        )
    print(f"Created source metadata: {source_id}")
    print("WARNING: the original text/file is NOT archived by this MVP.")


def list_pending(args) -> None:
    with connect() as db, db.cursor() as cur:
        cur.execute(
            """SELECT p.id, e.name AS entity, e.domain, p.claim_type,
                      p.predicate, p.proposed_value, p.evidence,
                      p.review_status, p.recorded_at, s.citation
               FROM secretary.pending_claims p
               JOIN secretary.entities e ON e.id=p.entity_id
               JOIN secretary.sources s ON s.id=p.source_id
               WHERE p.review_status = 'pending'
               ORDER BY p.recorded_at, p.id LIMIT %s""",
            (args.limit,),
        )
        print_json(cur.fetchall())


def review_candidate(args, confirm=input) -> None:
    if args.decision not in DECISIONS:
        raise ValueError("Invalid decision.")
    candidate_id = UUID(args.id)
    notes = require_text(args.notes, "Review notes")
    actor = require_text(args.reviewer, "Reviewer")
    # Hold the candidate row lock until confirmation and transaction commit,
    # so two reviewer processes cannot approve the same candidate.
    with connect() as db, db.cursor() as cur:
        cur.execute(
            """SELECT p.*, e.name AS entity_name, e.domain, s.citation,
                      s.uri AS source_uri, s.source_type
               FROM secretary.pending_claims p
               JOIN secretary.entities e ON e.id=p.entity_id
               JOIN secretary.sources s ON s.id=p.source_id
               WHERE p.id=%s FOR UPDATE OF p""",
            (candidate_id,),
        )
        candidate = cur.fetchone()
        if candidate is None:
            raise ValueError("Candidate does not exist.")
        if candidate["review_status"] != "pending":
            raise ValueError("Already reviewed; no duplicate promotion.")
        print_json({
            key: candidate[key] for key in (
                "id", "entity_name", "domain", "source_uri", "source_type",
                "citation", "claim_type", "predicate", "proposed_value",
                "confidence", "evidence", "extraction_model", "prompt_version",
                "recorded_at",
            )
        })
        print(f"Decision: {args.decision}; reviewer: {actor}; notes: {notes}")
        if args.decision == "accepted":
            print("Acceptance records an UNVERIFIED claim. It does not validate truth.")
        phrase = f"CONFIRM {candidate_id}"
        if confirm(f"Type '{phrase}' to commit: ").strip() != phrase:
            print("Cancelled without writing.")
            return

        # Keep the decision, approval (when final), claim and audit atomic.
        cur.execute(
            """UPDATE secretary.pending_claims
               SET review_status=%s, reviewer=%s, reviewer_notes=%s, reviewed_at=now()
               WHERE id=%s AND review_status='pending'""",
            (args.decision, actor, notes, candidate_id),
        )
        if cur.rowcount != 1:
            raise ValueError("Candidate changed concurrently.")
        approval_id = None
        claim_id = None
        if args.decision in ("accepted", "rejected"):
            scope = {
                "entity_id": str(candidate["entity_id"]),
                "source_id": str(candidate["source_id"]),
                "pending_claim_id": str(candidate_id),
                "predicate": candidate["predicate"],
                "memory_only": True,
            }
            cur.execute(
                """INSERT INTO secretary.approvals
                   (approval_kind, pending_claim_id, status, scope,
                    requested_by, decided_by, reason, decided_at, expires_at)
                   VALUES ('memory_write', %s, %s, %s, %s, %s, %s,
                           now(), now()+interval '5 minutes')
                   RETURNING id""",
                (
                    candidate_id,
                    "approved" if args.decision == "accepted" else "rejected",
                    Jsonb(scope), "local_candidate", actor, notes,
                ),
            )
            approval_id = cur.fetchone()["id"]
        if args.decision == "accepted":
            if candidate["claim_type"] not in ALLOWED_CLAIM_TYPES:
                raise ValueError("Unexpected claim type.")
            cur.execute(
                """INSERT INTO secretary.claims
                   (entity_id, source_id, pending_claim_id, claim_type,
                    predicate, value, evidence, origin, verification_status,
                    valid_from)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,'reviewed_extraction',
                           'unverified',now())
                   RETURNING id""",
                (
                    candidate["entity_id"], candidate["source_id"], candidate_id,
                    candidate["claim_type"], candidate["predicate"],
                    Jsonb(candidate["proposed_value"]), candidate["evidence"],
                ),
            )
            claim_id = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO secretary.audit_events
               (actor, event_type, approval_id, object_type, object_id, details)
               VALUES (%s, %s, %s, 'pending_claim', %s, %s)""",
            (
                actor, f"memory.review_{args.decision}", approval_id,
                candidate_id, Jsonb({"claim_id": str(claim_id) if claim_id else None}),
            ),
        )
    print_json({"decision": args.decision, "claim_id": claim_id, "approval_id": approval_id})


def main() -> None:
    parser = argparse.ArgumentParser(description="Local trusted human Memory Review.")
    sub = parser.add_subparsers(dest="command", required=True)
    entity = sub.add_parser("entity", help="Create or find an entity.")
    entity.add_argument("--name", required=True)
    entity.add_argument("--domain", required=True)
    entity.add_argument("--entity-type", required=True)
    entity.add_argument("--reviewer", required=True)
    entity.set_defaults(handler=add_entity)
    source = sub.add_parser("source", help="Record local user-statement source metadata.")
    source.add_argument("--uri", required=True)
    source.add_argument("--citation", required=True)
    source.add_argument("--source-type", choices=sorted(ALLOWED_SOURCE_TYPES),
                        default="user_statement")
    source.add_argument("--confidentiality", choices=["public", "private", "restricted"],
                        default="private")
    source.add_argument("--reviewer", required=True)
    source.set_defaults(handler=add_source)
    pending = sub.add_parser("pending", help="List pending candidates.")
    pending.add_argument("--limit", type=int, choices=range(1, 101), default=20,
                         metavar="[1-100]")
    pending.set_defaults(handler=list_pending)
    review = sub.add_parser("review", help="Review candidate with explicit confirmation.")
    review.add_argument("--id", required=True)
    review.add_argument("--decision", required=True, choices=sorted(DECISIONS))
    review.add_argument("--notes", required=True)
    review.add_argument("--reviewer", required=True)
    review.set_defaults(handler=review_candidate)
    args = parser.parse_args()
    try:
        args.handler(args)
    except (ValueError, psycopg.Error) as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
