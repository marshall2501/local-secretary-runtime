"""Read-only OODA projection; never changes Task state or stores a new phase."""
from dataclasses import dataclass
from collections.abc import Mapping, Sequence


OODA_PHASES = (
    ("observe", "Observe", "観察・情報収集"),
    ("orient", "Orient", "状況把握・意味づけ"),
    ("decide", "Decide", "意思決定・採否"),
    ("act", "Act", "行動・実行"),
)


@dataclass(frozen=True)
class OodaDisplay:
    phase: str | None
    reason: str
    terminal: str | None = None

    @property
    def label(self) -> str:
        return next((f"{name}（{note}）" for key, name, note in OODA_PHASES
                     if key == self.phase), "—")


def derive_ooda(task: Mapping | None, actions: Sequence[Mapping] = ()) -> OodaDisplay:
    """Terminal > running Action > wait > explicit phase > evidence > intake.

    A completed Action is history, not evidence that a tool is still running.
    Unknown states remain unknown rather than implying successful progress.
    """
    if not task:
        return OodaDisplay(None, "Task未選択です。依頼または保存済みTaskを選んでください。")
    status = task.get("status")
    phase = task.get("phase")
    if status == "completed":
        return OodaDisplay("decide", "Taskが完了状態です。直前段階は表示上Decideとして扱います。", status)
    if status in {"failed", "error", "cancelled", "rejected"}:
        return OodaDisplay(None, "処理は終了または中断しています。結果・エラーを確認してください。", status)
    if any(a.get("action_status") == "running" for a in actions):
        return OodaDisplay("act", "実行中のActionがあるため、選択した能力・Toolの実行段階です。")
    if status in {"waiting_external", "awaiting_clarification"} or phase == "awaiting_clarification":
        return OodaDisplay("orient", "判断に必要な追加情報・外部応答を待ち、状況を整理する段階です。")
    if phase in {"input", "observe"} or status in {"received", "queued"}:
        return OodaDisplay("observe", "依頼・追加回答を受け付けました。後続段階の結果はまだ取得していません。")
    if phase == "orient":
        return OodaDisplay("orient", "取得した情報と不足情報を整理する段階です。")
    if phase == "act":
        return OodaDisplay("act", "既存のphaseがActを示しています。選択した行動の実行段階です。")
    if phase == "decide":
        return OodaDisplay("decide", "既存のphaseがDecideを示しています。次の行動の採否を判断する段階です。")
    if actions:
        return OodaDisplay("orient", "過去のActionがあります。実行中ではなく結果・現在の状況を整理する段階です。")
    if task.get("selected_capability"):
        return OodaDisplay("decide", "能力が選択されています。実行中Actionは確認されていません。")
    if status in {"pending", "running"} and not phase:
        return OodaDisplay("observe", "Taskを受け付けています。能力選択・実行中Actionはまだ確認されていません。")
    return OodaDisplay(None, "既存状態からOODA段階を特定できません。Taskの状態・ログを確認してください。")
