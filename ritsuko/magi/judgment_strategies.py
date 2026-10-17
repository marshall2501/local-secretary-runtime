# Provenance-Origin: original
# Provenance-AI: AI-generated
"""MAGI判断方式を交換するPortと、従来／Goalの二つのAdapter。

判断方式はMAGIへの質問と候補への解釈だけを所有する。Task作成、
Action/Resultの実行・保存、Observationの検証、停止・復旧、最終状態の確定は
上位の同じRITSUKO Application/Core/Repositoryへ戻す。

async_executionとgoal_loopの既存transportを再利用し、循環importを
避けるため各Adapterの実行時にのみ呼び出す。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol


class JudgmentStrategy(Protocol):
    """一つのTask Sessionへ固定されるMAGIの判断方式契約。"""

    name: str
    allows_recorded_read_failure: bool

    def initialize(self, session: dict) -> None:
        """新規Sessionに方式固有の推論文脈だけを設定する。"""

    def prepare_resume(self, session: dict) -> None:
        """旧保存Sessionが不足する推論用fieldを補完する。"""

    async def start(self, session: dict, **context) -> dict:
        """Task受付後に最初のMAGI判断を実行する。"""

    async def advance(self, session: dict, **context) -> dict:
        """共通Observationの後に、次のMAGI判断を実行する。"""

    async def resume_user(self, session: dict, reply: str, **context) -> dict:
        """本人回答を判断方式の文脈へ取り込み、次の判断を行う。"""


@dataclass(frozen=True)
class StageJudgment:
    """既存の段階的MAGI判断を共通RITSUKOへ接続するAdapter。"""

    name: str = "standard"
    allows_recorded_read_failure: bool = False

    def initialize(self, session: dict) -> None:
        """段階判断が既に持つ共通Session初期値をそのまま使う。"""

    def prepare_resume(self, session: dict) -> None:
        """従来SessionにはGoal固有の追加fieldを要求しない。"""

    async def start(self, session: dict, **context) -> dict:
        """分類結果を保存した後、通常の段階判断へ進む。"""
        from .async_execution import _send_async
        from .dialogue import CLASSIFY_QUESTION
        classification = await _send_async(
            session, "classify", "classify", CLASSIFY_QUESTION,
            timeout=context["timeout"], caller=context["caller"],
            stop_requested=context.get("stop_requested"),
            on_turn_start=context.get("on_turn_start"),
            on_member_progress=context.get("on_member_progress"),
        )
        if classification is None:
            return session
        session["classification"] = deepcopy(classification)
        if classification["multiple_requests"]:
            session.update(status="stopped", next_step="multiple_requests_detected")
            return session
        return await self.advance(session, **context)

    async def advance(self, session: dict, **context) -> dict:
        """段階判断の次Turnを既存MAGI通信経路で評価する。"""
        from .async_execution import _advance_async
        return await _advance_async(
            session, timeout=context["timeout"], caller=context["caller"],
            stop_requested=context.get("stop_requested"),
            on_turn_start=context.get("on_turn_start"),
            on_member_progress=context.get("on_member_progress"),
            member_specs_override=context.get("member_specs_override"),
            context_policy=context.get("context_policy"),
        )

    async def resume_user(self, session: dict, reply: str, **context) -> dict:
        """分類未了なら分類し、通常は本人回答をObservationとして再評価する。"""
        from .async_execution import _apply_detail_async, _send_async
        from .dialogue import CLASSIFY_QUESTION, _compose_question

        if session.get("classification") is None:
            session["next_step"] = "classify_with_context"
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
        response = await _send_async(
            session, "analyze", "evaluate_observation",
            _compose_question(session, "evaluate_observation"),
            timeout=context["timeout"], caller=context["caller"],
            stop_requested=context.get("stop_requested"),
            on_turn_start=context.get("on_turn_start"),
            on_member_progress=context.get("on_member_progress"),
            member_specs_override=context.get("member_specs_override"),
            context_policy=context.get("context_policy"),
        )
        if response is None:
            return session
        return await _apply_detail_async(
            session, response, timeout=context["timeout"],
            purpose="evaluate_observation", caller=context["caller"],
            stop_requested=context.get("stop_requested"),
            on_turn_start=context.get("on_turn_start"),
            on_member_progress=context.get("on_member_progress"),
            member_specs_override=context.get("member_specs_override"),
            context_policy=context.get("context_policy"),
        )


@dataclass(frozen=True)
class GoalJudgment:
    """MAGI 1～3席のGoal／次の行動判断を共通RITSUKOへ接続するAdapter。"""

    name: str = "llm"
    allows_recorded_read_failure: bool = True

    def initialize(self, session: dict) -> None:
        """新規TaskのGoal判断文脈を初期化し、既定席・権限は変更しない。"""
        from .goal_loop import (
            MAX_GOAL_TURNS, goal_requires_source, goal_requires_web,
        )
        session["member_specs"] = [
            item for item in session["member_specs"] if item.get("enabled")
        ]
        if not session["member_specs"]:
            raise ValueError("no_enabled_magi_member")
        session.update(
            control_mode="goal_loop_v1", next_step="evaluate_goal",
            requires_source=goal_requires_source(session["user_raw"]),
            requires_web=goal_requires_web(session["user_raw"]),
            goal=None, goal_judgment=None, user_replies=[],
            previous_queries=[], status_message=None,
            turn_limit=MAX_GOAL_TURNS,
        )

    def prepare_resume(self, session: dict) -> None:
        """以前のGoal Sessionに不足する共有MAGI補助fieldのみ補完する。"""
        from .goal_loop import MAX_GOAL_TURNS
        session.setdefault("previous_request_signatures", [])
        session.setdefault("turn_limit", MAX_GOAL_TURNS)

    async def start(self, session: dict, **context) -> dict:
        """Goal評価を最初のMAGI判断として実行する。"""
        return await self.advance(session, **context)

    async def advance(self, session: dict, **context) -> dict:
        """保存済みTaskのGoal・ObservationをMAGIへ再評価させる。"""
        from .goal_loop import _run_goal_turn
        self.prepare_resume(session)
        return await _run_goal_turn(
            session, timeout=context["timeout"],
            caller=context.get("goal_caller"),
            stop_requested=context.get("stop_requested"),
            on_turn_start=context.get("on_turn_start"),
            on_member_progress=context.get("on_member_progress"),
            member_specs_override=context.get("member_specs_override"),
            context_policy=context.get("context_policy"),
        )

    async def resume_user(self, session: dict, reply: str, **context) -> dict:
        """本人の追加入力をGoal判断用の会話文脈に入れて再評価する。"""
        session.setdefault("user_replies", []).append(reply)
        session["user_replies"] = session["user_replies"][-3:]
        session["pending_requests"] = []
        session["next_step"] = "evaluate_user_clarification"
        return await self.advance(session, **context)


_STAGE: JudgmentStrategy = StageJudgment()
_GOAL: JudgmentStrategy = GoalJudgment()


def strategy_for_name(name: str) -> JudgmentStrategy:
    """新規Taskに指定された交換可能な判断方式を選ぶ。"""
    if name == "standard":
        return _STAGE
    if name == "llm":
        return _GOAL
    raise ValueError("unknown_flow_decision_strategy")


def strategy_for_session(session: dict) -> JudgmentStrategy:
    """永続化済みTaskの判断方式を復元し、旧Goal Sessionも受け入れる。"""
    if (session.get("decision_strategy") == "llm"
            or session.get("control_mode") == "goal_loop_v1"):
        return _GOAL
    return _STAGE
