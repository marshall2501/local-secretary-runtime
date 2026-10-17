// Provenance-Origin: original
// Provenance-AI: AI-generated
/** 既存Task Query ApplicationをHTTPで読み取るtyped API adapter。 */
import { getJson, postJson } from '../../shared/api/http'
import type { TaskListView, TaskSummary, TaskTrace } from './types'

const base = '/api/v1/ritsuko'

/** 最近・進行中・完了の保存済みTask一覧を取得する。更新操作は行わない。 */
export async function listTasks(
  view: TaskListView, limit = 10, offset = 0,
): Promise<TaskSummary[]> {
  const query = new URLSearchParams({
    view, limit: String(limit), offset: String(offset),
  })
  const response = await getJson<{ items: TaskSummary[]; source: string }>(
    base + '/tasks?' + query.toString(),
  )
  return response.items
}
/** F5復元や一覧からの選択時に、同一TaskのAction/Result履歴を取得する。 */
export async function getTaskTrace(id: string): Promise<TaskTrace> {
  const response = await getJson<{ trace: TaskTrace; source: string }>(
    base + '/tasks/' + encodeURIComponent(id),
  )
  return response.trace
}

/** 実DB・Sourceが注入されたAPIかどうか。画面はこの値だけで操作を開閉する。 */
export async function getTaskApiMode(): Promise<'read_only' | 'commands_enabled'> {
  const info = await getJson<{ mode: 'read_only' | 'commands_enabled' }>(base + '/health')
  return info.mode
}

export type DecisionStrategy = 'standard' | 'llm'
export interface JudgmentPreferences {
  default_strategy: DecisionStrategy
  show_strategy_selector: boolean
}

/** 既存NiceGUIと同じ保存済み判断設定を取得する。 */
export async function getJudgmentPreferences(): Promise<JudgmentPreferences> {
  return await getJson<JudgmentPreferences>(base + '/preferences')
}

/** 次回の新規Taskから適用する。保存済みTask・MAGI席割当は変更しない。 */
export async function saveJudgmentPreferences(
  preferences: JudgmentPreferences,
): Promise<JudgmentPreferences> {
  return await postJson<JudgmentPreferences>(base + '/preferences', preferences)
}
/** APIプロセス内の一時的なMAGI席状態。DBの確定結果ではない。 */
export interface MemberOperationProgress {
  member: string
  provider: string
  model: string
  state: string
  turn: number
  elapsed_seconds?: number
}
export interface TaskOperation {
  task_id: string
  member_progress?: Record<string, MemberOperationProgress>
  live_turn?: number
  status: string
  kind?: string | null
  task_status?: string
  source?: string
  stop_requested?: boolean | null
  execution_reconciliation_required?: boolean
}

/** 同じUUID・依頼本文・方式で再送すると重複Taskを生成しない。 */
export async function startTask(
  request: string, strategy: DecisionStrategy, id: string,
): Promise<TaskOperation> {
  return await postJson<TaskOperation>(base + '/tasks', {
    request, decision_strategy: strategy, task_id: id,
  })
}
export async function answerTask(id: string, reply: string): Promise<TaskOperation> {
  return await postJson<TaskOperation>(
    base + '/tasks/' + encodeURIComponent(id) + '/answer', { reply },
  )
}
export type ReviewDecision = 'answer_only' | 'remember'
/** Explicit owner choice. Both decisions use the same RITSUKO review Port. */
export async function reviewTask(
  id: string, decision: ReviewDecision = 'answer_only',
): Promise<TaskOperation> {
  return await postJson<TaskOperation>(
    base + '/tasks/' + encodeURIComponent(id) + '/review',
    { decision },
  )
}
/** DBが再開可能と判定した停止済みTaskだけを、同じIDで実行受付する。 */
export async function resumeTask(id: string): Promise<TaskOperation> {
  return await postJson<TaskOperation>(
    base + '/tasks/' + encodeURIComponent(id) + '/resume',
  )
}
export async function stopTask(id: string): Promise<TaskOperation> {
  return await postJson<TaskOperation>(
    base + '/tasks/' + encodeURIComponent(id) + '/stop',
  )
}
export async function getTaskOperation(id: string): Promise<TaskOperation> {
  return await getJson<TaskOperation>(
    base + '/operations/' + encodeURIComponent(id),
  )
}
