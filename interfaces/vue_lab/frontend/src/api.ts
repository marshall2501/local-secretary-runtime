// Provenance-Origin: original
// Provenance-AI: AI-generated
/** RITSUKO試作APIの表示用契約。Taskの真の実行判断は保持しない。 */
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

export interface Session {
  csrf: string
}

/** Cookieを送信し、HTTP失敗を利用者に通知できるエラーへ変換する。 */
export async function callApi<T>(
  path: string,
  method: 'GET' | 'POST' = 'GET',
  data?: unknown,
  csrf = '',
): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      ...(method === 'POST' ? {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrf,
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
      // HTMLエラーなども認証成功・Task成功と見なさない。
    }
    throw new Error(detail)
  }
  return await response.json() as T
}
