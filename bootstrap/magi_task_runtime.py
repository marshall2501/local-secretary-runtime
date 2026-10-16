# Provenance-Origin: original
# Provenance-AI: AI-generated
"""既存RITSUKO/MAGI/PKBの実行PortをVueとNiceGUIから共通利用できる形に組み立てる。

具象RepositoryやCredential取得経路はComposition Rootだけが選ぶ。
このモジュールをimportしただけではDB接続・Task作成・MAGI送信をしない。
通常8097の読取プレビューにはまだ注入しない。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import partial
from typing import Callable

from bootstrap.ritsuko_task_composition import build_task_application
from ritsuko.application import task_records
from ritsuko.application.task_commands import TaskCommands
from ritsuko.application.pkb_observation import execute_magi_pkb_request
from ritsuko.application.read_dispatch import execute_core_read
from ritsuko.application.source_execution import execute_magi_source_request
from ritsuko.core.observation_loop import (
    review_proposal, run_observation_loop, resume_user_answer,
)
from ritsuko.magi.async_execution import (
    continue_with_proposal_review_async,
    continue_with_user_clarification_async,
    continue_with_verified_observations_async,
    start_dialogue_async,
)
from ritsuko.magi.native_web import search_magi_web
from ritsuko.core.request_scope import core_answer
from capabilities.finance.application import (
    finance_core_answer, query_finance_text,
)
from capabilities.web_research.application import (
    research_text, web_core_answer,
)
from pkb.application.daily import (
    entity_map as load_entity_map,
    search_text as search_pkb_text,
)


@dataclass(frozen=True)
class MagiTaskBindings:
    """RITSUKO実Taskが必要とする状態保存・Source・設定の具象依存をまとめる。

    参照元が同じ運用DBであることを呼出し側で保証する。テストでは
    fake Repositoryへ差し替えるが、Applicationへ直接DB接続は持ち込まない。

    Attributes:
        magi_repository: Task/Action/Resultの永続化とclaimを管理するRepository。
        pkb_repository: EntityとPKB観測を読み取るRepository。
        finance_repository: 家計を読み取るRepository。
        queries: 確定Taskと保存済みMAGI sessionを読むQuery Service。
        member_specs_loader: 現在有効なMAGI席の設定を読み取る関数。
        policy_snapshot_loader: Task開始時の権限Snapshotを読む関数。
        native_web_search: 許可済み公開検索文を検索Providerへ渡す関数。
    """

    magi_repository: object
    pkb_repository: object
    finance_repository: object
    queries: object
    member_specs_loader: Callable[[], list[dict]]
    policy_snapshot_loader: Callable[[], dict | None]
    native_web_search: Callable = search_magi_web


def build_magi_task_commands(
    bindings: MagiTaskBindings,
    *,
    dialogue_starter: Callable = start_dialogue_async,
    observation_continuation: Callable = continue_with_verified_observations_async,
    user_continuation: Callable = continue_with_user_clarification_async,
    review_continuation: Callable = continue_with_proposal_review_async,
) -> TaskCommands:
    """共有Portに実Repositoryを接続し、画面非依存のTask受付を構築する。

    テストでMAGI対話関数を差し替えられるため、検証時に外部LLM送信しない。
    対話の確定・失敗・claimは既存RITSUKO処理とRepositoryに委譲する。

    Args:
        bindings: 同一Runtimeの実DBとSource依存の集合。
        dialogue_starter: 初回MAGI対話。通常は既存非同期MAGI。
        observation_continuation: 観測後にMAGIへ再判断させる関数。
        user_continuation: 本人回答後の同一Task再開関数。
        review_continuation: 本人Review後の評価関数。

    Returns:
        TaskCommands: Task ID単位で受付状況を管理するコマンド入口。
    """
    magi = bindings.magi_repository
    pkb = bindings.pkb_repository
    finance = bindings.finance_repository

    def core_read(capability: str, query: str) -> dict:
        """Source種別を既存Capabilityへ対応させ、回答整形を共有する。"""
        return execute_core_read(
            capability, query,
            pkb_search=lambda text: search_pkb_text(text, repository=pkb),
            finance_read=lambda text: query_finance_text(text, repository=finance),
            web_research=research_text,
            pkb_answer=core_answer,
            finance_answer=finance_core_answer,
            web_answer=web_core_answer,
        )

    def pkb_read(user_raw: str, pending: dict) -> dict:
        """対象PC/部品が曖昧なPKB依頼を広い検索へ自動拡張せず処理する。"""
        return execute_magi_pkb_request(
            user_raw, pending, pkb_repository=pkb,
            entity_map=lambda: load_entity_map(pkb), execute_read=core_read,
        )

    def source_read(user_raw: str, pending: dict, session: dict) -> dict:
        """Taskに保存された権限を使って既存PKB・Web・Financeを読み取る。"""
        return execute_magi_source_request(
            user_raw, pending, session,
            pkb_execute=pkb_read, execute_read=core_read,
            native_web_search=bindings.native_web_search,
        )

    async def begin_dialogue(user_raw: str, **kwargs) -> dict:
        """新しいTaskに権限Snapshotを固定し、MAGI対話へ引き渡す。

        再開時は保存済みsessionのSnapshotを使うため再取得しない。
        """
        policy = await asyncio.to_thread(bindings.policy_snapshot_loader)
        return await dialogue_starter(
            user_raw, policy_snapshot=policy, **kwargs,
        )

    async def start_runner(user_raw: str, **kwargs) -> dict:
        """DB受付・Source観測・MAGI対話を既存RITSUKO開始ループで実行する。"""
        return await run_observation_loop(
            user_raw, dialogue_starter=begin_dialogue,
            observation_continuation=observation_continuation,
            **kwargs,
        )

    async def answer_runner(task_id, reply: str, **kwargs) -> dict:
        """Task IDと保存済みMAGI sessionを使い、本人回答を既存経路で再開する。"""
        return await resume_user_answer(
            task_id, reply, user_continuation=user_continuation, **kwargs,
        )

    async def review_runner(task_id, decision: str, **kwargs) -> dict:
        """本人が選択した回答Reviewを既存RITSUKOの再評価・確定に渡す。"""
        return await review_proposal(
            task_id, decision, review_continuation=review_continuation,
            **kwargs,
        )

    application = build_task_application(
        create_task_record=partial(task_records.create_task_record, magi),
        execute_source_request=source_read,
        record_source_read_record=partial(task_records.record_source_read_record, magi),
        persist_session_record=partial(task_records.persist_session_record, magi),
        fail_task_record=partial(task_records.fail_task_record, magi),
        claim_user_resume_record=partial(task_records.claim_user_resume_record, magi),
        abort_user_resume_record=partial(task_records.abort_user_resume_record, magi),
        claim_proposal_review_record=partial(task_records.claim_proposal_review_record, magi),
        finalize_proposal_review_record=partial(task_records.finalize_proposal_review_record, magi),
        abort_proposal_review_record=partial(task_records.abort_proposal_review_record, magi),
        start_runner=start_runner,
        resume_runner=answer_runner,
        review_runner=review_runner,
    )
    return TaskCommands(
        application, bindings.queries, bindings.member_specs_loader,
    )


def build_daily_magi_task_commands() -> TaskCommands:
    """通常日常RuntimeのRepositoryとCredentialを組み立てる。

    実Taskのwriteを行うのはこの関数ではなく、返したCommandsへ
    本人がTaskを依頼した後。通常8097のmainからはまだ呼び出さない。
    """
    from bootstrap.web_runtime import (
        build_core_task_queries,
        build_finance_repository,
        build_magi_settings_repository,
        build_magi_task_repository,
        build_pkb_repository,
        build_runtime_policy_repository,
        build_service_connection_repository,
    )
    from integrations.connections.credential_resolver import (
        register_connection_credential_loader,
    )
    from integrations.connections.service_connections import (
        get_connection_auth_value,
    )
    from ritsuko.magi.settings import load_member_specs

    connections = build_service_connection_repository()
    register_connection_credential_loader(
        lambda connection_id: get_connection_auth_value(
            connections, connection_id, "api_key",
        )
    )
    settings = build_magi_settings_repository()
    policy = build_runtime_policy_repository()
    return build_magi_task_commands(MagiTaskBindings(
        magi_repository=build_magi_task_repository(),
        pkb_repository=build_pkb_repository(),
        finance_repository=build_finance_repository(),
        queries=build_core_task_queries(),
        member_specs_loader=lambda: load_member_specs(settings),
        policy_snapshot_loader=lambda: policy.load_snapshot_or_none("lan"),
    ))
