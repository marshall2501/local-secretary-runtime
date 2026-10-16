# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Vue試作専用の架空Task実行境界。

実運用DB・Provider・外部Actionへ接続しない。共通StateDrivenTaskApplicationと
RITSUKOのObservation/本人回答再開関数を使い、UIから独立した受付を検査する。
ここで得た「完了」は合成fixtureの結果であり実依頼の達成ではない。
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Event, RLock
from uuid import UUID, uuid4

from bootstrap.ritsuko_task_composition import build_task_application


class TaskConflict(ValueError):
    """要求された架空Taskの状態が操作に適さない場合。"""


@dataclass
class _Task:
    """同じTask IDに対応する架空状態とRITSUKO sessionを保持する。"""

    id: str
    request: str
    status: str = "queued"
    message: str = ""
    question: str | None = None
    # 本人回答再開に必要な合成MAGI session。DBには保存せずプロセス内だけで保持。
    session: dict | None = None
    # 画面の停止要求と合成Runnerの協調停止を同じTask内で受け渡す。
    stop: Event = field(default_factory=Event)
    error: str | None = None


class IsolatedTaskService:
    """本番Taskを変更せず開始・回答・停止のAPI契約を試す。

    架空Taskの状態と非同期Jobはこのプロセス内だけにあり、再起動時には失われる。
    """

    def __init__(self) -> None:
        """架空Taskストアと共通RITSUKO Applicationを初期化する。"""
        self._lock = RLock()
        # 架空Taskの唯一の正本。画面や実DBへ複製しない。
        self._tasks: dict[str, _Task] = {}
        # 受付・回答HTTPが終了した後も合成処理を継続するためJob参照を保持する。
        # 停止はこの辞書のTask取消ではなく、各Taskのstop Eventで伝える。
        self._jobs: dict[str, asyncio.Task] = {}
        self._application = build_task_application(
            create_task_record=self._create_record,
            execute_source_request=self._reject_source,
            record_source_read_record=self._reject_source,
            persist_session_record=self._persist,
            fail_task_record=self._fail,
            claim_user_resume_record=self._claim_answer,
            abort_user_resume_record=self._abort_answer,
            claim_proposal_review_record=self._unsupported_review,
            finalize_proposal_review_record=self._unsupported_review,
            abort_proposal_review_record=self._unsupported_review,
            review_runner=self._unsupported_review,
        )

    def _record(self, task_id: UUID | str) -> _Task:
        """既存Task IDが存在しなければ404用のキー例外を返す。"""
        with self._lock:
            return self._tasks[str(task_id)]

    @staticmethod
    def _snapshot(task: _Task) -> dict:
        """画面にはTask正本の状態のみ渡し、生の内部sessionは渡さない。"""
        return {
            "task_id": task.id, "request": task.request, "status": task.status,
            "message": task.message, "question": task.question,
            "error": task.error, "synthetic": True,
        }

    def list_tasks(self) -> list[dict]:
        """メモリに残る架空Task一覧を新しいものから返す。"""
        with self._lock:
            return [self._snapshot(t) for t in reversed(list(self._tasks.values()))]

    def get(self, task_id: str) -> dict:
        """同一Task IDの最新状態を返す。"""
        with self._lock:
            return self._snapshot(self._record(task_id))

    async def start(self, request: str) -> dict:
        """架空Taskを受付け、MAGIの完了を待たずTask IDを返す。"""
        text = request.strip()
        if not text:
            raise ValueError("empty_request")
        uid = str(uuid4())
        with self._lock:
            self._tasks[uid] = _Task(id=uid, request=text)
            self._jobs[uid] = asyncio.create_task(self._run_start(uid))
            return self._snapshot(self._tasks[uid])

    async def _run_start(self, uid: str) -> None:
        """共通Task開始処理へ架空対話関数を渡し、実LLMなしで状態を進める。"""
        try:
            task = self._record(uid)
            await self._application.start(
                task.request,
                member_specs=[{"name": "MELCHIOR", "enabled": True}],
                timeout=5.0,
                task_id=UUID(uid),
                stop_requested=task.stop.is_set,
                dialogue_starter=self._synthetic_dialogue,
            )
        except Exception as exc:
            self._fail(UUID(uid), type(exc).__name__)

    async def _synthetic_dialogue(self, request: str, *, task_id: str,
                                  stop_requested=None, **_kwargs) -> dict:
        """外部モデルを使わず本人質問に至る架空sessionを作る。"""
        await asyncio.sleep(0.06)
        if stop_requested is not None and stop_requested():
            return {"task_id": task_id, "status": "stopped",
                    "next_step": "user_requested_stop", "turns": []}
        return {
            "task_id": task_id, "status": "waiting_user", "next_step": "ask_user",
            "user_question": "架空テスト：回答の対象を指定してください。",
            "turns": [], "pending_requests": [], "observations": [],
            "user_raw": request,
        }

    async def answer(self, task_id: str, reply: str) -> dict:
        """質問待ちTaskを共通Applicationから再開し、受付状態を返す。"""
        text = reply.strip()
        if not text:
            raise ValueError("empty_reply")
        with self._lock:
            task = self._record(task_id)
            if task.status != "waiting_user":
                raise TaskConflict("not_waiting_for_user")
            task.status = "running"
            task.question = None
            task.stop.clear()
            self._jobs[task_id] = asyncio.create_task(self._run_answer(task_id, text))
            return self._snapshot(task)

    async def _run_answer(self, task_id: str, text: str) -> None:
        """既存Task IDのRITSUKO本人回答再開境界を実行する。"""
        try:
            task = self._record(task_id)
            await self._application.answer(
                UUID(task_id), text, timeout=5.0,
                stop_requested=task.stop.is_set,
                user_continuation=self._synthetic_answer,
            )
        except Exception as exc:
            self._fail(UUID(task_id), type(exc).__name__)

    async def _synthetic_answer(self, session: dict, reply: str,
                                *, stop_requested=None, **_kwargs) -> dict:
        """架空返信を受けた場合だけ合成の回答候補を作る。"""
        await asyncio.sleep(0.06)
        updated = deepcopy(session)
        if stop_requested is not None and stop_requested():
            updated.update(status="stopped", next_step="user_requested_stop")
        else:
            updated.update(
                status="candidate_ready", next_step="respond",
                user_question=None,
                detail={"answer_candidate": f"架空テスト回答：{reply.strip()}"},
            )
        return updated

    def stop(self, task_id: str) -> dict:
        """実行中は停止要求を伝え、本人回答待ちは停止状態にする。"""
        with self._lock:
            task = self._record(task_id)
            if task.status not in {"queued", "running", "waiting_user"}:
                raise TaskConflict("task_not_stoppable")
            task.stop.set()
            if task.status == "waiting_user":
                task.status = "paused"
                task.question = None
            return self._snapshot(task)

    def _create_record(self, task_id: UUID, _request: str, _specs: list[dict]) -> None:
        """RITSUKOのTask作成処理から架空レコードを実行中へ遷移する。"""
        with self._lock:
            task = self._record(task_id)
            task.status = "running"

    def _persist(self, task_id: UUID, session: dict,
                 _selected_capability: str | None = None) -> dict:
        """合成MAGIの状態を架空Taskへ保存し、UI用状態に変換する。

        `candidate_ready` を完了へ変えるのは隔離fixture専用であり、
        実Taskの完了判断を実装・検証したことにはならない。
        """
        with self._lock:
            task = self._record(task_id)
            task.session = deepcopy(session)
            state = session.get("status")
            if state == "waiting_user":
                task.status = "waiting_user"
                task.question = str(session.get("user_question") or "")
            elif state == "candidate_ready":
                # ここでの完了は試験用の疑似判定。Production TaskではMAGI候補だけで完了にしない。
                task.status = "completed"
                task.message = str((session.get("detail") or {}).get("answer_candidate") or "")
            elif state == "stopped" and session.get("next_step") == "user_requested_stop":
                task.status = "paused"
            else:
                task.status = "failed"
                task.error = "unexpected_fixture_state"
            return self._snapshot(task)

    def _claim_answer(self, task_id: UUID, _length: int, _fingerprint: str):
        """本人回答再開用の保存済み架空sessionを返す。"""
        with self._lock:
            task = self._record(task_id)
            if task.session is None or task.session.get("status") != "waiting_user":
                raise TaskConflict("no_waiting_session")
            return deepcopy(task.session), None

    def _abort_answer(self, task_id: UUID, reason: str) -> None:
        """架空の再開失敗を保存し、成功として表示しない。"""
        self._fail(task_id, reason)

    def _fail(self, task_id: UUID, reason: str) -> None:
        """失敗理由を限定した架空Taskの失敗状態を保持する。"""
        with self._lock:
            task = self._record(task_id)
            task.status = "failed"
            task.question = None
            task.error = reason

    @staticmethod
    def _reject_source(*_args, **_kwargs):
        """隔離試作から実PKB/Web/Financeにアクセスすることを禁止する。"""
        raise RuntimeError("synthetic_source_access_disabled")

    @staticmethod
    def _unsupported_review(*_args, **_kwargs):
        """Proposal Reviewは今回の依頼画面試作の対象外とする。"""
        raise TaskConflict("proposal_review_out_of_scope")
