"""Read-only RITSUKO Task query application service."""
from __future__ import annotations

from typing import Protocol
from uuid import UUID


class CoreTaskQueryRepository(Protocol):
    def recent(self, limit: int, offset: int) -> list[dict]: ...
    def open(self, limit: int, offset: int) -> list[dict]: ...
    def completed(self, limit: int, offset: int) -> list[dict]: ...
    def trace(self, task_id: UUID) -> dict: ...


def _validate_window(limit: int, offset: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be 1..50")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")


class CoreTaskQueryService:
    def __init__(self, repository: CoreTaskQueryRepository):
        self._repository = repository

    def recent(self, limit: int = 10, offset: int = 0) -> list[dict]:
        _validate_window(limit, offset)
        return self._repository.recent(limit, offset)

    def open(self, limit: int = 20, offset: int = 0) -> list[dict]:
        _validate_window(limit, offset)
        return self._repository.open(limit, offset)

    def completed(self, limit: int = 8, offset: int = 0) -> list[dict]:
        _validate_window(limit, offset)
        return self._repository.completed(limit, offset)

    def trace(self, task_id: UUID) -> dict:
        return self._repository.trace(task_id)


def core_task_selection_result(item: dict) -> dict:
    task_id = str(item.get("id") or "").strip()
    status = str(item.get("status") or "").strip()
    if not task_id:
        raise ValueError("Task ID is required")
    if status not in {"waiting_external", "running", "paused", "completed", "failed"}:
        raise ValueError("Unsupported Task status")
    return {
        "task_id": task_id, "status": status, "core_slice": item.get("core_slice"),
        "phase": item.get("phase"), "selected_capability": item.get("selected_capability"),
        "question": item.get("question"),
        "message": item.get("message") or (
            "完了済みTaskを閲覧しています。" if status == "completed"
            else "保存済みTaskを選択しました。"
        ),
        "effective_request": item.get("effective_request"),
        "comparison": item.get("comparison"), "advisor_shadow": item.get("advisor_shadow"),
        "read_only_history": status == "completed", "resumed_from_storage": True,
    }
