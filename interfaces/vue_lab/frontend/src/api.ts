// Provenance-Origin: original
// Provenance-AI: AI-generated
/** 架空TaskのHTTP応答を画面表示へ渡す契約。本番RITSUKOの確定情報ではない。 */
export type TaskStatus =
  | 'queued' | 'running' | 'waiting_user' | 'completed' | 'paused' | 'failed'

export interface TaskView {
  task_id: string
  request: string
  status: TaskStatus
  message: string
  question: string | null
  error: string | null
  synthetic: true
}

/** ログイン不要の同一オリジンAPIへ要求し、HTTP失敗理由を画面へ返す。 */
export async function callApi<T>(
  path: string,
  method: 'GET' | 'POST' = 'GET',
  data?: unknown,
): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      ...(method === 'POST' ? {
        'Content-Type': 'application/json',
      } : {}),
    },
    ...(method === 'POST' ? { body: JSON.stringify(data ?? {}) } : {}),
  })
  if (!response.ok) {
    let detail = String(response.status)
    try {
      const payload = await response.json() as { detail?: string }
      detail = payload.detail ?? detail
    } catch {
      // エラー本文がJSONでなくても、HTTP失敗をTask成功として扱わない。
    }
    throw new Error(detail)
  }
  return await response.json() as T
}
