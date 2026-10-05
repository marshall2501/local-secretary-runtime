"""Concrete PostgreSQL implementation of the PKB persistence port."""
from __future__ import annotations

from uuid import UUID

from config.runtime_database import source_ref as build_source_ref
from infrastructure.postgres import pkb_correction_repository
from infrastructure.postgres import pkb_entity_repository
from infrastructure.postgres import pkb_memory_repository
from infrastructure.postgres import pkb_pending_repository
from infrastructure.postgres import pkb_query_repository
from infrastructure.postgres import pkb_write_repository


class PostgresPkbRepository:
    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    def source_ref(self, prefix: str, identifier: str) -> str:
        with self._connection_factory() as db:
            return build_source_ref(db, prefix, identifier)

    def list_entities(self) -> list[dict]:
        with self._connection_factory() as db:
            return pkb_entity_repository.list_entities(db)

    def load_entity_detail(self, entity_id: str) -> dict | None:
        with self._connection_factory() as db:
            return pkb_entity_repository.load_entity_detail(db, entity_id)

    def list_components(self, parent_entity_id: UUID) -> list[dict]:
        with self._connection_factory() as db:
            return pkb_entity_repository.list_components(db, parent_entity_id)

    def resolve_component_reference(
        self, parent_name: str, role_token: str
    ) -> dict | None:
        with self._connection_factory() as db:
            return pkb_entity_repository.resolve_component_reference(
                db, parent_name, role_token
            )

    def authoritative_entity_aliases(self) -> dict[str, set[str]]:
        with self._connection_factory() as db, db.cursor() as cur:
            return pkb_entity_repository.authoritative_entity_aliases(cur)

    def load_catalog(self) -> list[dict]:
        with self._connection_factory() as db:
            return pkb_entity_repository.load_catalog(db)

    def create_relation(self, **kwargs):
        with self._connection_factory() as db:
            return pkb_entity_repository.create_relation(db, **kwargs)

    def write_one(self, record, claim):
        with self._connection_factory() as db:
            return pkb_write_repository.write_one(db, record, claim)

    def correct_entity(self, record, claim):
        with self._connection_factory() as db:
            return pkb_correction_repository.correct_entity(db, record, claim)

    def find_correction_targets(
        self, *, old_entity_id: str, predicate: str, value: str
    ) -> list[tuple]:
        with self._connection_factory() as db:
            return pkb_correction_repository.find_correction_targets(
                db,
                old_entity_id=old_entity_id,
                predicate=predicate,
                value=value,
            )

    def enqueue_pending(self, **kwargs):
        with self._connection_factory() as db:
            return pkb_pending_repository.enqueue(db, **kwargs)

    def list_pending(self, limit: int = 50) -> list[dict]:
        with self._connection_factory() as db:
            return pkb_pending_repository.list_pending(db, limit)

    def accept_pending(self, pending_id: str):
        with self._connection_factory() as db:
            return pkb_pending_repository.accept_pending(db, pending_id)

    def review_pending(self, pending_id: str, decision: str):
        with self._connection_factory() as db:
            return pkb_pending_repository.review_pending(db, pending_id, decision)

    def list_reviewed(self, limit: int = 20) -> list[dict]:
        with self._connection_factory() as db:
            return pkb_pending_repository.list_reviewed(db, limit)

    def query_claims(self, query):
        with self._connection_factory() as db:
            return pkb_query_repository.query_claims(db, query)

    def write_intake(self, intake, *, extractor):
        with self._connection_factory() as db:
            return pkb_memory_repository.write_intake(
                db, intake, extractor=extractor
            )
