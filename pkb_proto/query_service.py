"""Read-only SQL-first claim search for the isolated PKB prototype.

The two clocks are independent:
* effective_at: when the described event/fact applies.
* known_at: what the system had recorded by this point in time.

Historical event predicates such as driver_updated are not inferred current
attributes. No semantic similarity, LLM-generated SQL, or top-k approximation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

WRITER = "secretary_pkb_proto_writer_20260927"
DBNAME = "secretary_pkb_proto_20260927"


@dataclass(frozen=True)
class ClaimQuery:
    entity_id: UUID | None = None
    domain: str | None = None
    predicate: str | None = None
    effective_from: datetime | None = None
    effective_before: datetime | None = None
    effective_at: datetime | None = None
    known_at: datetime | None = None
    include_history: bool = False
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True)
class ClaimPage:
    total: int
    items: tuple[dict, ...]
    limit: int
    offset: int
    known_at: datetime
    effective_at: datetime | None
    include_history: bool
    status: str = "ok"  # failures raise exceptions; zero results are not failures


def validate(query: ClaimQuery) -> None:
    if query.entity_id is not None and not isinstance(query.entity_id, UUID):
        raise ValueError("entity_id must be a UUID, not a model-generated name")
    if query.domain is not None and (not query.domain.strip() or len(query.domain) > 200):
        raise ValueError("invalid domain")
    if query.predicate is not None and (not query.predicate.strip() or len(query.predicate) > 200):
        raise ValueError("invalid predicate")
    if not isinstance(query.include_history, bool):
        raise ValueError("include_history must be explicit boolean")
    if type(query.limit) is not int or not 1 <= query.limit <= 100:
        raise ValueError("limit must be 1..100")
    if type(query.offset) is not int or not 0 <= query.offset <= 1000000:
        raise ValueError("invalid offset")
    for timestamp in (
        query.effective_from, query.effective_before, query.effective_at, query.known_at
    ):
        if timestamp is not None and (
            not isinstance(timestamp, datetime) or timestamp.utcoffset() is None
        ):
            raise ValueError("all supplied timestamps must contain timezones")
    if query.effective_from and query.effective_before:
        if query.effective_from >= query.effective_before:
            raise ValueError("effective_from must precede effective_before")


def _sql() -> str:
    # All dynamic predicates are bound SQL parameters. The sole history switch
    # is a bound boolean; no table names or SQL fragments come from the caller.
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
    info = db.info
    if (
        (info.dbname or "") != DBNAME
        or (info.host or "") not in ("localhost", "127.0.0.1", "::1")
        or (info.user or "") != WRITER
    ):
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
    # One request = one consistent DB snapshot for count and page. Subsequent
    # page requests can observe later writes; do not claim export-wide snapshot.
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
        total=total, items=items, limit=query.limit, offset=query.offset,
        known_at=known, effective_at=query.effective_at,
        include_history=query.include_history,
    )
