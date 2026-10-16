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
