"""Checked Memory Intake write use case for the standalone PKB."""
from __future__ import annotations

from pkb.memory_intake import write_intake


def register_memory_intake(db, intake) -> dict:
    with db.cursor() as cur:
        cur.execute("SELECT to_regclass('secretary.pkb_memory_intakes')")
        if cur.fetchone()[0] is None:
            raise ValueError("Memory Intake用のproduction schemaが未適用です。")
    return write_intake(db, intake)
