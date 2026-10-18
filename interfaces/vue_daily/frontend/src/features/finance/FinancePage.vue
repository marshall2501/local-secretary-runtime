<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** Saved Finance dashboard; shared Application remains the calculation source. */
import { onMounted, ref } from 'vue'
import { readLayoutPreferences } from '../../shared/layoutPreferences'
import type { LayoutPreferences } from '../../shared/layoutPreferences'
import { commitFinanceImport, loadFinanceDashboard, loadFinanceOptions, prepareCsv, previewFinanceImport } from './api'
import type { FinanceDashboard, FinanceOptions, FinanceFilters, CsvPayload, FinanceImportPlan, FinanceImportPreview } from './api'

const layout = ref<LayoutPreferences | null>(null)
const data = ref<FinanceDashboard | null>(null)
const options = ref<FinanceOptions>({ accounts: [], major_categories: [] })
const loading = ref(false)
const problem = ref('')
const csv = ref<CsvPayload | null>(null)
const importPlan = ref<FinanceImportPlan | null>(null)
const importPreview = ref<FinanceImportPreview | null>(null)
const importBusy = ref(false)
const importError = ref('')
const importMessage = ref('')
const filters = ref<FinanceFilters>({
  account: '', major_category: '', start_date: '', end_date: '',
  search_text: '', row_mode: 'calculation_target',
  page: 1, recent_limit: 25, sort_by: 'date', sort_dir: 'desc',
})
const yen = (amount: number) => new Intl.NumberFormat('ja-JP', {
  style: 'currency', currency: 'JPY', maximumFractionDigits: 0,
}).format(amount)

/** Always read fresh persisted rows; the UI never calculates category totals. */
async function refresh(page = 1): Promise<void> {
  if (loading.value) return
  filters.value.page = page
  loading.value = true
  problem.value = ''
  data.value = null
  try {
    data.value = await loadFinanceDashboard(filters.value)
  } catch (error) {
    problem.value = '保存済み家計を取得できません（' + String(error) + '）。'
  } finally {
    loading.value = false
  }
}

/** Changing the source invalidates any previously confirmed import plan. */
async function chooseCsv(event: Event): Promise<void> {
  csv.value = null
  importPlan.value = null
  importPreview.value = null
  importMessage.value = ''
  importError.value = ''
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    csv.value = await prepareCsv(file)
  } catch (error) {
    importError.value = String(error)
  }
}

/** Preview never writes rows; show actual database delta before confirmation. */
async function previewCsv(): Promise<void> {
  if (!csv.value || importBusy.value) return
  importBusy.value = true
  importError.value = ''
  importMessage.value = ''
  try {
    const result = await previewFinanceImport(csv.value)
    importPlan.value = result.plan
    importPreview.value = result.preview
  } catch (error) {
    importError.value = '取込プレビューに失敗しました（' + String(error) + '）。'
  } finally {
    importBusy.value = false
  }
}

/** Commit the exact bytes previewed, using the existing idempotent Finance repository. */
async function commitCsv(): Promise<void> {
  if (!csv.value || !importPlan.value || importBusy.value) return
  importBusy.value = true
  importError.value = ''
  try {
    const result = await commitFinanceImport({
      ...csv.value, expected_sha256: importPlan.value.source_sha256,
    })
    importMessage.value = '取込結果：' + result.status
      + ' / 追加 ' + result.inserted + '件、更新 ' + result.updated + '件'
      + (result.reason ? ' / ' + result.reason : '')
    importPlan.value = null
    importPreview.value = null
    await refresh()
  } catch (error) {
    importError.value = '取込確定に失敗しました（' + String(error) + '）。'
  } finally {
    importBusy.value = false
  }
}

onMounted(() => {
  void loadFinanceOptions().then(v => { options.value = v })
    .catch(() => { /* The dashboard is still usable without suggestions. */ })
  void readLayoutPreferences().then(value => {
    layout.value = value
    const size = value.finance.recent_limit
    if (typeof size === 'number') filters.value.recent_limit = size
  }).catch(() => { /* Keep usable defaults if only layout read fails. */ })
    .finally(() => { void refresh() })
})
</script>

<template>
  <div class="page-title">
    <h1>家計・資産</h1>
    <span class="muted small">保存済みデータ・Finance Application</span>
  </div>
  <section v-show="layout?.visibility.finance.filter !== false" class="card" aria-label="家計フィルタ">
      <details :open="layout?.finance.filter !== false" class="finance-accordion">
        <summary>家計フィルタ・検索</summary>

<form @submit.prevent="refresh(1)">
      <div class="pkb-search-fields">
        <label>開始日 <input v-model="filters.start_date" aria-label="家計開始日" type="date" /></label>
        <label>終了日 <input v-model="filters.end_date" aria-label="家計終了日" type="date" /></label>
        <label>金融機関
          <select v-model="filters.account" aria-label="家計金融機関">
            <option value="">すべて</option>
            <option v-for="item in options.accounts" :key="item" :value="item">{{ item }}</option>
          </select>
        </label>
        <label>大分類
          <select v-model="filters.major_category" aria-label="家計大分類">
            <option value="">すべて</option>
            <option v-for="item in options.major_categories" :key="item" :value="item">{{ item }}</option>
          </select>
        </label>
        <label>明細検索 <input v-model="filters.search_text" aria-label="家計明細検索" maxlength="200" /></label>
        <label>明細種別
          <select v-model="filters.row_mode" aria-label="家計明細種別">
            <option value="calculation_target">計算対象</option>
            <option value="transfer">振替</option>
            <option value="all">すべて</option>
          </select>
        </label>
      </div>
      <div class="command-row">
        <button type="submit" class="primary-command" :disabled="loading">
          {{ loading ? '読込中…' : '表示を更新' }}
        </button>
      </div>
    </form>
        </details>
    </section>
  <section v-show="layout?.visibility.finance.csv !== false" class="card" aria-label="MoneyForward取込">
      <details :open="layout?.finance.csv !== false" class="finance-accordion">
        <summary>MoneyForward CSV取込</summary>

<p class="muted small">まず内容と既存データとの差分をプレビューし、本人が確定した場合だけ取込します。</p>
    <input type="file" accept=".csv,text/csv" aria-label="MoneyForward CSV" @change="chooseCsv" />
    <div class="command-row">
      <button type="button" class="secondary" :disabled="importBusy || !csv" @click="previewCsv">
        {{ importBusy ? '処理中…' : '差分を確認' }}
      </button>
    </div>
    <div v-if="importPlan && importPreview" class="pkb-record">
      <h3>取込プレビュー（{{ csv?.filename }}）</h3>
      <p>対象：{{ importPreview.start_date }} 〜 {{ importPreview.end_date }}
        ／ CSV {{ importPlan.total }}件</p>
      <p>追加：{{ importPlan.inserted }}件 ／ 更新：{{ importPlan.updated }}件
        ／ 変更なし：{{ importPlan.unchanged }}件 ／ 重複ID：{{ importPlan.duplicate_external_ids }}件</p>
      <p class="muted small">確定時はプレビューしたファイルと同じ内容のみ受け付けます。</p>
      <button type="button" class="primary-command"
        :disabled="importBusy || importPlan.duplicate_external_ids > 0"
        @click="commitCsv">この差分を取り込む</button>
    </div>
    <p v-if="importError" role="alert" class="error">{{ importError }}</p>
    <p v-if="importMessage" role="status">{{ importMessage }}</p>
        </details>
    </section>
  <p v-if="problem" role="alert" class="error">{{ problem }}</p>
  <template v-if="data">
    <section v-show="layout?.visibility.finance.stored !== false" class="card" aria-label="家計集計">
      <details :open="layout?.finance.stored !== false" class="finance-accordion">
        <summary>集計</summary>

<div class="pkb-search-fields">
        <div><strong>収入</strong><p>{{ yen(data.income_total) }}</p></div>
        <div><strong>支出</strong><p>{{ yen(data.expense_total) }}</p></div>
        <div><strong>差引</strong><p>{{ yen(data.net_total) }}</p></div>
      </div>
      <p class="muted small">対象期間：{{ data.start_date || '—' }} 〜 {{ data.end_date || '—' }}</p>
          </details>
    </section>
    <section v-show="layout?.visibility.finance.monthly !== false" class="card" aria-label="家計月別集計">
      <details :open="layout?.finance.monthly !== false" class="finance-accordion">
        <summary>月別集計</summary>
      <p class="muted small">{{ data.monthly.length }}か月</p>
      <p v-if="!data.monthly.length" class="muted">月別データはありません。</p>
      <div v-else class="finance-table-scroll">
        <table class="finance-table">
          <thead><tr><th scope="col">月</th><th scope="col">収入</th><th scope="col">支出</th>
            <th scope="col">差引</th><th scope="col">取引件数</th></tr></thead>
          <tbody><tr v-for="row in data.monthly" :key="row.month">
            <th scope="row">{{ row.month }}</th>
            <td>{{ yen(row.income) }}</td><td>{{ yen(row.expense) }}</td>
            <td>{{ yen(row.net) }}</td><td>{{ row.count }}</td>
          </tr></tbody>
        </table>
      </div>
          </details>
    </section>
    <section v-show="layout?.visibility.finance.categories !== false" class="card" aria-label="家計カテゴリ">
      <details :open="layout?.finance.categories !== false" class="finance-accordion">
        <summary>支出カテゴリ</summary>

<p v-if="!data.categories.length" class="muted">カテゴリ集計はありません。</p>
      <div v-if="data.categories.length" class="finance-table-scroll">
        <table class="finance-table">
          <thead><tr><th scope="col">大分類</th><th scope="col">小分類</th><th scope="col">支出</th></tr></thead>
          <tbody><tr v-for="(item, index) in data.categories" :key="index">
            <th scope="row">{{ item.major || '未分類' }}</th><td>{{ item.minor || '—' }}</td>
            <td>{{ yen(item.expense) }}</td>
          </tr></tbody>
        </table>
      </div>
          </details>
    </section>
    <section v-show="layout?.visibility.finance.details !== false" class="card" aria-label="家計明細">
      <details :open="layout?.finance.details !== false" class="finance-accordion">
        <summary>取引明細</summary>

<p v-if="!data.recent_rows.length" class="muted">該当する明細はありません。</p>
      <div v-for="item in data.recent_rows" :key="item.external_id" class="pkb-record">
        <div class="pkb-record-heading">
          <strong>{{ item.date }}　{{ item.content }}</strong>
          <span>{{ yen(item.amount) }}</span>
        </div>
        <p class="muted small">{{ item.account }} / {{ item.major_category }} / {{ item.minor_category }}</p>
        <p v-if="item.memo">{{ item.memo }}</p>
      </div>
      <div class="command-row">
        <button type="button" class="secondary" :disabled="loading || data.page <= 1"
          @click="refresh(data.page - 1)">前へ</button>
        <span>ページ {{ data.page }} / {{ data.total_pages }}</span>
        <button type="button" class="secondary" :disabled="loading || data.page >= data.total_pages"
          @click="refresh(data.page + 1)">次へ</button>
      </div>
          </details>
    </section>
    <section v-show="layout?.visibility.finance.imports !== false" class="card" aria-label="家計取込履歴">
      <details :open="layout?.finance.imports !== false" class="finance-accordion">
        <summary>取込履歴</summary>

<p v-if="!data.import_batches.length" class="muted">取込履歴はありません。</p>
      <div v-for="batch in data.import_batches" :key="batch.id" class="pkb-record">
        <strong>{{ batch.source_filename }}</strong>
        <p class="muted small">{{ batch.imported_at }} / {{ batch.row_count }}件 / {{ batch.status }}</p>
      </div>
          </details>
    </section>
  </template>
</template>

<style scoped>
.finance-accordion > summary { cursor:pointer; font-weight:700; font-size:16px; margin-bottom:8px; }
.finance-accordion[open] > summary { padding-bottom:10px; border-bottom:1px solid #dce7ed; }
.finance-table-scroll { overflow-x:auto; max-width:100%; margin-top:11px; }
.finance-table { border-collapse:collapse; width:100%; min-width:520px; font-size:13px; }
.finance-table th,.finance-table td { border-bottom:1px solid #dfe7ed; padding:9px 12px; }
.finance-table thead { background:#edf5fa; }
.finance-table th { text-align:left; }
.finance-table td { text-align:right; font-variant-numeric:tabular-nums; }
.finance-table th:last-child { text-align:right; }
</style>
