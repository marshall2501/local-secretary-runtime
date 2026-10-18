// Provenance-Origin: original
// Provenance-AI: AI-generated
/** Shared PKB query and user-directed memory command API adapter. */
import { getJson, postJson } from '../../shared/api/http'

export type MemoryKind = 'claim' | 'issue' | 'hypothesis' | 'source'
export interface MemoryRecord {
  id: string
  kind: MemoryKind
  domain?: string | null
  entity_name?: string | null
  title: string
  value_text?: string | null
  evidence?: string | null
  state?: string | null
  source_citation?: string | null
  source_uri?: string | null
  recorded_at?: string | null
}
export interface MemoryResult {
  total: number
  limit: number
  offset: number
  include_history: boolean
  items: MemoryRecord[]
}
export interface MemoryFilters {
  q?: string
  domain?: string
  kind?: MemoryKind
  include_history: boolean
  limit: number
  offset: number
}

/** 既存ReadServiceと同じ検索条件をGETで渡し、変更操作を行わない。 */
export async function searchMemory(filters: MemoryFilters): Promise<MemoryResult> {
  const params = new URLSearchParams({
    include_history: String(filters.include_history),
    limit: String(filters.limit),
    offset: String(filters.offset),
  })
  if (filters.q?.trim()) params.set('q', filters.q.trim())
  if (filters.domain?.trim()) params.set('domain', filters.domain.trim())
  if (filters.kind) params.set('kind', filters.kind)
  return await getJson<MemoryResult>('/api/v1/pkb/memory/search?' + params.toString())
}

/** Pending claims remain separate from verified memories until a user action. */
export interface PendingClaim {
  id: string
  raw_text?: string | null
  reason?: string | null
  entity_name?: string | null
  predicate?: string | null
  proposed_value?: unknown
  recorded_at?: string | null
  review_status?: string | null
}
export interface PkbCommandResult {
  status: string
  reason?: string | null
  pending_id?: string | null
  claim_id?: string | null
  message?: string | null
}
export async function registerMemory(text: string): Promise<PkbCommandResult> {
  return await postJson<PkbCommandResult>('/api/v1/pkb/register', { text })
}
export async function correctMemory(text: string): Promise<PkbCommandResult> {
  return await postJson<PkbCommandResult>('/api/v1/pkb/correct', { text })
}
export async function listPending(limit = 50): Promise<PendingClaim[]> {
  return await getJson<PendingClaim[]>('/api/v1/pkb/pending?limit=' + limit)
}
export async function listReviewed(limit = 20): Promise<PendingClaim[]> {
  return await getJson<PendingClaim[]>('/api/v1/pkb/reviewed?limit=' + limit)
}
export async function acceptPending(id: string): Promise<PkbCommandResult> {
  return await postJson<PkbCommandResult>(
    '/api/v1/pkb/pending/' + encodeURIComponent(id) + '/accept',
  )
}
export async function reviewPending(
  id: string, decision: 'rejected' | 'needs_edit',
): Promise<PkbCommandResult> {
  return await postJson<PkbCommandResult>(
    '/api/v1/pkb/pending/' + encodeURIComponent(id) + '/review', { decision },
  )
}
