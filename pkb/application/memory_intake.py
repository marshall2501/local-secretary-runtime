"""Checked Memory Intake write use case for the standalone PKB."""
from __future__ import annotations

from pkb.memory_intake import write_intake


def register_memory_intake(repository, intake) -> dict:
    return write_intake(repository, intake)
