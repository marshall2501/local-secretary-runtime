// Provenance-Origin: original
// Provenance-AI: AI-generated
/** 全feature共通の読み取り専用HTTP処理。Task確定・業務判断は持たない。 */
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
      // HTMLエラーでもHTTP失敗として扱う。
    }
    throw new Error(detail)
  }
  return await response.json() as T
}
