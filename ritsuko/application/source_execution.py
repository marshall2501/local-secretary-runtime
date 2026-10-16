# Provenance-Origin: original
# Provenance-AI: AI-generated
"""MAGIから要求されたPKB・Web・Finance観測の共通実行入口。

画面から業務処理を外すため、検索関数とRepositoryをPortとして受け取る。
既存Policy・Webクエリ・公開語句制限を変更せずに利用する。
"""
from __future__ import annotations

from ritsuko.application.context_policy import public_web_search_permission
from ritsuko.application.observation_sources import (
    finance_query_from_request, web_query_from_request,
)


def execute_magi_source_request(
    user_raw: str,
    pending_request: dict,
    session: dict,
    *,
    pkb_execute,
    execute_read,
    native_web_search,
) -> dict:
    """既存RITSUKOのPKB・Web・家計の読取を、画面から独立して実行する。

    Webへ送る検索文は既存のPolicyと私的情報の境界で検査し、
    native検索失敗時も同じ許可済みクエリだけを既存検索へ渡す。

    Args:
        user_raw: Task受付時の依頼本文。
        pending_request: MAGIが指定したSourceおよび必要情報。
        session: Task開始時の権限Snapshot等を含むMAGI session。
        pkb_execute: 既存PKB対象解決・読取を行う関数。
        execute_read: Finance/Webの既存Capabilityを呼ぶ関数。
        native_web_search: MAGIに紐づく非同期Web検索関数。

    Returns:
        dict: Source読取結果。Taskへの保存は呼出し側が行う。
    """
    source = str(pending_request.get("source") or "").strip()
    if source == "pkb":
        return pkb_execute(user_raw, pending_request)
    if source == "web":
        query = web_query_from_request(pending_request, session)
        permission = public_web_search_permission(session.get("policy_snapshot"))
        if not permission["allowed"]:
            return {
                "status": "error", "source": "web",
                "capability": "web_research", "confidentiality": "public",
                "operation": "research", "total": 0, "answer": "",
                "result": {"result_kind": "web_search_permission_denied",
                           "reason": permission["reason"]},
                "error_type": permission["reason"], "verified_by": None,
            }
        # Observation loopが別スレッドで実行するため、ここで非同期検索を起動できる。
        # 外部に送るのは許可済みの公開検索文だけで、Task原文や私的PKBは渡さない。
        import anyio
        native = anyio.run(native_web_search, query,
                           session.get("member_specs") or [])
        if native.get("status") == "ok":
            return native
        # 既存のWeb検索へ戻す場合も、同じ許可済み公開クエリを再利用する。
        result = execute_read("web_research", query)
        source_metadata = dict(result.get("source_metadata") or {})
        source_metadata["native_web_fallback"] = {
            "status": native.get("status"),
            "member_runs": (native.get("result") or {}).get("member_runs") or [],
        }
        return {"source": "web", **result,
                "source_metadata": source_metadata}
    if source == "finance":
        query = finance_query_from_request(pending_request, session=session)
        result = execute_read("finance_read", query)
        return {"source": "finance", **result}
    raise ValueError("unsupported_observation_source")
