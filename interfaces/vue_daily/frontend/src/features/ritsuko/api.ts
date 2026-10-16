// Provenance-Origin: original
// Provenance-AI: AI-generated
/** 既存Task Query ApplicationをHTTPで読み取るtyped API adapter。 */
import { getJson } from '../../shared/api/http'
import type { TaskListView, TaskSummary, TaskTrace } from './types'

const base = '/api/v1/ritsuko'

export async function listTasks(view: TaskListView): Promise<TaskSummary[]> {
  const query = new URLSearchParams({ view, limit: '20' })
  const response = await getJson<{ items: TaskSummary[]; source: string }>(
    base + '/tasks?' + query.toString(),
  )
  return response.items
}
export async function getTaskTrace(id: string): Promise<TaskTrace> {
  const response = await getJson<{ trace: TaskTrace; source: string }>(
    base + '/tasks/' + encodeURIComponent(id),
  )
  return response.trace
}
