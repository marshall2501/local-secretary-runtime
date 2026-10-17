# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Two MAGI judgment strategies feeding one RITSUKO state application.

Strategies own questions, context and validated proposals, not Task state,
Source execution, Result identity or persistence. Both use the same runner.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from ritsuko.core.judgment_application import apply_judgment


class JudgmentStrategy(Protocol):
    """MAGI judgment strategy fixed for the lifetime of a persisted Task."""

    name: str
    allows_recorded_read_failure: bool

    def initialize(self, session: dict) -> None:
        """Initialize judgment-specific prompt context."""

    def prepare_resume(self, session: dict) -> None:
        """Fill missing judgment-specific fields on restored Sessions."""

    async def start(self, session: dict, **context) -> dict:
        """Ask MAGI for the initial proposal."""

    async def advance(self, session: dict, **context) -> dict:
        """Ask MAGI again after a recorded Observation."""

    async def resume_user(self, session: dict, reply: str, **context) -> dict:
        """Interpret a user response without starting another Task."""


@dataclass(frozen=True)
class StageJudgment:
    """The original staged MAGI questioning; not the old Task executor."""

    name: str = "standard"
    allows_recorded_read_failure: bool = False

    def initialize(self, session: dict) -> None:
        """Use shared session fields prepared by the Task lifecycle."""

    def prepare_resume(self, session: dict) -> None:
        """No additional fields are required for staged questions."""

    async def start(self, session: dict, **context) -> dict:
        """Classify the user request before the first staged proposal."""
        from .async_execution import _send_async
        from .dialogue import CLASSIFY_QUESTION

        classification = await _send_async(
            session, "classify", "classify", CLASSIFY_QUESTION,
            timeout=context["timeout"], caller=context["caller"],
            stop_requested=context.get("stop_requested"),
            on_turn_start=context.get("on_turn_start"),
            on_member_progress=context.get("on_member_progress"),
            member_specs_override=context.get("member_specs_override"),
            context_policy=context.get("context_policy"),
        )
        if classification is None:
            return session
        session["classification"] = deepcopy(classification)
        if classification["multiple_requests"]:
            session.update(status="stopped", next_step="multiple_requests_detected")
            return session
        return await self.advance(session, **context)

    async def _evaluate(self, session: dict, purpose: str, **context) -> dict:
        """Send one question and apply its proposal through the shared Core."""
        from .async_execution import _send_async
        from .dialogue import _compose_question

        async def ask(purpose_name: str, *, issue: str | None = None):
            return await _send_async(
                session, "analyze", purpose_name,
                _compose_question(session, purpose_name, issue=issue)
                if issue else _compose_question(session, purpose_name),
                timeout=context["timeout"], caller=context["caller"],
                stop_requested=context.get("stop_requested"),
                on_turn_start=context.get("on_turn_start"),
                on_member_progress=context.get("on_member_progress"),
                member_specs_override=context.get("member_specs_override"),
                context_policy=context.get("context_policy"),
            )

        decision = await ask(purpose)
        if decision is None:
            return session

        async def repair(issue: str):
            return await ask("review_or_repair", issue=issue)

        return await apply_judgment(
            session, decision, strategy=self.name, purpose=purpose,
            repair=repair, stop_requested=context.get("stop_requested"),
        )

    async def advance(self, session: dict, **context) -> dict:
        """Choose a staged question, not a stage-specific Task state writer."""
        from .dialogue import _select_question_purpose, _stop_between_turns

        if _stop_between_turns(session, context.get("stop_requested")):
            return session
        return await self._evaluate(
            session, _select_question_purpose(session), **context,
        )

    async def resume_user(self, session: dict, reply: str, **context) -> dict:
        """Continue the same Task with the user's verified input context."""
        if session.get("classification") is None:
            session["next_step"] = "classify_with_context"
            return await self.start(session, **context)

        responds_to = [
            item["request_id"] for item in session.get("pending_requests") or []
            if isinstance(item, dict) and item.get("request_id")
        ]
        session.setdefault("observations", []).append({
            "source": "user_clarification", "verified": False,
            "text": reply, "responds_to": responds_to,
        })
        session["pending_requests"] = []
        session["next_step"] = "evaluate_user_clarification"
        return await self._evaluate(
            session, "evaluate_observation", **context,
        )


@dataclass(frozen=True)
class GoalJudgment:
    """LLM Goal judgment for any one-to-three enabled MAGI members."""

    name: str = "llm"
    allows_recorded_read_failure: bool = True

    def initialize(self, session: dict) -> None:
        """Initialize bounded Goal context without rewriting member settings."""
        from .goal_loop import MAX_GOAL_TURNS

        enabled = [s for s in session["member_specs"] if s.get("enabled")]
        if not enabled:
            raise ValueError("no_enabled_magi_member")
        session.update(
            control_mode="goal_loop_v1", next_step="evaluate_goal",
            goal=None, goal_judgment=None, user_replies=[],
            previous_queries=[], status_message=None,
            turn_limit=MAX_GOAL_TURNS,
        )

    def prepare_resume(self, session: dict) -> None:
        """Restore only reasoning context; never change Task's chosen strategy."""
        from .goal_loop import MAX_GOAL_TURNS

        session.setdefault("previous_request_signatures", [])
        session.setdefault("turn_limit", MAX_GOAL_TURNS)

    async def start(self, session: dict, **context) -> dict:
        """Request the initial Goal evaluation."""
        return await self.advance(session, **context)

    async def advance(self, session: dict, **context) -> dict:
        """Choose a proposal, then use the shared RITSUKO state application."""
        from .goal_decision import evaluate_goal

        self.prepare_resume(session)
        decision = await evaluate_goal(
            session, timeout=context["timeout"],
            caller=context.get("goal_caller"),
            stop_requested=context.get("stop_requested"),
            on_turn_start=context.get("on_turn_start"),
            on_member_progress=context.get("on_member_progress"),
            member_specs_override=context.get("member_specs_override"),
            context_policy=context.get("context_policy"),
        )
        if decision is None:
            return session
        return await apply_judgment(
            session, decision, strategy=self.name, purpose="evaluate_goal",
            stop_requested=context.get("stop_requested"),
        )

    async def resume_user(self, session: dict, reply: str, **context) -> dict:
        """Add user context and request fresh Goal evaluation."""
        session.setdefault("user_replies", []).append(reply)
        session["user_replies"] = session["user_replies"][-3:]
        session["pending_requests"] = []
        session["next_step"] = "evaluate_user_clarification"
        return await self.advance(session, **context)


_STAGE: JudgmentStrategy = StageJudgment()
_GOAL: JudgmentStrategy = GoalJudgment()


def strategy_for_name(name: str) -> JudgmentStrategy:
    """Resolve the Task's selected judgment strategy."""
    if name == "standard":
        return _STAGE
    if name == "llm":
        return _GOAL
    raise ValueError("unknown_flow_decision_strategy")


def strategy_for_session(session: dict) -> JudgmentStrategy:
    """Restore the saved strategy, including historical Goal sessions."""
    if (
        session.get("decision_strategy") == "llm"
        or session.get("control_mode") == "goal_loop_v1"
    ):
        return _GOAL
    return _STAGE
