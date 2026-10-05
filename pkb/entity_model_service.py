"""Pure PKB Entity model semantics and persistence port delegates."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from .memory_registry import EFFECT_RULES

EVENT_PREDICATES = {
    "driver_updated",
    "servo_updated",
    "os_release_changed",
}
STATE_PREDICATES = {
    "current_driver",
    "current_servo",
    "current_os_release",
    "installed",
}
ATTRIBUTE_PREDICATES = {
    "manufacturer",
    "model",
    "serial_number",
    "purchase_date",
}
RELATION_PREDICATES = {
    "has_component",
    "owned_by",
    "belongs_to_project",
    "uses_account",
}

EVENT_TO_STATE = {key: rule.state_predicate for key, rule in EFFECT_RULES.items()}
STATEFUL_ENTITY_TYPES = {
    "gpu",
    "network_adapter",
    "rc_servo",
}
COMPONENT_ROLE_TOKENS = {
    "GPU": "primary_gpu",
    "NIC": "wired_nic",
}
ROLE_TO_HUMAN_TOKEN = {value: key for key, value in COMPONENT_ROLE_TOKENS.items()}


@dataclass(frozen=True)
class RelationResult:
    status: str
    reason: str
    relation_id: str | None = None


def classify_predicate(predicate: str) -> str | None:
    if predicate in EVENT_PREDICATES:
        return "event"
    if predicate in STATE_PREDICATES:
        return "state"
    if predicate in ATTRIBUTE_PREDICATES:
        return "attribute"
    return None


def create_relation(repository, **kwargs) -> RelationResult:
    return repository.create_relation(**kwargs)


def advance_state_for_event(repository, **kwargs) -> str | None:
    return repository.advance_state_for_event(**kwargs)


def list_components(repository, parent_entity_id: UUID) -> list[dict]:
    return repository.list_components(parent_entity_id)


def resolve_component_reference(repository, parent_name: str, role_token: str) -> dict | None:
    return repository.resolve_component_reference(parent_name, role_token)


def authoritative_entity_aliases(repository) -> dict[str, set[str]]:
    return repository.authoritative_entity_aliases()


def load_entity_detail(repository, entity_id: str) -> dict | None:
    return repository.load_entity_detail(entity_id)
