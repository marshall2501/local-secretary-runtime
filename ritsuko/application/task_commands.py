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
    ) -> None:
        self._application = application
        self._queries = queries
        self._member_specs_loader = member_specs_loader
        self._dialogue_starter_factory = dialogue_starter_factory
        self._reserve_start = reserve_start
        self._pending_starts_loader = pending_starts_loader
        self._policy_snapshot_loader = policy_snapshot_loader
        self._request_stop = request_stop
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
                # 入力文や認証情報を受付状況へ残さず、原因の型だけを保持する。
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

    async def start(self, request: str, *, decision_strategy: str = "standard") -> dict:
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
        # 先行Goal判断実装の現行制約。席数を勝手に変更・保存しない。
        if decision_strategy == "llm" and len(enabled) != 1:
            raise TaskCommandConflict("llm_strategy_currently_requires_one_member")
        if decision_strategy == "llm" and self._dialogue_starter_factory is None:
            raise TaskCommandConflict("llm_strategy_not_connected")
        task_id = uuid4()
        policy = None
        if self._reserve_start is not None:
            if self._policy_snapshot_loader is None:
                raise TaskCommandConflict("durable_start_policy_loader_missing")
            policy = await asyncio.to_thread(self._policy_snapshot_loader)
            # DB transaction must commit before 202 is returned or any Job is scheduled.
            await asyncio.to_thread(
                self._reserve_start, task_id=task_id, request=text,
                member_specs=deepcopy(specs), decision_strategy=decision_strategy,
                policy_snapshot=deepcopy(policy),
            )
        self._launch_start(task_id, text, specs, decision_strategy, policy)
        return self.operation(task_id)

    def _launch_start(self, task_id: UUID, request: str,
                      member_specs: list[dict], decision_strategy: str,
                      policy_snapshot: dict | None) -> None:
        """保存済み受付Taskを既存Applicationへつなぎ、Runnerは一種類に保つ。"""
        starter_options = {}
        if self._dialogue_starter_factory is not None:
            if self._reserve_start is not None:
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

        async def run(stop: Event) -> None:
            """DB pendingのclaim後、既存のRITSUKO/MAGI共通Taskループを起動する。"""
            await self._application.start(
                request, task_id=task_id, member_specs=deepcopy(member_specs),
                timeout=timeout, stop_requested=stop.is_set, **starter_options,
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
            if strategy == "llm" and sum(
                bool(member.get("enabled")) for member in specs
            ) != 1:
                continue
            try:
                self._launch_start(
                    task_id, item["request"], specs, strategy,
                    item.get("policy_snapshot"),
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
            )
        return self._schedule(task_id, "answer", run)

    async def review(self, task_id: UUID, decision: str) -> dict:
        """記憶登録を伴わない回答のみReviewを受付する。

        記憶反映には独立したMemory Intakeが必要なので、この段階では
        未対応として拒否し、偽のPKB成功を返さない。
        """
        if decision != "answer_only":
            raise ValueError("review_decision_not_enabled")
        task = await self._task_for_command(task_id)
        session = task.get("magi_session") or {}
        if (task.get("status") != "waiting_external"
                or session.get("status") != "proposal_ready"):
            raise TaskCommandConflict("task_not_waiting_for_review")
        timeout = max(
            (float(item.get("timeout_seconds") or 120)
             for item in session.get("member_specs") or []
             if item.get("enabled")), default=120,
        )

        async def run(stop: Event) -> None:
            """提案を再評価し、既存Task Storeへ確定判断を委譲する。"""
            await self._application.review(
                task_id, "answer_only", timeout=timeout, stop_requested=stop.is_set,
            )
        return self._schedule(task_id, "review", run)

    def stop(self, task_id: UUID) -> dict:
        """実行中Jobへ停止Eventを送る。Taskが停止した保証は返さない。"""
        job = self._jobs.get(task_id)
        if job is None or job.done():
            raise TaskCommandConflict("no_running_operation")
        if self._request_stop is not None:
            try:
                # Durable intent is recorded before the in-process Event is set.
                db_status = self._request_stop(task_id=task_id)
            except ValueError as exc:
                raise TaskCommandConflict("task_not_stoppable") from exc
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
            "stop_requested": None,
            "error_type": None,
            "source": "persisted_task",
            "task_status": persisted_status,
            # running Taskは「Job実行中」と同義ではない。
            "execution_reconciliation_required": persisted_status == "running",
        }
