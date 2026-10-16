// Provenance-Origin: original
// Provenance-AI: AI-generated
/**
 * Vue側で共通利用するJSON取得関数。HTTP失敗は画面で説明できる例外へ変える。
 * この関数はTaskの実行・保存状態を判断しない。
 */
export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    method: 'GET',
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    let detail = 'HTTP ' + response.status
    try {
      const payload = await response.json() as { detail?: unknown }
      if (typeof payload.detail === 'string') detail += ' / ' + payload.detail
    } catch {
      // 503等がHTMLを返しても本来のHTTP状態コードを失わない。
    }
    throw new Error(detail)
  }
  return await response.json() as T
}

/**
 * ローカルRITSUKO操作APIの共通JSON送信。
 * 202は受付だけを意味し、Taskの保存済み完了は後続GETで確かめる。
 * ネットワーク失敗時は呼出し元が同じTask UUIDで再送できる。
 */
export async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  if (!response.ok) {
    let detail = 'HTTP ' + response.status
    try {
      const payload = await response.json() as { detail?: unknown }
      if (typeof payload.detail === 'string') detail += ' / ' + payload.detail
    } catch {
      // Backendが非JSONの場合もHTTP codeを残す。
    }
    throw new Error(detail)
  }
  return await response.json() as T
}
