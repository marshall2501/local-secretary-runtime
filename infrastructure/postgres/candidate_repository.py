"""PostgreSQL adapter for restricted pending-claim proposals."""
from __future__ import annotations

from collections.abc import Callable

from psycopg.types.json import Jsonb


class PostgresCandidateRepository:
    def __init__(self, connect: Callable):
        self._connect = connect

    def propose(self, **candidate) -> dict:
        actor = candidate.pop("actor")
        with self._connect() as db:
            with db.cursor() as cur:
                cur.execute(
                    """INSERT INTO secretary.pending_claims
                       (entity_id, source_id, claim_type, predicate, proposed_value,
                        confidence, evidence, extraction_model, prompt_version)
                       VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                       RETURNING id, review_status, recorded_at""",
                    (
                        candidate["entity_id"], candidate["source_id"],
                        candidate["claim_type"], candidate["predicate"],
                        Jsonb(candidate["proposed_value"]), candidate["confidence"],
                        candidate["evidence"], candidate["extraction_model"],
                        candidate["prompt_version"],
                    ),
                )
                created = cur.fetchone()
                cur.execute(
                    """INSERT INTO secretary.audit_events
                       (actor, event_type, object_type, object_id)
                       VALUES (%s, 'memory.candidate_proposed', 'pending_claim', %s)""",
                    (actor, created["id"]),
                )
                return created
