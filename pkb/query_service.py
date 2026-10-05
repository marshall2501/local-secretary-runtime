"""Pure contracts for SQL-first PKB claim search."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


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
    status: str = "ok"


def validate(query: ClaimQuery) -> None:
    if query.entity_id is not None and not isinstance(query.entity_id, UUID):
        raise ValueError("entity_id must be a UUID, not a model-generated name")
    if query.domain is not None and (
        not query.domain.strip() or len(query.domain) > 200
    ):
        raise ValueError("invalid domain")
    if query.predicate is not None and (
        not query.predicate.strip() or len(query.predicate) > 200
    ):
        raise ValueError("invalid predicate")
    if not isinstance(query.include_history, bool):
        raise ValueError("include_history must be explicit boolean")
    if type(query.limit) is not int or not 1 <= query.limit <= 100:
        raise ValueError("limit must be 1..100")
    if type(query.offset) is not int or not 0 <= query.offset <= 1000000:
        raise ValueError("invalid offset")
    for timestamp in (
        query.effective_from,
        query.effective_before,
        query.effective_at,
        query.known_at,
    ):
        if timestamp is not None and (
            not isinstance(timestamp, datetime) or timestamp.utcoffset() is None
        ):
            raise ValueError("all supplied timestamps must contain timezones")
    if query.effective_from and query.effective_before:
        if query.effective_from >= query.effective_before:
            raise ValueError("effective_from must precede effective_before")


def query_claims(repository, query: ClaimQuery) -> ClaimPage:
    validate(query)
    return repository.query_claims(query)
