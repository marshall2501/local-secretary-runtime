// Provenance-Origin: original
// Provenance-AI: AI-generated
/** Stored Finance dashboard API; SQL/reporting remains in the Finance Application. */
import { getJson, postJson } from '../../shared/api/http'

export interface FinanceRow {
  external_id: string; date: string; content: string; amount: number
  account: string; major_category: string; minor_category: string
  memo?: string; calculation_target: boolean; is_transfer: boolean
}
export interface FinanceDashboard {
  transaction_count: number; calculation_target_count: number
  start_date: string | null; end_date: string | null
  income_total: number; expense_total: number; net_total: number
  monthly: Array<{ month: string; income: number; expense: number; net: number; count: number }>
  categories: Array<{ major: string; minor: string; expense: number }>
  recent_rows: FinanceRow[]
  import_batches: Array<{ id: string; source_filename: string; row_count: number; imported_at: string; status: string }>
  page: number; page_size: number; total_pages: number
}
export interface FinanceFilters {
  account: string; major_category: string; start_date: string; end_date: string
  search_text: string; row_mode: 'calculation_target' | 'transfer' | 'all'
  page: number; recent_limit: number; sort_by: 'date' | 'amount'
  sort_dir: 'asc' | 'desc'
}
export interface FinanceOptions { accounts: string[]; major_categories: string[] }

export async function loadFinanceDashboard(filters: FinanceFilters): Promise<FinanceDashboard> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value !== '') query.set(key, String(value))
  }
  return await getJson<FinanceDashboard>('/api/v1/finance/dashboard?' + query.toString())
}
export async function loadFinanceOptions(): Promise<FinanceOptions> {
  return await getJson<FinanceOptions>('/api/v1/finance/options')
}

export interface FinanceImportPlan {
  source_sha256: string; total: number; inserted: number
  updated: number; unchanged: number; duplicate_external_ids: number
}
export interface FinanceImportPreview {
  row_count: number; start_date: string; end_date: string
  income_total: number; expense_total: number; net_total: number
}
export interface FinanceImportResult {
  status: string; batch_id: string | null; reason: string | null
  total: number; inserted: number; updated: number; unchanged: number
}
export interface CsvPayload {
  filename: string; csv_base64: string; expected_sha256?: string
}

/** Encode a user-selected local CSV without sending it until preview is requested. */
export async function prepareCsv(file: File): Promise<CsvPayload> {
  const bytes = new Uint8Array(await file.arrayBuffer())
  if (bytes.length > 20 * 1024 * 1024) throw new Error('CSVが20MiBを超えています')
  let chars = ''
  for (let offset = 0; offset < bytes.length; offset += 16_384) {
    chars += String.fromCharCode(...bytes.subarray(offset, offset + 16_384))
  }
  return { filename: file.name, csv_base64: btoa(chars) }
}
export async function previewFinanceImport(payload: CsvPayload): Promise<{
  plan: FinanceImportPlan; preview: FinanceImportPreview
}> {
  return await postJson('/api/v1/finance/import/plan', payload)
}
export async function commitFinanceImport(payload: CsvPayload): Promise<FinanceImportResult> {
  return await postJson<FinanceImportResult>('/api/v1/finance/import/commit', payload)
}
