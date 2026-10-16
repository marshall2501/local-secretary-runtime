// Provenance-Origin: original
// Provenance-AI: AI-generated
/** 未移行の機能は既存NiceGUIへ案内し、空画面やダミー操作を置かない。 */
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

/** 既存サブPCのNiceGUI（8093）へ明示誘導。 */
export function legacyPageUrl(path: string): string {
  const url = new URL(path, window.location.href)
  url.port = '8093'
  return url.toString()
}
