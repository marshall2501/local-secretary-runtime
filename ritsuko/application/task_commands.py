# Provenance-Origin: original
# Provenance-AI: AI-generated
"""RITSUKOのTask実行をHTTPやVueの寿命から切り離す受付サービス。

Taskの確定状態は既存ApplicationとDB側のPortが所有する。このサービスは
実行受付・同一Task内の重複防止・協調停止Event・受付状況のみを管理する。
受付状況はプロセス内の一時情報で、永続Taskの代替ではない。
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from threading import Event
from typing import Callable
from uuid import UUID, uuid4

from ritsuko.application.task_runtime import StateDrivenTaskApplication
from ritsuko.core.magi_bridge import (
    reviewable_answer_candidate, reviewable_user_knowledge_proposal,
)


class TaskCommandConflict(ValueError):
    """Task状態や同時処理のため、指定した操作を受け付けられないことを示す。"""


class TaskCommandNotFound(KeyError):
    """受付状況またはTask IDが存在しないことを示す。"""


class TaskCommands:
    """既存Task Applicationの実行を非同期で受付け、進捗を問い合わせ可能にする。

    Taskの保存・権限判定・MAGI判断は行わない。APIプロセス終了時に受付状況と
    実行中Jobは失われるので、DBの実Task状態と混同しない。

    Args:
        application: 正しいDB/Source/Policy Portを注入済みのTask Application。
        queries: 同一DBの確定Task履歴を取得するQuery Service。
        member_specs_loader: 新規Task開始時に有効なMAGI設定を読む関数。
        dialogue_starter_factory: 判断方式に対応した共通MAGI入口を生成する関数。
            Noneでは従来standardのみを受付け、llmへ暗黙に切り替えない。
        max_retained: 完了した操作の受付状況を保持する最大件数。
        memory_review_writer: 本人の明示remember選択時のみ呼び出す
            既存Memory Intakeのprepare/write Port。未接続ならrememberを拒否。
    """

    def __init__(
        self,
        application: StateDrivenTaskApplication,
        queries,
        member_specs_loader: Callable[[], list[dict]],
        *,
        max_retained: int = 100,
        dialogue_starter_factory: Callable | None = None,
        reserve_start: Callable | None = None,
        pending_starts_loader: Callable | None = None,
        policy_snapshot_loader: Callable | None = None,
        request_stop: Callable | None = None,
        requeue_paused: Callable | None = None,
        memory_review_writer: Callable | None = None,
    ) -> None:
        self._application = application
        self._queries = queries
        self._member_specs_loader = member_specs_loader
        self._dialogue_starter_factory = dialogue_starter_factory
        self._reserve_start = reserve_start
        self._pending_starts_loader = pending_starts_loader
        self._policy_snapshot_loader = policy_snapshot_loader
        self._request_stop = request_stop
        self._requeue_paused = requeue_paused
        self._memory_review_writer = memory_review_writer
        self._max_retained = max(1, int(max_retained))
        # 実行中Jobと停止EventはAPIプロセス内だけの一時情報。
        self._jobs: dict[UUID, asyncio.Task] = {}
        self._stops: dict[UUID, Event] = {}
        # 202受付後、DB行が作られる前も照会できる操作状況。
        self._operations: dict[UUID, dict] = {}

    def _retain(self) -> None:
        """完了済み操作の古い記録だけを間引き、実行中の停止操作を失わない。"""
        finished = [
            uid for uid, info in self._operations.items()
            if info["status"] in {"execution_finished", "execution_failed", "interrupted"}
        ]
        for uid in finished[:-self._max_retained]:
            if uid not in self._jobs:
                self._operations.pop(uid, None)

    def _report_member_progress(self, task_id: UUID, event: dict) -> None:
        """Store bounded transient seat states; never persist prompts or Secrets.

        Each callback belongs to the already accepted Task. This is a live
        display hint, not an authoritative Task completion or Provider log.
        """
        if not isinstance(event, dict):
            return
        info = self._operations.get(task_id)
        if info is None or (
            event.get("task_id") and str(event["task_id"]) != str(task_id)
        ):
            return
        name = str(event.get("member") or "").strip()[:64]
        if not name:
            return
        try:
            turn = max(0, int(event.get("turn") or 0))
        except (ValueError, TypeError):
            return
        previous_turn = int(info.get("live_turn") or 0)
        if turn < previous_turn:
            return
        if turn > previous_turn:
            info["member_progress"] = {}
            info["live_turn"] = turn
        progress = {
            "member": name,
            "provider": str(event.get("provider") or "")[:48],
            "model": str(event.get("model") or "")[:96],
            "state": str(event.get("state") or "unknown")[:32],
            "turn": turn,
        }
        elapsed = event.get("elapsed_seconds")
        if type(elapsed) in (int, float) and 0 <= elapsed < 86400:
            progress["elapsed_seconds"] = float(elapsed)
        info.setdefault("member_progress", {})[name] = progress

    def _schedule(self, task_id: UUID, kind: str, command) -> dict:
        """同一Taskの多重実行を防ぎ、受付とTask完了を別々に記録する。"""
        job = self._jobs.get(task_id)
        if job is not None and not job.done():
            raise TaskCommandConflict("task_operation_already_running")
        stop_event = Event()
        self._stops[task_id] = stop_event
        self._operations[task_id] = {
            "task_id": str(task_id), "kind": kind, "status": "accepted",
            "stop_requested": False, "error_type": None,
            "member_progress": {}, "live_turn": 0,
        }

        async def execute() -> None:
            """Operation進捗を更新する。永続Taskの結果はApplicationへ委譲する。"""
            self._operations[task_id]["status"] = "running"
            try:
                await command(stop_event)
            except asyncio.CancelledError:
                # サーバー終了等でworkerが消えた場合、Taskを完了扱いしない。
                self._operations[task_id]["status"] = "interrupted"
                raise
            except Exception as exc:
                # pending Task was paused by Stop before the claim: this is
                # a successful refusal to execute, not an execution failure.
                if (stop_event.is_set() and isinstance(exc, ValueError)
                        and str(exc) == "task_start_not_pending"):
                    self._operations[task_id]["status"] = "interrupted"
                else:
                    # 例外本文に混じる入力／Credentialは表示しない。
                    self._operations[task_id]["status"] = "execution_failed"
                    self._operations[task_id]["error_type"] = type(exc).__name__
            else:
                # Runnerが返ったことのみ表す。Task completedとは別の事実。
                self._operations[task_id]["status"] = "execution_finished"
            finally:
                self._stops.pop(task_id, None)
                self._jobs.pop(task_id, None)
                self._retain()

        self._jobs[task_id] = asyncio.create_task(execute())
        return self.operation(task_id)

    async def _task_for_command(self, task_id: UUID) -> dict:
        """回答・Review前に保存済みTaskを照会する。最終競合判定はDB claimに残す。"""
        try:
            trace = await asyncio.to_thread(self._queries.trace, task_id)
        except ValueError as exc:
            raise TaskCommandNotFound("task_not_found") from exc
        return dict(trace.get("task") or {})

    async def start(self, request: str, *, decision_strategy: str = "standard",
                    task_id: UUID | None = None) -> dict:
        """Taskの判断方式とメンバー設定を受付時に確定して共通Applicationへ渡す。

        旧Task APIとの内部互換ではstandardを既定とする。新Vue HTTPの
        呼出元には判断方式の明示指定を要求し、意図せぬ方式変更を避ける。
        実行を受理してもDBのTask作成や完了が終わったことにはならない。
        """
        if decision_strategy not in {"standard", "llm"}:
            raise ValueError("unknown_flow_decision_strategy")
        text = str(request or "").strip()
        if not text:
            raise ValueError("empty_request")
        specs = await asyncio.to_thread(self._member_specs_loader)
        enabled = [item for item in specs if item.get("enabled")]
        if not enabled:
            raise TaskCommandConflict("no_enabled_magi_member")
        if decision_strategy == "llm" and self._dialogue_starter_factory is None:
            raise TaskCommandConflict("llm_strategy_not_connected")
        task_id = task_id or uuid4()
        policy = None
        if self._reserve_start is not None:
            if self._policy_snapshot_loader is None:
                raise TaskCommandConflict("durable_start_policy_loader_missing")
            policy = await asyncio.to_thread(self._policy_snapshot_loader)
            # DB transaction must commit before 202 is returned or any Job is scheduled.
            try:
                created = await asyncio.to_thread(
                    self._reserve_start, task_id=task_id, request=text,
                    member_specs=deepcopy(specs), decision_strategy=decision_strategy,
                    policy_snapshot=deepcopy(policy),
                )
            except ValueError as exc:
                if str(exc) == "task_id_intent_mismatch":
                    raise TaskCommandConflict("task_id_intent_mismatch") from exc
                raise
            if created is False:
                # Same UUID, request and judgment strategy: no second MAGI run.
                return {
                    "task_id": str(task_id), "kind": "start",
                    "status": "already_accepted", "source": "persisted_task",
                    "stop_requested": None, "error_type": None,
                }
        self._launch_start(task_id, text, specs, decision_strategy, policy)
        return self.operation(task_id)

    def _launch_start(self, task_id: UUID, request: str,
                      member_specs: list[dict], decision_strategy: str,
                      policy_snapshot: dict | None,
                      resume_snapshot: dict | None = None) -> None:
        """保存済み受付Taskを既存Applicationへつなぎ、Runnerは一種類に保つ。"""
        starter_options = {}
        if self._dialogue_starter_factory is not None:
            if self._reserve_start is not None or self._requeue_paused is not None:
                # Explicit resume carries the original persisted Policy
                # snapshot; today's settings must not override it.
                starter_options["dialogue_starter"] = self._dialogue_starter_factory(
                    decision_strategy, deepcopy(policy_snapshot),
                )
            else:
                starter_options["dialogue_starter"] = (
                    self._dialogue_starter_factory(decision_strategy)
                )
        timeout = max(
            (float(item.get("timeout_seconds") or 120)
             for item in member_specs if item.get("enabled")),
            default=120,
        )

        if resume_snapshot is not None:
            starter_options["resume_snapshot"] = deepcopy(resume_snapshot)
        async def run(stop: Event) -> None:
            """DB pendingのclaim後、既存のRITSUKO/MAGI共通Taskループを起動する。"""
            await self._application.start(
                request, task_id=task_id, member_specs=deepcopy(member_specs),
                timeout=timeout, stop_requested=stop.is_set,
                on_member_progress=lambda event: self._report_member_progress(
                    task_id, event,
                ),
                **starter_options,
            )
        self._schedule(task_id, "start", run)

    async def recover_pending(self, *, limit: int = 20) -> list[dict]:
        """DBに残った未着手Taskだけを選び、通常の実行経路へ再投入する。

        既存のrunningは自動再実行しない。同時Workerの衝突はRepositoryの
        pending→running原子claimで拒否される。API/PC自動起動は別段階。
        """
        if self._reserve_start is None or self._pending_starts_loader is None:
            raise TaskCommandConflict("durable_start_not_configured")
        pending = await asyncio.to_thread(self._pending_starts_loader, limit=limit)
        results = []
        for item in pending:
            task_id = UUID(str(item["task_id"]))
            strategy = item["decision_strategy"]
            specs = deepcopy(item["member_specs"])
            if strategy not in {"standard", "llm"} or not any(
                member.get("enabled") for member in specs
            ):
                continue
            try:
                self._launch_start(
                    task_id, item["request"], specs, strategy,
                    item.get("policy_snapshot"),
                    item.get("resume_snapshot"),
                )
            except TaskCommandConflict:
                # Another request in this process already started this Task.
                continue
            results.append(self.operation(task_id))
        return results

    async def answer(self, task_id: UUID, reply: str) -> dict:
        """DB上で本人回答待ちのTaskだけを同一IDで再開受付する。"""
        text = str(reply or "").strip()
        if not text:
            raise ValueError("empty_reply")
        task = await self._task_for_command(task_id)
        session = task.get("magi_session") or {}
        if task.get("status") != "waiting_external" or session.get("status") != "waiting_user":
            raise TaskCommandConflict("task_not_waiting_for_user")
        timeout = max(
            (float(item.get("timeout_seconds") or 120)
             for item in session.get("member_specs") or []
             if item.get("enabled")), default=120,
        )

        async def run(stop: Event) -> None:
            """保存済みMAGI対話の本人回答再開をRITSUKOへ委譲する。"""
            await self._application.answer(
                task_id, text, timeout=timeout, stop_requested=stop.is_set,
                on_member_progress=lambda event: self._report_member_progress(
                    task_id, event,
                ),
            )
        return self._schedule(task_id, "answer", run)

    async def review(self, task_id: UUID, decision: str) -> dict:
        """本人の回答のみ／明示記憶反映を同一RITSUKO Task経路で受付ける。

        remember選択時だけ既存のPKB Memory Intakeを先に実行し、その
        receiptをTask Reviewへ渡す。PKB登録成功をLLM提案だけで宣言しない。
        """
        if decision not in {"answer_only", "remember"}:
            raise ValueError("review_decision_not_enabled")
        if decision == "remember" and self._memory_review_writer is None:
            raise ValueError("review_decision_not_enabled")
        task = await self._task_for_command(task_id)
        session = task.get("magi_session") or {}
        if decision == "remember":
            eligible = reviewable_user_knowledge_proposal(session) is not None
        else:
            # The same guarded candidates as the DB claim. An arbitrary
            # proposal_ready session cannot initiate a supposedly valid review.
            eligible = (
                reviewable_user_knowledge_proposal(session) is not None
                or reviewable_answer_candidate(session) is not None
            )
        if (
            task.get("status") != "waiting_external"
            or task.get("phase") not in {None, "awaiting_review"}
            or not eligible
        ):
            raise TaskCommandConflict("task_not_waiting_for_review")
        timeout = max(
            (float(item.get("timeout_seconds") or 120)
             for item in session.get("member_specs") or []
             if item.get("enabled")), default=120,
        )

        async def run(stop: Event) -> None:
            """Explicit Memory Intake receipt feeds the existing review executor."""
            memory_result = None
            if decision == "remember":
                memory_result = await asyncio.to_thread(
                    self._memory_review_writer, task_id,
                )
                if not isinstance(memory_result, dict) or memory_result.get(
                    "status"
                ) not in {"committed", "replayed"}:
                    raise ValueError("memory_intake_not_committed")
            await self._application.review(
                task_id, decision, timeout=timeout,
                memory_result=memory_result,
                stop_requested=stop.is_set,
                on_member_progress=lambda event: self._report_member_progress(
                    task_id, event,
                ),
            )
        return self._schedule(task_id, "review", run)

    async def resume(self, task_id: UUID) -> dict:
        """停止した未実行Taskを元の設定・同じIDで再び実行受付する。

        Stage/Goal方式とPolicy/MAGI席は保存済みIntentから復元する。
        実行済みActionのあるTaskを新Taskとして繰り返すことは許可しない。
        複数APIインスタンスの競合はRepositoryのFOR UPDATEが防止する。

        Args:
            task_id: 再開したい永続TaskのID。

        Returns:
            dict: 再開を受付けたプロセス内Operation。
        """
        if self._requeue_paused is None or self._dialogue_starter_factory is None:
            raise TaskCommandConflict("task_resume_not_configured")
        job = self._jobs.get(task_id)
        if job is not None and not job.done():
            raise TaskCommandConflict("task_operation_already_running")
        try:
            saved = await asyncio.to_thread(
                self._requeue_paused, task_id=task_id,
            )
        except ValueError as exc:
            if str(exc) == "task_not_found":
                raise TaskCommandNotFound("task_not_found") from exc
            raise TaskCommandConflict("paused_task_not_requeueable") from exc
        # Task is now pending in the DB. The ordinary atomic claim and
        # single MAGI/Application runner handle this same Task ID.
        self._launch_start(
            task_id, saved["request"], saved["member_specs"],
            saved["decision_strategy"], saved.get("policy_snapshot"),
            saved.get("resume_snapshot"),
        )
        return self.operation(task_id)

    def stop(self, task_id: UUID) -> dict:
        """実行中Jobへ停止Eventを送る。Taskが停止した保証は返さない。"""
        job = self._jobs.get(task_id)
        if job is None or job.done():
            # A persisted Task can survive API restart without a local worker.
            # Persist the stop request without reporting execution as stopped.
            if self._request_stop is None:
                raise TaskCommandConflict("no_running_operation")
            try:
                db_status = self._request_stop(task_id=task_id)
            except ValueError as exc:
                if str(exc) == "task_not_found":
                    raise TaskCommandNotFound("task_not_found") from exc
                raise TaskCommandConflict("task_not_stoppable") from exc
            if db_status not in {"paused", "running"}:
                raise TaskCommandConflict("invalid_persisted_stop_status")
            return {
                "task_id": str(task_id), "kind": "stop",
                "status": "operation_not_in_process",
                "source": "persisted_task", "task_status": db_status,
                "stop_requested": True, "error_type": None,
                "execution_reconciliation_required": db_status == "running",
            }
        if self._request_stop is not None:
            try:
                # Durable intent is recorded before the in-process Event is set.
                db_status = self._request_stop(task_id=task_id)
            except ValueError as exc:
                if str(exc) == "task_not_found":
                    raise TaskCommandNotFound("task_not_found") from exc
                raise TaskCommandConflict("task_not_stoppable") from exc
            if db_status not in {"paused", "running"}:
                raise TaskCommandConflict("invalid_persisted_stop_status")
            self._operations[task_id]["task_status"] = db_status
        self._stops[task_id].set()
        self._operations[task_id]["stop_requested"] = True
        return self.operation(task_id)

    def operation(self, task_id: UUID) -> dict:
        """プロセス内Operationを返し、不在なら既存DB Taskの状態を照会する。

        API再起動後でも保存済みTaskを404にしない。DBの「running」は
        Worker稼働の証拠でないため、Operationが継続していると偽らない。
        DBへ操作履歴を新設・書込はせず、受付の永続化も保証しない。
        """
        current = self._operations.get(task_id)
        if current is not None:
            return dict(current)
        try:
            trace = self._queries.trace(task_id)
        except ValueError as exc:
            raise TaskCommandNotFound("operation_and_task_not_found") from exc
        task = trace.get("task") or {}
        if not task:
            raise TaskCommandNotFound("operation_and_task_not_found")
        persisted_status = str(task.get("status") or "unknown")
        return {
            "task_id": str(task_id),
            "kind": None,
            "status": "operation_not_in_process",
            "stop_requested": task.get("stop_requested"),
            "error_type": None,
            "source": "persisted_task",
            "task_status": persisted_status,
            # running Taskは「Job実行中」と同義ではない。
            "execution_reconciliation_required": persisted_status == "running",
        }
