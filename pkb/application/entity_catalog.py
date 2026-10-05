"""Entity catalog application boundary for standalone PKB onboarding."""
from __future__ import annotations

from typing import Protocol


class EntityCatalogRepository(Protocol):
    def create_or_get(
        self,
        *,
        name: str,
        domain: str,
        entity_type: str,
        actor: str,
    ) -> dict: ...


def _required(value: str, label: str, max_length: int) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{label} is required")
    if len(cleaned) > max_length:
        raise ValueError(f"{label} is too long")
    return cleaned


class EntityCatalogService:
    """User-driven Entity onboarding without model-created identifiers."""

    def __init__(self, repository: EntityCatalogRepository):
        self.repository = repository

    def create(
        self,
        *,
        name: str,
        domain: str,
        entity_type: str,
        actor: str = "local_user",
    ) -> dict:
        return self.repository.create_or_get(
            name=_required(name, "Entity name", 200),
            domain=_required(domain, "Domain", 100),
            entity_type=_required(entity_type, "Entity type", 100),
            actor=_required(actor, "Actor", 100),
        )
