// Provenance-Origin: original
// Provenance-AI: AI-generated
/** RITSUKO確定TaskとMAGI内部候補を明確に分ける表示用API型。 */
export type TaskListView = 'recent' | 'open' | 'completed'
export interface TaskSummary {
  id: string
  request: string
  status: string
  revision: number
  core_slice?: string
  updated_at?: string
}
export interface TaskRecord extends TaskSummary {
  message?: string | null
  question?: string | null
  phase?: string
  magi_session?: { status?: string; next_step?: string } | null
}
export interface TaskAction {
  action_id?: string
  tool?: string
  operation?: string
  action_status?: string
  result_id?: string | null
  outcome?: string | null
  summary?: string | null
}
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
export function taskStatusLabel(status: string): string {
  return labels[status] ?? status
}
export function isTaskClosed(status: string): boolean {
  return ['completed', 'failed', 'cancelled'].includes(status)
}
