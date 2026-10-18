// Provenance-Origin: original
// Provenance-AI: AI-generated
/** Shared UI visibility/defaults contract; saved in legacy ui_preferences.json. */
import { getJson, postJson } from './api/http'

export interface LayoutPreferences {
  pkb: Record<string, boolean>
  finance: Record<string, boolean | number>
  core: Record<string, boolean | number>
  entity: { default_tab: string }
  debug: { er_diagram_providers: Record<string, boolean> }
  visibility: {
    pkb: Record<string, boolean>
    entity: Record<string, boolean>
    finance: Record<string, boolean>
    core: Record<string, boolean>
  }
}

export async function readLayoutPreferences(): Promise<LayoutPreferences> {
  return getJson<LayoutPreferences>('/api/v1/ui/layout')
}
export async function readLayoutDefaults(): Promise<LayoutPreferences> {
  return getJson<LayoutPreferences>('/api/v1/ui/layout/defaults')
}
export async function saveLayoutPreferences(
  value: LayoutPreferences,
): Promise<LayoutPreferences> {
  return postJson<LayoutPreferences>('/api/v1/ui/layout', value)
}
