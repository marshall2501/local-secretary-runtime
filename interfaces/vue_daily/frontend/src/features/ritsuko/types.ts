// Provenance-Origin: original
// Provenance-AI: AI-generated
/** RITSUKO確定TaskとMAGI内部候補を明確に分ける表示用API型。 */
/** Task一覧の絞り込み。サーバー側Queryの範囲指定と一致させる。 */
export type TaskListView = 'recent' | 'open' | 'completed'
/** 右側Task一覧のための保存済みTask最小表示情報。 */
export interface TaskSummary {
  id: string
  request: string
  status: string
  revision: number
  core_slice?: string
  updated_at?: string
}
/** 現在Task欄で表示する確定状態とMAGI進捗の保存情報。 */
export interface TaskRecord extends TaskSummary {
  message?: string | null
  question?: string | null
  phase?: string
  magi_session?: { status?: string; next_step?: string } | null
}
/** Actionの状態とResultの識別子を別々に保持し、未完了を成功扱いしない。 */
export interface TaskAction {
  action_id?: string
  tool?: string
  operation?: string
  action_status?: string
  result_id?: string | null
  outcome?: string | null
  summary?: string | null
}
/** 選択中Taskの正本と監査用履歴を一緒に画面へ渡す。 */
export interface TaskTrace {
  task: TaskRecord
  actions: TaskAction[]
  advisor_events: Array<{ event_type: string }>
}
const labels: Record<string, string> = {
  pending: '受付', queued: '受付', running: '処理中',
  waiting_external: '本人への確認待ち', waiting_user: '本人の回答待ち',
  paused: '停止', stopped: '停止', completed: '完了',
  failed: '失敗', cancelled: '取消',
}
/** RITSUKOが確定した保存状態だけを表示文字列に変換する。 */
export function taskStatusLabel(status: string): string {
  return labels[status] ?? status
}
/** 右一覧の分類に使用する。停止中は再開可能なため終了済みに含めない。 */
export function isTaskClosed(status: string): boolean {
  return ['completed', 'failed', 'cancelled'].includes(status)
}
