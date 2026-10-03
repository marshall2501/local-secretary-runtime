"""Transport-independent PKB candidate intake application service."""
from __future__ import annotations

from decimal import Decimal
from typing import Protocol
from uuid import UUID


class CandidateRepository(Protocol):
    def propose(self, *, actor: str, entity_id: UUID, source_id: UUID,
                claim_type: str, predicate: str, proposed_value: object,
                confidence: Decimal | None, evidence: str,
                extraction_model: str, prompt_version: str) -> dict: ...


class CandidateService:
    def __init__(self, repository: CandidateRepository):
        self._repository = repository

    def propose(self, **kwargs) -> dict:
        return self._repository.propose(**kwargs)
