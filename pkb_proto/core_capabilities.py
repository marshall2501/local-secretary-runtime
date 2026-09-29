"""Shared capability registry for Secretary Core and MAGI members.

Capabilities are facts about what the runtime can do.  They are not routing
decisions.  MELCHIOR, CASPER and the Core synthesizer must therefore read the
same registry instead of carrying separate copies.
"""
from __future__ import annotations


CAPABILITY_REGISTRY = {
    "pkb_search": {
        "description": "Search the user's local PKB for PC/RC configuration, state and history.",
        "risk": "local_read_only",
        "permissions": ["pkb_read"],
        "scope_rank": 10,
        "safe_prefix": None,
    },
    "finance_read": {
        "description": "Read and aggregate already imported household-finance data.",
        "risk": "local_read_only",
        "permissions": ["finance_read"],
        "scope_rank": 10,
        "safe_prefix": None,
    },
    "web_research": {
        "description": "Perform bounded read-only public Web research.",
        "risk": "external_read",
        "permissions": ["web_research"],
        "scope_rank": 20,
        "safe_prefix": None,
    },
    "pkb_web_compare": {
        "description": "Use both PKB current state and bounded Web research in one Task for comparison.",
        "risk": "external_read",
        "permissions": ["pkb_read", "web_research"],
        "scope_rank": 30,
        "safe_prefix": "pkb_search",
    },
}


def capability_allowed(name: str, permissions: dict[str, bool]) -> bool:
    spec = CAPABILITY_REGISTRY.get(name)
    if not spec:
        return False
    return all(bool(permissions.get(permission)) for permission in spec["permissions"])


def safe_prefix(name: str) -> str | None:
    spec = CAPABILITY_REGISTRY.get(name) or {}
    prefix = spec.get("safe_prefix")
    return prefix if isinstance(prefix, str) and prefix else None


def scope_rank(name: str) -> int:
    spec = CAPABILITY_REGISTRY.get(name) or {}
    return int(spec.get("scope_rank") or 10_000)
