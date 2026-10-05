"""PostgreSQL adapter for SQL-first PKB claim search."""
from __future__ import annotations

from datetime import datetime, timezone

from config.runtime_database import allowed_daily_connection
from pkb.query_service import ClaimPage, ClaimQuery, validate


def _sql() -> str:
    return """
      WITH candidates AS (
        SELECT c.id, c.entity_id, e.name AS entity_name, e.domain,
               c.source_id, s.source_type, s.uri AS source_uri,
               s.citation AS source_citation,
               c.predicate, c.value, c.claim_type, c.semantic_kind, c.origin,
               c.evidence, c.verification_status AS recorded_status,
               c.valid_from, c.valid_to, c.recorded_at, c.retracted_at,
               c.supersedes_id,
               (c.retracted_at IS NOT NULL AND c.retracted_at <= %(known_at)s
                OR EXISTS (
                   SELECT 1 FROM secretary.claims successor
                   WHERE successor.supersedes_id=c.id
                     AND successor.recorded_at <= %(known_at)s
                )) AS superseded_by_cutoff
        FROM secretary.claims c
        JOIN secretary.entities e ON e.id=c.entity_id
        JOIN secretary.sources s ON s.id=c.source_id
        WHERE c.recorded_at <= %(known_at)s
          AND (%(entity_id)s::uuid IS NULL OR c.entity_id=%(entity_id)s)
          AND (%(domain)s::text IS NULL OR e.domain=%(domain)s)
          AND (%(predicate)s::text IS NULL OR c.predicate=%(predicate)s)
          AND (%(effective_from)s::timestamptz IS NULL OR
               c.valid_from >= %(effective_from)s)
          AND (%(effective_before)s::timestamptz IS NULL OR
               c.valid_from < %(effective_before)s)
          AND (%(effective_at)s::timestamptz IS NULL OR
               (c.valid_from <= %(effective_at)s AND
                (c.valid_to IS NULL OR c.valid_to > %(effective_at)s)))
      )
      SELECT *, CASE WHEN superseded_by_cutoff THEN 'superseded'
                     ELSE 'active' END AS status_at_cutoff
      FROM candidates
      WHERE (%(include_history)s OR NOT superseded_by_cutoff)
    """


def query_claims(db, query: ClaimQuery) -> ClaimPage:
    validate(query)
    if not allowed_daily_connection(db):
        raise ValueError("Refusing non-isolated DB or non-dedicated writer")
    known = query.known_at or datetime.now(timezone.utc)
    args = {
        "entity_id": query.entity_id,
        "domain": query.domain,
        "predicate": query.predicate,
        "effective_from": query.effective_from,
        "effective_before": query.effective_before,
        "effective_at": query.effective_at,
        "known_at": known,
        "include_history": query.include_history,
    }
    sql = _sql()
    with db.transaction(), db.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cur.execute("SELECT count(*) FROM (" + sql + ") matching", args)
        total = cur.fetchone()[0]
        cur.execute(
            "SELECT * FROM (" + sql + """) matching
               ORDER BY valid_from ASC, recorded_at ASC, id ASC
               LIMIT %(limit)s OFFSET %(offset)s""",
            {**args, "limit": query.limit, "offset": query.offset},
        )
        columns = [x.name for x in cur.description]
        items = tuple(dict(zip(columns, row)) for row in cur.fetchall())
    return ClaimPage(
        total=total,
        items=items,
        limit=query.limit,
        offset=query.offset,
        known_at=known,
        effective_at=query.effective_at,
        include_history=query.include_history,
    )
