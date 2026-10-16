// Provenance-Origin: original
// Provenance-AI: AI-generated
/** DBに保存したTask、MAGI途中経過、Action/Resultを混同しない表示用の型。 */
/** Task一覧の絞り込み。サーバー側Queryの範囲指定と一致させる。 */
export type TaskListView = 'recent' | 'open' | 'completed'
/** 右側Task一覧の表示項目。statusはRITSUKOが保存したTaskの状態。 */
export interface TaskSummary {
  id: string
  request: string
  /** RITSUKOが確定した状態。MAGIの回答候補の状態ではない。 */
  status: string
  /** 同じTask IDの保存更新順序を示す改訂番号。 */
  revision: number
  core_slice?: string
  updated_at?: string
}
/** 選択Taskの詳細。MAGI情報が存在してもTaskの完了を意味しない。 */
export interface TaskRecord extends TaskSummary {
  message?: string | null
  question?: string | null
  phase?: string
  /** MAGI sessionに保存された候補・次の処理。Task.statusとは独立。 */
  magi_session?: { status?: string; next_step?: string } | null
  /** DBに受理された停止要求。trueでもWorkerが止まった証明ではない。 */
  stop_requested?: boolean | null
  /** MAGI提案は本人が確定したGoalと同義ではない。 */
  goal_definition?: {
    version: number
    resolution: 'unresolved' | 'proposed' | 'confirmed'
    interpreted_goal?: string | null
    criteria?: string[]
  } | null
}
/** DBに保存されたActionとResultの参照情報。Resultが未記録の場合もある。 */
export interface TaskAction {
  action_id?: string
  tool?: string
  operation?: string
  action_status?: string
  result_id?: string | null
  outcome?: string | null
  summary?: string | null
}
/** Task詳細APIの応答。現在の画面はAction履歴のみ展開表示する。 */
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
/** 一覧の「終了済み」分類に使う。停止中は終了状態と決めつけない。 */
export function isTaskClosed(status: string): boolean {
  return ['completed', 'failed', 'cancelled'].includes(status)
}
