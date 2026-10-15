# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Transport非依存のRITSUKO Task Application組立。

Web画面、REST adapter、隔離試験で同じPort/Runner契約を使う。
この組立関数はDB接続や外部LLM送信を行わない。具象Adapterの選択は呼び出し元に残す。
"""
from __future__ import annotations

from typing import Callable

from ritsuko.application.task_runtime import StateDrivenTaskApplication, TaskRuntimePorts
from ritsuko.core.observation_loop import (
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
) -> StateDrivenTaskApplication:
    """同じTask契約をNiceGUI・REST・合成Adapterへ渡す。

    個別のDB、MAGI、Tool依存はPort経由で注入する。
    実行の開始や完了判断をこのComposition Rootに実装しない。
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
        ),
        start_runner=start_runner,
        resume_runner=resume_runner,
        review_runner=review_runner,
        action_service=action_service,
    )
