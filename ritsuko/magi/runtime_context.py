"""Task-scoped reference date for the MAGI request contract.

The agent, not the model, owns the reference clock. Persist this small,
non-secret value in the MAGI session; never recalculate it on resume.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from zoneinfo import ZoneInfo


DEFAULT_TASK_TIMEZONE = "Asia/Tokyo"
DEFAULT_TASK_LOCALE = "ja-JP"


def create_runtime_context(
    *,
    timezone_name: str = DEFAULT_TASK_TIMEZONE,
    locale: str = DEFAULT_TASK_LOCALE,
    now: datetime | None = None,
) -> dict[str, str]:
    """Freeze the Task's reference datetime using an IANA calendar timezone."""
    zone = ZoneInfo(timezone_name)
    if not isinstance(locale, str) or not locale.strip():
        raise ValueError("invalid_task_locale")
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ValueError("task_reference_datetime_must_be_aware")
    reference = (now or datetime.now(zone)).astimezone(zone)
    return {
        "reference_datetime": reference.isoformat(timespec="seconds"),
        "timezone": timezone_name,
        "locale": locale,
    }


def runtime_context_from_session(session: dict) -> dict[str, str] | None:
    """Return an existing snapshot, never backfill a legacy Task with today's time."""
    context = session.get("runtime_context")
    if context is None:
        return None
    if not isinstance(context, dict) or not all(
        isinstance(context.get(key), str) and context[key]
        for key in ("reference_datetime", "timezone", "locale")
    ):
        raise ValueError("invalid_task_runtime_context")
    reference = datetime.fromisoformat(context["reference_datetime"])
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("naive_task_reference_datetime")
    ZoneInfo(context["timezone"])
    return deepcopy({
        "reference_datetime": context["reference_datetime"],
        "timezone": context["timezone"],
        "locale": context["locale"],
    })


def task_reference_date(session: dict) -> date | None:
    """Resolve the saved Task clock to its own calendar, not the resume clock."""
    context = runtime_context_from_session(session)
    if context is None:
        return None
    reference = datetime.fromisoformat(context["reference_datetime"])
    return reference.astimezone(ZoneInfo(context["timezone"])).date()
