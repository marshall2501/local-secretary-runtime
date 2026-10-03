"""Transport-independent RITSUKO Task application service."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID


PROTOTYPE_STEPS = (
    "Recall: inspect existing memory and previous actions",
    "Plan: choose a safe read-only diagnostic",
    "Execute: run a simulated read-only diagnostic",
    "Verify and record the result",
)


class TaskNotFoundError(LookupError):
    pass


class TaskConflictError(RuntimeError):
    pass


class TaskRepository(Protocol):
    def create_task(self, *, request: str, actor: str, domain: str,
                    completion_criteria: str, entity_id: UUID | None,
                    due_at: datetime | None) -> dict: ...
    def create_prototype_task(self, *, request: str, actor: str, domain: str,
                              completion_criteria: str, entity_id: UUID | None,
                              due_at: datetime | None, steps: tuple[str, ...]) -> dict: ...
    def run_prototype_task(self, task_id: UUID, actor: str,
                           steps: tuple[str, ...]) -> dict: ...
    def read_prototype_task(self, task_id: UUID) -> dict: ...
    def transition_task(self, task_id: UUID, revision: int,
                        expected_statuses: tuple[str, ...], destination: str,
                        actor: str) -> dict: ...


class TaskService:
    def __init__(self, repository: TaskRepository):
        self._repository = repository

    def create_task(self, **kwargs) -> dict:
        return self._repository.create_task(**kwargs)

    def create_prototype_task(self, **kwargs) -> dict:
        return self._repository.create_prototype_task(steps=PROTOTYPE_STEPS, **kwargs)

    def run_prototype_task(self, task_id: UUID, actor: str) -> dict:
        return self._repository.run_prototype_task(task_id, actor, PROTOTYPE_STEPS)

    def read_prototype_task(self, task_id: UUID) -> dict:
        return self._repository.read_prototype_task(task_id)

    def transition_task(self, task_id: UUID, revision: int,
                        expected_statuses: tuple[str, ...], destination: str,
                        actor: str) -> dict:
        return self._repository.transition_task(
            task_id, revision, expected_statuses, destination, actor
        )
