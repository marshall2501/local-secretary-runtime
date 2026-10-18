// Provenance-Origin: original
// Provenance-AI: AI-generated
/** Shared connection, service-billing and debug views on the Secretary API. */
import { getJson, postJson } from '../../shared/api/http'

export interface ConnectionSummary {
  id: string
  display_name: string
  adapter_key: string
  endpoint?: string | null
  account_label?: string | null
  capabilities?: string[]
  enabled: boolean
  connection_type?: string
  connection_role?: string | null
  auth_configured: boolean
  auth_fields?: string[]
  credential_ref?: string | null
  config_data?: Record<string, unknown>
}
export interface BillingProfile {
  id: string
  display_name: string
  connection_id: string
  connection_name: string
  adapter_key: string
  enabled: boolean
  connection_enabled: boolean
}
export interface RuntimeInfo {
  status: string
  pid: number
  branch: string
  commit: string
  python: string
  process_started_at: string
  [key: string]: unknown
}
export interface EnvironmentItem {
  name: string; value: string; secret: boolean
}
export async function readConnections(): Promise<ConnectionSummary[]> {
  return await getJson<ConnectionSummary[]>('/api/v1/connections')
}
export async function readBillingProfiles(): Promise<BillingProfile[]> {
  return await getJson<BillingProfile[]>('/api/v1/billing/profiles')
}
export async function readBillingSnapshot(id: string): Promise<Record<string, unknown>> {
  return await getJson<Record<string, unknown>>(
    '/api/v1/billing/profiles/' + encodeURIComponent(id) + '/snapshot',
  )
}
export async function readRuntime(): Promise<RuntimeInfo> {
  return await getJson<RuntimeInfo>('/api/v1/debug/runtime')
}
export async function readEnvironment(): Promise<EnvironmentItem[]> {
  return await getJson<EnvironmentItem[]>('/api/v1/debug/environment')
}
export async function readDatabase(): Promise<Record<string, unknown>> {
  return await getJson<Record<string, unknown>>('/api/v1/debug/database')
}

export interface ConnectionInput {
  connection_id?: string
  display_name: string
  adapter_key: string
  endpoint?: string
  credential_ref?: string
  connection_type?: string
  connection_role?: string
  capabilities?: string[]
  config_data?: Record<string, unknown>
  auth_data?: Record<string, unknown>
  enabled?: boolean
}
export interface BillingProfileInput {
  profile_id?: string
  display_name: string
  connection_id: string
  enabled: boolean
}
export async function saveConnection(input: ConnectionInput): Promise<ConnectionSummary> {
  return await postJson<ConnectionSummary>('/api/v1/connections', input)
}
export async function saveBillingProfile(input: BillingProfileInput): Promise<BillingProfile> {
  return await postJson<BillingProfile>('/api/v1/billing/profiles', input)
}

export interface SchemaDiagramResult {
  status: string
  provider: string
  database: string
  schema: string
  output_format: 'svg' | 'mermaid'
  content: string
  table_count: number
  relation_count: number
  duration_ms: number
  generated_at: string
  warnings: string[]
}
export async function readSchemaDiagram(
  keysOnly = true, format: 'svg' | 'mermaid' = 'svg',
): Promise<SchemaDiagramResult> {
  return await getJson<SchemaDiagramResult>(
    '/api/v1/debug/er?keys_only=' + String(keysOnly) + '&format=' + format,
  )
}

export interface ApiRouteInfo { path: string; methods: string[]; name: string }
export async function readApiRoutes(): Promise<ApiRouteInfo[]> {
  return await getJson<ApiRouteInfo[]>('/api/v1/debug/routes')
}

export interface LlmProfile {
  id: string; display_name: string; model: string
  provider: string; connection_id: string; connection_display_name: string
  context_window_tokens?: number | null
  ollama_num_predict?: number | null
  enabled: boolean
}
export interface MagiMember {
  name: string; profile_id: string; profile_label?: string
  provider?: string; model?: string; enabled: boolean
  weight: number; timeout_seconds: number; retry_within_turn: boolean
}
export interface LlmProfileInput {
  profile_id?: string; model: string; connection_id: string
  display_name?: string; context_window_tokens?: number
  ollama_num_predict?: number; enabled: boolean
}
export async function readMagiProfiles(): Promise<LlmProfile[]> {
  return await getJson<LlmProfile[]>('/api/v1/magi/profiles')
}
export async function readMagiMembers(): Promise<MagiMember[]> {
  return await getJson<MagiMember[]>('/api/v1/magi/members')
}
export async function saveMagiProfile(profile: LlmProfileInput): Promise<LlmProfile> {
  return await postJson<LlmProfile>('/api/v1/magi/profiles', profile)
}
export async function saveMagiMembers(members: MagiMember[]): Promise<MagiMember[]> {
  return await postJson<MagiMember[]>('/api/v1/magi/members', { assignments: members.map(m => ({
    name: m.name, profile_id: m.profile_id, enabled: m.enabled,
    weight: m.weight, timeout_seconds: m.timeout_seconds,
    retry_within_turn: m.retry_within_turn,
  })) })
}
