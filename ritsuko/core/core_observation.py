"""Bounded observation pack shared by MELCHIOR baseline and CASPER Advisor.

The pack contains observations, not decisions. It is deliberately built from
existing read-only PKB helpers so the Core does not grow another semantic router
just to prepare LLM context.
"""
from __future__ import annotations

from typing import Callable


OBSERVATION_PACK_VERSION = "magi_observation_v1"
MAX_MATCHED_ENTITIES = 4
MAX_CURRENT_FACTS_PER_ENTITY = 12
MAX_RELATIONS_PER_ENTITY = 12
MAX_COMPONENTS_PER_ENTITY = 12


def _entity_identity(row: dict) -> dict:
    return {
        "id": str(row.get("id") or ""),
        "name": row.get("name"),
        "domain": row.get("domain"),
        "entity_type": row.get("entity_type"),
    }


def build_observation_pack(
    request: str,
    entities: dict[str, dict],
    *,
    detail_lookup: Callable[[str], dict | None],
    components_lookup: Callable[[str], list[dict]],
) -> dict:
    """Build a bounded, mechanically selected observation snapshot.

    Selection is intentionally shallow: active Entity names literally mentioned
    in the request are included, then their already-modelled current facts,
    relations and components are exposed. The builder does not choose a
    capability or decide whether the information is sufficient.
    """
    ordered = sorted(entities.items(), key=lambda item: len(item[0]), reverse=True)
    mentioned = [
        row for name, row in ordered
        if name and name in request
    ][:MAX_MATCHED_ENTITIES]

    matched_entities: list[dict] = []
    current_facts: list[dict] = []
    relations: list[dict] = []
    components: list[dict] = []

    for row in mentioned:
        identity = _entity_identity(row)
        matched_entities.append(identity)
        entity_id = identity["id"]
        if not entity_id:
            continue

        detail = detail_lookup(entity_id) or {}
        for item in (detail.get("current") or [])[:MAX_CURRENT_FACTS_PER_ENTITY]:
            current_facts.append({
                "entity_id": entity_id,
                "entity_name": identity["name"],
                "predicate": item.get("predicate"),
                "value": item.get("value"),
                "semantic_kind": item.get("semantic_kind"),
                "verification_status": item.get("verification_status"),
                "source_uri": item.get("source_uri"),
            })

        for item in (detail.get("relations") or [])[:MAX_RELATIONS_PER_ENTITY]:
            if item.get("valid_to") is not None:
                continue
            relations.append({
                "entity_id": entity_id,
                "entity_name": identity["name"],
                "direction": item.get("direction"),
                "predicate": item.get("predicate"),
                "relation_role": item.get("relation_role"),
                "other_entity_id": item.get("other_entity_id"),
                "other_entity_name": item.get("other_entity_name"),
                "other_entity_type": item.get("other_entity_type"),
            })

        for item in components_lookup(entity_id)[:MAX_COMPONENTS_PER_ENTITY]:
            components.append({
                "parent_entity_id": entity_id,
                "parent_entity_name": identity["name"],
                "component_id": str(item.get("component_id") or ""),
                "component_name": item.get("component_name"),
                "component_type": item.get("component_type"),
                "relation_role": item.get("relation_role"),
                "current_driver": item.get("current_driver"),
            })

    return {
        "version": OBSERVATION_PACK_VERSION,
        "request": request,
        "selection_policy": "literal_active_entity_match_then_existing_relations",
        "matched_entities": matched_entities,
        "current_facts": current_facts,
        "relations": relations,
        "components": components,
        "data_sources": {
            "pkb": {
                "available": True,
                "description": "Structured Entity/Relation/Claim data in local PostgreSQL.",
            },
            "finance": {
                "available": True,
                "description": "Imported household-finance data can be aggregated by finance_read.",
            },
            "web": {
                "available": True,
                "description": "Bounded read-only public Web research is available.",
            },
        },
        "bounds": {
            "matched_entities": MAX_MATCHED_ENTITIES,
            "current_facts_per_entity": MAX_CURRENT_FACTS_PER_ENTITY,
            "relations_per_entity": MAX_RELATIONS_PER_ENTITY,
            "components_per_entity": MAX_COMPONENTS_PER_ENTITY,
        },
    }
