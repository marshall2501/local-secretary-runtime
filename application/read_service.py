"""Transport-independent read application service for Secretary data."""
from __future__ import annotations

from typing import Protocol
from uuid import UUID


class ReadRepository(Protocol):
    def list_entities(self, domain: str | None, limit: int): ...
    def list_current_claims(self, entity_id: UUID, verified_only: bool, limit: int): ...
    def list_tasks(self, task_status: str | None, limit: int): ...
    def search_memory(self, q: str | None, domain: str | None, kind: str | None,
                      include_history: bool, limit: int, offset: int): ...
    def search_experience(self, domain: str | None, entity_name: str | None,
                          limit: int, offset: int): ...


class ReadService:
    """Common use cases consumed by REST today and MCP/other adapters later."""

    def __init__(self, repository: ReadRepository):
        self.repository = repository

    @staticmethod
    def _clean(value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    def list_entities(self, domain: str | None = None, limit: int = 50):
        return self.repository.list_entities(self._clean(domain), limit)

    def list_current_claims(
        self, entity_id: UUID, verified_only: bool = False, limit: int = 100,
    ):
        return self.repository.list_current_claims(entity_id, verified_only, limit)

    def list_tasks(self, task_status: str | None = None, limit: int = 50):
        return self.repository.list_tasks(task_status, limit)

    def search_memory(
        self, q: str | None = None, domain: str | None = None,
        kind: str | None = None, include_history: bool = False,
        limit: int = 50, offset: int = 0,
    ):
        q = self._clean(q)
        domain = self._clean(domain)
        total, items = self.repository.search_memory(
            q, domain, kind, include_history, limit, offset
        )
        return {
            "total": total, "limit": limit, "offset": offset,
            "include_history": include_history, "items": items,
        }

    def search_experience(
        self, domain: str | None = None, entity_name: str | None = None,
        limit: int = 20, offset: int = 0,
    ):
        domain = self._clean(domain)
        entity_name = self._clean(entity_name)
        if entity_name and not domain:
            raise ValueError("entity_name requires domain")
        total, items = self.repository.search_experience(
            domain, entity_name, limit, offset
        )
        return {
            "total": total, "limit": limit, "offset": offset,
            "scope": "linked entity only" if entity_name else
                     "domain-wide including unlinked tasks",
            "items": items,
        }
