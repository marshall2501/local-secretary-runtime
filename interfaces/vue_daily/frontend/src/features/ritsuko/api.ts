// Provenance-Origin: original
// Provenance-AI: AI-generated
/** 既存Task Query ApplicationをHTTPで読み取るtyped API adapter。 */
import { getJson } from '../../shared/api/http'
import type { TaskListView, TaskSummary, TaskTrace } from './types'

const base = '/api/v1/ritsuko'

/** 最近・進行中・完了の保存済みTask一覧を取得する。更新操作は行わない。 */
export async function listTasks(view: TaskListView): Promise<TaskSummary[]> {
  const query = new URLSearchParams({ view, limit: '20' })
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
