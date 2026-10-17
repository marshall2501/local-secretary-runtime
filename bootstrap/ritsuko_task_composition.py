# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Transport非依存のRITSUKO Task Application組立。

Web画面、REST adapter、隔離試験で同じPort/Runner契約を使う。
この組立関数はDB接続や外部LLM送信を行わない。具象Adapterの選択は呼び出し元に残す。
"""
from __future__ import annotations

from typing import Callable

from ritsuko.application.task_runtime import StateDrivenTaskApplication, TaskRuntimePorts
from ritsuko.core.task_execution import (
    review_proposal,
    run_observation_loop,
    resume_user_answer,
)


def build_task_application(
    *,
    create_task_record: Callable,
    execute_source_request: Callable,
    record_source_read_record: Callable,
    persist_session_record: Callable,
    fail_task_record: Callable,
    claim_user_resume_record: Callable,
    abort_user_resume_record: Callable,
    claim_proposal_review_record: Callable,
    finalize_proposal_review_record: Callable,
    abort_proposal_review_record: Callable,
    start_runner: Callable = run_observation_loop,
    resume_runner: Callable = resume_user_answer,
    review_runner: Callable = review_proposal,
    action_service=None,
    checkpoint_source_progress: Callable | None = None,
) -> StateDrivenTaskApplication:
    """UIやHTTPに依存せず既存RITSUKOのTask処理を組み立てる。

    入力のPortとRunnerは呼び出し側が選ぶ。ここで新しいTask判断や
    DB・LLM通信を実装せず、NiceGUI・Vue隔離試験から同じ契約を使う。

    Args:
        create_task_record: Task受付時に記録を作成するPort。
        execute_source_request: 許可された観測Sourceを実行するPort。
        record_source_read_record: Sourceの読取記録を保存するPort。
        persist_session_record: MAGI sessionとTask状態を永続化するPort。
        fail_task_record: 処理失敗をTaskへ記録するPort。
        claim_user_resume_record: 本人回答で再開できるTaskを予約するPort。
        abort_user_resume_record: 回答再開が失敗した際に予約を戻すPort。
        claim_proposal_review_record: Proposal Review対象を予約するPort。
        finalize_proposal_review_record: Review判断を確定するPort。
        abort_proposal_review_record: Review失敗時に予約を戻すPort。
        start_runner: 新規TaskのObservation実行関数。
        resume_runner: 本人回答後の実行再開関数。
        review_runner: Proposal Reviewの実行関数。
        action_service: 設定された場合だけ外部Actionを扱うサービス。

    Returns:
        StateDrivenTaskApplication: 各入口で共通利用するTask制御Application。
    """
    return StateDrivenTaskApplication(
        TaskRuntimePorts(
            create_task_record=create_task_record,
            execute_source_request=execute_source_request,
            record_source_read_record=record_source_read_record,
            persist_session_record=persist_session_record,
            fail_task_record=fail_task_record,
            claim_user_resume_record=claim_user_resume_record,
            abort_user_resume_record=abort_user_resume_record,
            claim_proposal_review_record=claim_proposal_review_record,
            finalize_proposal_review_record=finalize_proposal_review_record,
            abort_proposal_review_record=abort_proposal_review_record,
            checkpoint_source_progress=checkpoint_source_progress,
        ),
        start_runner=start_runner,
        resume_runner=resume_runner,
        review_runner=review_runner,
        action_service=action_service,
    )
