"""Pure Memory Intake application entry delegated through a persistence port."""
from __future__ import annotations

from .memory_extractor import extract


def write_intake(repository, intake, *, extractor=extract):
    intake.validate()
    return repository.write_intake(intake, extractor=extractor)
