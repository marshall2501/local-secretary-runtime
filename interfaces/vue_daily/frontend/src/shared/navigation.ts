// Provenance-Origin: original
// Provenance-AI: AI-generated
/**
 * 日常画面のナビ項目と移行状態。RITSUKO以外は既存NiceGUIで利用する。
 * 各機能をVueへ移行したら、その項目の遷移先だけを変更する。
 */
export const navigationItems = [
  { label: 'TOP', path: '/', current: false },
  { label: '機能一覧', path: '/features', current: false },
  { label: 'PKB', path: '/pkb', current: false },
  { label: 'RITSUKO', path: '/core', current: true },
  { label: '家計・資産', path: '/finance', current: false },
  { label: '利用料金・契約', path: '/service-billing', current: false },
  { label: 'デバッグ', path: '/debug', current: false },
  { label: '設定', path: '/settings', current: false },
] as const

/**
 * 現在のローカルホスト上で稼働するNiceGUI（8093）の既存画面URLを作る。
 * VueプレビューとNiceGUIが同じホストにある移行期間のための処理。
 */
export function legacyPageUrl(path: string): string {
  const url = new URL(path, window.location.href)
  url.port = '8093'
  return url.toString()
}
