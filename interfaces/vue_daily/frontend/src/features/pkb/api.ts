// Provenance-Origin: original
// Provenance-AI: AI-generated
/** SQL-first PKB read adapter. Task execution and memory writes are not exposed. */
import { getJson } from '../../shared/api/http'

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
