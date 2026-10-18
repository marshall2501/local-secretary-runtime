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

export interface ApiRouteInfo { path: string; methods: string[]; name: string }
export async function readApiRoutes(): Promise<ApiRouteInfo[]> {
  return await getJson<ApiRouteInfo[]>('/api/v1/debug/routes')
}
