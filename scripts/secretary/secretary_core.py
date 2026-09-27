"""Runnable read-only Secretary Core: plan -> delegate -> verify -> replan -> answer.

This is the FIRST integrated manager, not a general autonomous PC operator.
It reuses the existing Ollama and Secretary API; checkpoint files stay local.
Entry point: python scripts/secretary/secretary_core.py "question" --mode fixture
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import sys
import uuid
from typing import TypedDict

from langgraph.graph import END, StateGraph

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "framework_compare"))
import ask
from shared import Evidence, FICTIONAL_NOTES

MAX_DELEGATIONS = 4

RECALL_REQUEST_TERMS = ("以前", "過去", "前回", "これまで", "履歴", "実施済み", "経験")


def requires_recall(request: str) -> bool:
    """The user explicitly requested their history, regardless of LLM choice."""
    return any(term in request for term in RECALL_REQUEST_TERMS)
DEFAULT_STATE_DIR = ask.ROOT / "secrets" / "secretary-core-states"


class WorkState(TypedDict, total=False):
    task_id: str
    original_request: str
    target: str
    domain: str
    mode: str
    model: str
    user_update: str
    status: str
    turns: int
    review_attempts: int
    decision: dict
    feedback: str
    observations: list[dict]
    events: list[dict]
    answer: str
    awaiting: str


def save_state(state: WorkState, folder: Path) -> None:
    """Atomic local checkpoint, intentionally not presented as DB Action/Result."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (state["task_id"] + ".json")
    temp = folder / (state["task_id"] + ".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    os.replace(temp, path)


def load_state(task_id: str, folder: Path) -> WorkState:
    valid_id = str(uuid.UUID(task_id))
    data = json.loads((folder / (valid_id + ".json")).read_text(encoding="utf-8"))
    if data.get("task_id") != valid_id:
        raise ValueError("Checkpoint task_id mismatch")
    return data



def evidence_based_fallback(state: WorkState) -> str:
    """Produce a bounded, provenance-aware report without trusting a failed LLM review.

    A rejected answer does not mean the underlying memory retrieval failed.
    Use tool observations to distinguish successful lookup, lookup failure,
    unverified claims and unlinked simulated actions. Domain-neutral by design.
    """
    observations = state.get("observations") or []
    memory = [x for x in observations if x.get("specialist") == "memory"]
    successful = [x for x in memory if not x.get("error")]
    parts = []

    if successful:
        latest = successful[-1]
        parts.append("記憶の照会は完了しました。")
        records = latest.get("records") or []
        if records:
            parts.append("取得できた記録（確定事実とは限りません）:")
            for record in records[:5]:
                qualifiers = []
                if record.get("simulated"):
                    qualifiers.append("模擬")
                if record.get("linkage") == "unlinked_same_domain_not_proof_of_target":
                    qualifiers.append("今回の対象に紐付けなし")
                if record.get("verification") in ("unverified", "unknown"):
                    qualifiers.append("未検証")
                if record.get("verification") == "fictional_not_real_reference":
                    qualifiers.append("架空資料")
                if not qualifiers:
                    qualifiers.append("記録上の情報・最新の実観測ではない")
                title = str(record.get("id") or "記録")
                detail = str(record.get("text") or "内容不明")[:180]
                parts.append(f"{title}: {detail}（{'・'.join(qualifiers)}）")
        else:
            parts.append("今回取得できた検索範囲に対象の記録はありません。"
                         "DB全体に存在しないことは確認できていません。")
        if latest.get("source_mode") == "fixture":
            parts.append("これらは試験用の架空データであり、実機の診断結果ではありません。")
    elif memory:
        parts.append("記憶への照会を試みましたが取得に失敗しました。"
                     "過去の記録の有無は確認できていません。")
    else:
        parts.append("過去の記憶はまだ取得できていません。"
                     "記録が存在しないとは断定できません。")

    research = [x for x in observations
                if x.get("specialist") == "research" and not x.get("error")]
    if research:
        parts.append("取得した参考資料の出典・適用条件も実際の対象との照合が必要です。"
                     "架空資料だけでは実際の原因を証明できません。")
    parts.append("現在の情報だけでは依頼の結論を確定できません。")
    if state.get("domain") == "pc":
        parts.append("次に必要なのは、現在の症状・発生時刻・実際のエラーログと"
                     "構成や更新履歴の確認です。実機の設定変更は行っていません。")
    else:
        parts.append("次に必要なのは、現在の状況と不足している根拠の確認です。")
    return "\n".join(parts)


def _json_llm(model: str, instruction: str, context: dict) -> dict:
    response = ask.ollama([
        {"role": "system", "content": instruction},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False,
                                              default=str)},
    ], model=model, json_output=True)
    try:
        parsed = json.loads(response)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("LLM did not return valid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("LLM JSON result must be an object")
    return parsed


MANAGER_RULES = (
    "あなたは個人秘書の統括役。ユーザーの元の依頼範囲を絶対に拡大しない。"
    "必要なら記憶担当(memory)・資料担当(research)に委任し、返った観測の品質を見て、"
    "再依頼・別担当・質問・回答を選び直す。以前の観測があるときは再利用する。"
    "模擬情報は実観測ではない。entityが未紐付けのActionを指定PCの実績と断定しない。"
    "調査で解決しない場合は不明点を明示して回答してよい。"
    "ユーザーから今聞くことで次の判断が変わる具体的な情報が不足しているなら、"
    "ask_userで最も重要な質問を1つ返し、回答を待つ。質問が不要なら"
    "判明した内容と限界を示してanswerする。既に取得した証拠は捨てない。"
    "明示的な実行依頼と承認がないので変更・購入・メール送信等を提案実行しない。"
    "JSONのみ返す。"
    '{"action":"delegate|answer|ask_user","specialist":"memory|research",'
    '"query":"担当への質問または検索語","reason":"理由",'
    '"response":"回答または本人への質問","used_ids":["M1","R1"]}。'
    "action=delegateの時のみspecialistとqueryが必要。回答は出典IDがある時だけ挙げる。"
)


class SecretaryCore:
    def __init__(self, folder: Path = DEFAULT_STATE_DIR):
        self.folder = Path(folder)
        self._evidence = None
        self.graph = self._build_graph()

    def _persist(self, state: WorkState) -> WorkState:
        save_state(state, self.folder)
        return state

    def manager(self, state: WorkState) -> WorkState:
        state["turns"] = state.get("turns", 0) + 1
        if state["turns"] > 9 or sum(
                x.get("type") == "dispatch" for x in state["events"]
                ) >= MAX_DELEGATIONS:
            state["decision"] = {
                "action": "answer", "response": evidence_based_fallback(state),
                "used_ids": [], "skip_review": True,
                "reason": "安全な調査上限のため取得済み証拠を限定報告",
            }
        else:
            observations = state["observations"]
            # Preserve the user's instruction to consult past experience. If a
            # prior turn asked the user or consulted research first, the same
            # task still retrieves memory before deciding what to do next.
            recall_needed = (
                requires_recall(state["original_request"])
                and not any(o.get("specialist") == "memory" for o in observations)
                and not any(e.get("type") == "dispatch" and e.get("to") == "memory"
                            for e in state["events"])
            )
            if recall_needed:
                decision = {
                    "action": "delegate", "specialist": "memory",
                    "query": state["target"] + "の過去の経験・対策・結果",
                    "reason": "元の依頼が過去の経験の確認を要求しているため先に照会",
                    "used_ids": [],
                }
            else:
                decision = _json_llm(state["model"], MANAGER_RULES, {
                    "request": state["original_request"],
                    "new_user_information": state.get("user_update", ""),
                    "target": state["target"], "domain": state["domain"],
                    "previous_observations": observations[-6:],
                    "previous_actions": state["events"][-9:],
                    "validation_feedback": state.get("feedback", ""),
                    "remaining_delegations": MAX_DELEGATIONS - sum(
                        x.get("type") == "dispatch" for x in state["events"]),
                })
            if decision.get("action") not in ("delegate", "answer", "ask_user"):
                state["feedback"] = "無効なaction。delegate, answer, ask_userから選択"
                decision = {"action": "answer", "response": (
                    "現在の情報では結論を出せません。必要な確認事項を整理してから回答します。"),
                    "used_ids": [], "reason": "不正な判断形式"}
            state["decision"] = decision
        decision_event = {
            "type": "decision", "action": state["decision"].get("action"),
            "specialist": (state["decision"].get("specialist")
                           if state["decision"].get("action") == "delegate" else None),
            "reason": state["decision"].get("reason", ""),
        }
        # Future diagnostic exports must contain the exact question and
        # proposed answers, including drafts later rejected by the reviewer.
        # Existing checkpoints remain readable (these fields may be absent).
        if state["decision"].get("action") == "ask_user":
            decision_event["question"] = str(state["decision"].get("response") or "")
        elif state["decision"].get("action") == "answer":
            decision_event["draft_answer"] = str(state["decision"].get("response") or "")
        state["events"].append(decision_event)
        return self._persist(state)

    def dispatch(self, state: WorkState) -> WorkState:
        decision = state["decision"]
        specialist = decision.get("specialist")
        query = str(decision.get("query") or state["original_request"])[:240]
        if specialist not in ("memory", "research"):
            state["feedback"] = "存在しない専門担当が指定された。memoryかresearchのみ利用可"
            state["events"].append({"type": "invalid_specialist", "name": specialist})
            return self._persist(state)

        # Repetition without changed evidence is not progress. Changed user
        # input or an intervening observation permits a fresh request.
        signature = (specialist, query.casefold().strip())
        # 'user_update' remains in the state for the manager's context, so
        # testing its non-emptiness here wrongly allowed every duplicate call
        # after a single user reply. Check event ordering instead.
        previous_same = [
            i for i, event in enumerate(state["events"])
            if event.get("type") == "dispatch"
            and event.get("signature") == list(signature)
        ]
        if previous_same:
            intervening = state["events"][previous_same[-1] + 1:]
            has_new_input = any(e.get("type") == "user_update" for e in intervening)
            has_other_delegation = any(
                e.get("type") == "dispatch" and e.get("to") != specialist
                for e in intervening
            )
            if not has_new_input and not has_other_delegation:
                state["feedback"] = (
                    "新情報のない同一依頼を停止。取得済みの資料を再利用し、"
                    "別の担当・質問・現状報告から次の手を選ぶ"
                )
                state["events"].append({
                    "type": "duplicate_blocked", "to": specialist,
                    "query": query,
                })
                return self._persist(state)

        state["events"].append({
            "type": "dispatch", "action": "delegate",
            "to": specialist, "query": query,
            "signature": list(signature),
            "observation_count": len(state["observations"]),
        })
        self._persist(state)
        try:
            if specialist == "memory":
                result = self._memory(state)
            else:
                result = self._research(state, query)
            if specialist == "research" and any(
                    o.get("specialist") == "research"
                    and not o.get("error")
                    and o.get("records") == result.get("records")
                    for o in state["observations"]):
                state["feedback"] = (
                    "検索条件を変更したが、新しい資料は得られなかった。"
                    "取得済み資料を再利用し、別の情報源かユーザーへの質問を検討"
                )
                state["events"].append({
                    "type": "evidence_unchanged", "to": specialist,
                    "query": query, "feedback": state["feedback"],
                })
                return self._persist(state)
            state["observations"].append(result)
            state["feedback"] = ""
        except Exception as exc:
            # Never pretend an unavailable API means the user has no history.
            state["observations"].append({
                "specialist": specialist, "records": [],
                "coverage": "unavailable",
                "error": str(exc), "query": query,
            })
            state["feedback"] = ("取得処理が失敗。記憶なしとは断定しない。"
                                 "再依頼・別担当・状況報告を選ぶ")
        return self._persist(state)

    def _memory(self, state: WorkState) -> dict:
        target, domain = state["target"], state["domain"]
        if state["mode"] == "fixture":
            raw = json.loads(self._evidence.recall())
            # The shared fixture intentionally names one fictional PC.
            if target != "架空テストPC" or domain != "pc":
                raise ValueError("Fixture only covers 架空テストPC / pc")
            records = []
            for record in raw["claims"]:
                records.append({
                    "id": "M" + str(len(records) + 1),
                    "entity_name": record.get("entity_name"),
                    "kind": record.get("kind"),
                    "text": record.get("title", "") + "="
                            + str(record.get("value_text")),
                    "verification": record.get("state"),
                    "source": record.get("source_citation"),
                    "simulated": False, "linkage": "explicit_entity_match",
                })
            for record in raw["previous_actions"]:
                records.append({
                    "id": "M" + str(len(records) + 1),
                    "entity_name": record.get("entity_name"),
                    "kind": "action_result",
                    "text": str(record.get("summary") or record.get("outcome")),
                    "verification": "simulated" if record.get("simulated")
                                    else "unverified",
                    "source": record.get("source_citation"),
                    "simulated": bool(record.get("simulated")),
                    "linkage": record.get("association"),
                })
            coverage = raw["coverage"]
        else:
            token_file = Path(os.getenv(
                "LSA_SECRETARY_API_TOKEN_FILE",
                str(ask.ROOT / "secrets" / "secretary-api-token.txt"),
            )).expanduser()
            if not token_file.is_file():
                raise RuntimeError("Secret API token not found: set LSA_SECRETARY_API_TOKEN_FILE; no fallback to fixture")
            token = token_file.read_text(encoding="utf-8-sig").strip()
            if not token:
                raise RuntimeError("Secret API token empty")
            claims = ask.search(token, domain=domain)
            actions = ask.search_experience(token, domain=domain)
            def same_target(row):
                return "".join(str(row.get("entity_name") or "").casefold().split()
                               ) == "".join(target.casefold().split())
            records = []
            for row in claims.get("items") or []:
                if not same_target(row):
                    continue
                records.append({
                    "id": "M" + str(len(records) + 1),
                    "entity_name": row.get("entity_name"),
                    "kind": row.get("kind"),
                    "text": str(row.get("title")) + " " + str(row.get("value_text")),
                    "verification": row.get("state"),
                    "source": row.get("source_citation"),
                    "source_id": str(row.get("source_id")),
                    "simulated": False, "linkage": "explicit_entity_match",
                })
            for row in actions.get("items") or []:
                if row.get("entity_name") and not same_target(row):
                    continue
                simulated = (
                    row.get("tool") == "prototype_mock"
                    or (isinstance(row.get("evidence"), dict) and
                        bool(row["evidence"].get("simulated")))
                )
                records.append({
                    "id": "M" + str(len(records) + 1),
                    "entity_name": row.get("entity_name"),
                    "kind": "action_result",
                    "text": str(row.get("summary") or row.get("outcome")),
                    "verification": "simulated" if simulated else "unverified",
                    "source": row.get("source_citation"),
                    "simulated": simulated,
                    "linkage": ("explicit_entity_match" if row.get("entity_name")
                                else "unlinked_same_domain_not_proof_of_target"),
                })
            coverage = ("first-page domain records only; memory total="
                        + str(claims.get("total")) + ", experience total="
                        + str(actions.get("total")))
        return {"specialist": "memory", "target": target, "domain": domain,
                "records": records[:20], "coverage": coverage, "source_mode": state["mode"]}

    def _research(self, state: WorkState, query: str) -> dict:
        if state["mode"] == "live":
            raise RuntimeError("Real research adapter not connected; never substitute fictional sources")
        # Fictional fixture only; preserve provenance.
        data = json.loads(self._evidence.research(query))
        return {
            "specialist": "research", "query": query,
            "records": [{
                "id": "R" + str(n + 1),
                "text": str(record.get("title")) + ": " + str(record.get("text")),
                "source": record.get("provenance"),
                "verification": "fictional_not_real_reference",
                "simulated": True,
            } for n, record in enumerate(data["sources"])],
            "coverage": data["coverage"], "source_mode": "fictional_fixture",
        }

    def evaluate(self, state: WorkState) -> WorkState:
        if state["events"][-1].get("type") in (
                "duplicate_blocked", "invalid_specialist", "evidence_unchanged"):
            # Dispatch deliberately returned no new result; preserve its error.
            state["events"].append({"type": "evaluation", "feedback": state["feedback"]})
            return self._persist(state)
        result = state["observations"][-1] if state["observations"] else None
        if not result or result.get("error"):
            state["feedback"] = "専門担当がデータを取得できなかった。理由を踏まえて次の手を選ぶ"
        else:
            records = result.get("records") or []
            if result["specialist"] == "memory":
                wrong = [r for r in records if
                         r.get("entity_name") and
                         "".join(r["entity_name"].casefold().split()) !=
                         "".join(state["target"].casefold().split())]
                if wrong:
                    result["records"] = [r for r in records if r not in wrong]
                    state["feedback"] = "対象外の記憶を除外した。現在の対象と照合する"
                elif not records:
                    state["feedback"] = ("この検索範囲で該当記憶なし。"
                                         "DB全体に不存在と断定しない")
                else:
                    state["feedback"] = (
                        "対象未紐付け・模擬・未検証記録は診断の証明にならない。"
                        "必要なら別担当へ依頼するか、現時点の結論を回答する")
            else:
                state["feedback"] = (
                    "取得した資料は架空の参考資料であり実機の根拠ではない。"
                    "新しい証拠がなければ原因を確定せず回答する")
        state["events"].append({"type": "evaluation",
                                "specialist": result.get("specialist") if result else None,
                                "feedback": state["feedback"]})
        return self._persist(state)

    def final(self, state: WorkState) -> WorkState:
        decision = state["decision"]
        if decision["action"] == "ask_user":
            state["awaiting"] = str(decision.get("response") or
                                    "もう少し状況を教えてください")
            state["answer"] = state["awaiting"]
            state["status"] = "waiting_user"
        else:
            answer = str(decision.get("response") or "").strip()
            known = {r.get("id") for obs in state["observations"]
                     for r in obs.get("records") or []}
            cited = (set(str(x) for x in decision.get("used_ids", []))
                     | set(re.findall(r"\b[MR][0-9]+\b", answer)))
            prior_checked = any(obs.get("specialist") == "memory"
                                and not obs.get("error") for obs in state["observations"])
            asks_history = any(word in state["original_request"]
                               for word in ("以前", "過去", "前回", "これまで"))
            validation_error = ""
            if not decision.get("skip_review") and not answer:
                validation_error = "回答が空です。取得済みの証拠を使い、限定的に回答する"
            elif not decision.get("skip_review") and (
                    cited - known or (asks_history and not prior_checked)):
                validation_error = (
                    "引用IDが不正か、以前の経験を未照会。確認せず不存在を宣言できない")
            elif not decision.get("skip_review") and state.get("review_attempts", 0) < 2:
                # Independent second LLM call to check content, not just schema.
                # The reviewer sees actual observations rather than manager prose.
                try:
                    review = _json_llm(state["model"], (
                        "あなたは独立した回答監査担当。与えられた観測の範囲だけで"
                        "回答の主張が成立するか判定。模擬資料を実機事実としない。"
                        "未紐付けのActionを対象PCの記録と断定しない。"
                        "回答に根本原因の特定を要求しない。「原因は未特定」や"
                        "「現時点の証拠では断定できない」は、事実に矛盾しなければ"
                        "適切な限定的回答としてsupported=true。"
                        "supported=falseは、観測と矛盾する具体的な断定や、"
                        "架空・未紐付け記録を実機の確定結果と偽る主張がある場合だけ。"
                        "その場合、具体的な問題箇所と対応する根拠をfeedbackで示す。"
                        'JSONのみ: {"supported":true|false,"feedback":"理由"}'
                    ), {
                        "original_request": state["original_request"],
                        "answer": answer,
                        "observations": state["observations"],
                        "used_ids": list(cited),
                    })
                    state["events"].append({"type": "answer_review",
                                            "supported": review.get("supported"),
                                            "feedback": review.get("feedback", "")})
                    if review.get("supported") is not True:
                        validation_error = ("独立監査で根拠不足: "
                                            + str(review.get("feedback", "")))
                except Exception as exc:
                    validation_error = "回答の独立監査に失敗: " + str(exc)
            if validation_error:
                state["feedback"] = validation_error
                state["review_attempts"] = state.get("review_attempts", 0) + 1
                if state["review_attempts"] < 2:
                    state["decision"] = {"action": "retry_manager"}
                    return self._persist(state)
                # The reviewer may itself be mistaken. Never claim the
                # successful memory lookup failed just because it rejected
                # two candidate answers. Return a verifiable evidence report.
                answer = evidence_based_fallback(state)
                state["events"].append({
                    "type": "evidence_fallback",
                    "reason": "回答監査が2回不合格。確認済みの取得結果のみで限定報告",
                    "memory_retrieved": prior_checked,
                })
            if not answer:
                answer = "現時点では回答を生成できませんでした。"
            state["answer"] = answer
            state["status"] = "answered"
        state["events"].append({"type": "finish", "status": state["status"]})
        return self._persist(state)

    @staticmethod
    def route_manager(state: WorkState) -> str:
        return "dispatch" if state["decision"]["action"] == "delegate" else "final"

    @staticmethod
    def route_final(state: WorkState) -> str:
        return "manager" if state["decision"]["action"] == "retry_manager" else "end"

    def _build_graph(self):
        g = StateGraph(WorkState)
        g.add_node("manager", self.manager)
        g.add_node("dispatch", self.dispatch)
        g.add_node("evaluate", self.evaluate)
        g.add_node("final", self.final)
        g.set_entry_point("manager")
        g.add_conditional_edges("manager", self.route_manager,
                                {"dispatch": "dispatch", "final": "final"})
        g.add_edge("dispatch", "evaluate")
        g.add_edge("evaluate", "manager")
        g.add_conditional_edges("final", self.route_final,
                                {"manager": "manager", "end": END})
        return g.compile()

    def run(self, state: WorkState) -> WorkState:
        if state["status"] in ("answered", "waiting_user"):
            return state
        if state["mode"] not in ("fixture", "live"):
            raise ValueError("Unknown mode")
        self._evidence = Evidence("fixture")
        try:
            result = self.graph.invoke(state, config={"recursion_limit": 45})
        except Exception:
            # Each completed node has already checkpointed. Never replace its
            # newer state with the stale invocation input on failure.
            raise
        return result

    def start(self, request: str, *, target: str, domain: str,
              mode: str = "fixture", model: str = "qwen3:8b",
              task_id: str | None = None) -> WorkState:
        if not request.strip() or not target.strip() or not domain.strip():
            raise ValueError("Request, target and domain cannot be empty")
        if mode == "fixture" and (target, domain) != ("架空テストPC", "pc"):
            raise ValueError("Fixture supports only 架空テストPC / pc")
        new_id = str(uuid.UUID(task_id)) if task_id else str(uuid.uuid4())
        if (self.folder / (new_id + ".json")).exists():
            raise ValueError("Task ID already exists; use resume instead of overwriting")
        state: WorkState = {
            "task_id": new_id, "original_request": request,
            "target": target, "domain": domain, "mode": mode, "model": model,
            "status": "running", "turns": 0, "review_attempts": 0,
            "feedback": "", "observations": [], "events": [],
        }
        save_state(state, self.folder)
        return self.run(state)

    def resume(self, task_id: str, user_update: str = "") -> WorkState:
        state = load_state(task_id, self.folder)
        if state["status"] == "answered":
            return state
        if state["status"] == "waiting_user" and not user_update.strip():
            return state
        if user_update.strip():
            state["user_update"] = user_update.strip()
            state["events"].append({"type": "user_update",
                                    "text": user_update.strip()})
        state["status"] = "running"
        return self.run(state)


def main():
    parser = argparse.ArgumentParser(description="Read-only Secretary Core")
    parser.add_argument("question", nargs="?")
    parser.add_argument("--target", default="架空テストPC")
    parser.add_argument("--domain", default="pc")
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--resume", metavar="TASK_ID")
    parser.add_argument("--reply", default="", help="New information for paused work")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    args = parser.parse_args()
    core = SecretaryCore(args.state_dir)
    if args.resume:
        result = core.resume(args.resume, args.reply)
    else:
        if not args.question:
            parser.error("Specify a question or --resume TASK_ID")
        result = core.start(args.question, target=args.target,
                            domain=args.domain, mode=args.mode,
                            model=args.model)
    print(json.dumps({
        "task_id": result["task_id"], "status": result["status"],
        "answer": result.get("answer"), "events": result["events"],
        "observations": result["observations"],
        "checkpoint": str(args.state_dir / (result["task_id"] + ".json")),
        "notice": ("Local checkpoint only; DB Task/Action/Result linking and "
                   "real-web tools are not yet implemented."),
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
