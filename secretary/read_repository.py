"""PostgreSQL-backed read repository shared by transport adapters."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID


MEMORY_KINDS = ("claim", "issue", "hypothesis", "source")


def memory_search_sql(include_history: bool) -> str:
    """Build the fixed SQL relation used for deterministic memory enumeration."""
    claim_relation = "secretary.claims" if include_history else "secretary.current_claims"
    return f"""
        WITH memory_records AS (
          SELECT c.id, 'claim'::text AS kind, e.domain, e.name AS entity_name,
                 c.predicate AS title, c.value::text AS value_text,
                 c.evidence AS evidence, c.verification_status AS state,
                 s.id AS source_id, s.citation AS source_citation, s.uri AS source_uri,
                 c.recorded_at AS recorded_at
          FROM {claim_relation} c
          JOIN secretary.entities e ON e.id=c.entity_id
          JOIN secretary.sources s ON s.id=c.source_id
          UNION ALL
          SELECT i.id, 'issue', e.domain, e.name,
                 i.description, NULL::text, NULL::text, i.status,
                 s.id, s.citation, s.uri, i.recorded_at
          FROM secretary.issues i
          LEFT JOIN secretary.entities e ON e.id=i.entity_id
          JOIN secretary.sources s ON s.id=i.source_id
          UNION ALL
          SELECT h.id, 'hypothesis', e.domain, e.name,
                 h.statement, NULL::text, h.evidence, h.status,
                 s.id, s.citation, s.uri, h.recorded_at
          FROM secretary.hypotheses h
          JOIN secretary.issues i ON i.id=h.issue_id
          LEFT JOIN secretary.entities e ON e.id=i.entity_id
          JOIN secretary.sources s ON s.id=h.source_id
          UNION ALL
          SELECT s.id, 'source', NULL::text, NULL::text,
                 s.citation, NULL::text, NULL::text, s.source_type,
                 s.id, s.citation, s.uri, s.recorded_at
          FROM secretary.sources s
        )
        SELECT * FROM memory_records
        WHERE (%s::text IS NULL OR domain=%s)
          AND (%s::text IS NULL OR kind=%s)
          AND (%s::text IS NULL OR
               strpos(lower(concat_ws(' ', entity_name, title, value_text,
                                      evidence, source_citation, source_uri)),
                      lower(%s)) > 0)
    """


class PostgresReadRepository:
    """Own SQL/data-access details; callers supply the established connection factory."""

    def __init__(self, connect: Callable[[], Any]):
        self._connect = connect

    def list_entities(self, domain: str | None, limit: int):
        with self._connect() as db:
            with db.cursor() as cur:
                cur.execute(
                    """SELECT id, name, entity_type, domain, created_at, retired_at
                       FROM secretary.entities
                       WHERE (%s::text IS NULL OR domain = %s)
                       ORDER BY created_at DESC, id DESC LIMIT %s""",
                    (domain, domain, limit),
                )
                return cur.fetchall()

    def list_current_claims(self, entity_id: UUID, verified_only: bool, limit: int):
        with self._connect() as db:
            with db.cursor() as cur:
                cur.execute(
                    """SELECT id, entity_id, source_id, claim_type, predicate, value,
                              evidence, origin, verification_status, valid_from,
                              valid_to, recorded_at
                       FROM secretary.current_claims
                       WHERE entity_id = %s
                         AND (NOT %s OR verification_status = 'verified')
                       ORDER BY recorded_at DESC, id DESC LIMIT %s""",
                    (entity_id, verified_only, limit),
                )
                return cur.fetchall()

    def list_tasks(self, task_status: str | None, limit: int):
        with self._connect() as db:
            with db.cursor() as cur:
                cur.execute(
                    """SELECT id, request, requested_by, domain, completion_criteria,
                              status, due_at, next_run_at, checkpoint, revision,
                              created_at, updated_at, completed_at
                       FROM secretary.tasks
                       WHERE (%s::text IS NULL OR status = %s)
                       ORDER BY created_at DESC, id DESC LIMIT %s""",
                    (task_status, task_status, limit),
                )
                return cur.fetchall()

    def search_memory(
        self, q: str | None, domain: str | None, kind: str | None,
        include_history: bool, limit: int, offset: int,
    ):
        filters = (domain, domain, kind, kind, q, q)
        matching_sql = memory_search_sql(include_history)
        with self._connect() as db:
            with db.cursor() as cur:
                cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                cur.execute(
                    f"SELECT count(*) AS total FROM ({matching_sql}) AS matching", filters
                )
                total = cur.fetchone()["total"]
                cur.execute(
                    f"""SELECT * FROM ({matching_sql}) AS matching
                        ORDER BY recorded_at DESC, kind ASC, id DESC
                        LIMIT %s OFFSET %s""",
                    (*filters, limit, offset),
                )
                items = cur.fetchall()
        return total, items

    def search_experience(
        self, domain: str | None, entity_name: str | None, limit: int, offset: int,
    ):
        where = """
            FROM secretary.actions a
            JOIN secretary.tasks t ON t.id = a.task_id
            LEFT JOIN secretary.entities e ON e.id = t.entity_id
            LEFT JOIN secretary.results r ON r.action_id = a.id
            LEFT JOIN secretary.sources s ON s.id = r.source_id
            WHERE (%s::text IS NULL OR t.domain = %s)
              AND (%s::text IS NULL OR e.name = %s)
        """
        filters = (domain, domain, entity_name, entity_name)
        with self._connect() as db:
            with db.cursor() as cur:
                cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                cur.execute("SELECT count(*) AS total " + where, filters)
                total = cur.fetchone()["total"]
                cur.execute(
                    """SELECT a.id AS action_id, t.id AS task_id,
                              t.domain, t.entity_id, e.name AS entity_name,
                              t.request AS task_request, t.status AS task_status,
                              a.tool, a.operation, a.status AS action_status,
                              a.parameters, a.started_at, a.finished_at,
                              r.id AS result_id, r.outcome, r.summary, r.evidence,
                              r.recorded_at, s.citation AS source_citation
                    """ + where + """
                    ORDER BY a.started_at DESC NULLS LAST, a.id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (*filters, limit, offset),
                )
                items = cur.fetchall()
        return total, items
