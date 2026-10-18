<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** Display the existing monthly Service Billing contract as a readable report. */
import { computed } from 'vue'
import type { ServiceBillingSnapshot } from './api'

const props = defineProps<{ snapshot: ServiceBillingSnapshot }>()
const activity = computed(() => props.snapshot.activity)
const charges = computed(() => props.snapshot.charges)
const models = computed(() => activity.value?.by_model || [])
const totals = computed(() => activity.value?.totals)
const amounts = computed(() => charges.value?.values || {})
const costEntries = computed(() => Object.entries(amounts.value).filter(([, sum]) => Number.isFinite(sum)))

function number(value: number | undefined): string {
  return value == null || !Number.isFinite(value) ? '—' : new Intl.NumberFormat('ja-JP').format(value)
}
function money(amount: number, currency: string): string {
  const value = amount.toLocaleString('ja-JP', { minimumFractionDigits: 4, maximumFractionDigits: 4 })
  return currency.toLowerCase() === 'usd' ? '$' + value : currency.toUpperCase() + ' ' + value
}
function utcDate(iso: string | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? '—'
    : new Intl.DateTimeFormat('ja-JP', {
      timeZone: 'UTC', year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(date)
}
function stateText(state: string | undefined): string {
  if (state === 'known') return '取得済み'
  if (state === 'unknown' || state === 'unavailable') return '取得不可'
  return '未取得'
}
const hasUsage = computed(() => activity.value?.status === 'known')
const costDisplay = computed(() => {
  if (charges.value?.status !== 'known' || costEntries.value.length === 0) return '—'
  return costEntries.value.map(([code, cost]) => money(cost, code)).join(' / ')
})
const serviceLabel = computed(() => props.snapshot.service === 'openai'
  ? 'OpenAI' : props.snapshot.service === 'google_cloud' ? 'Google Cloud' : props.snapshot.service || 'サービス')
</script>

<template>
  <section class="billing-report" aria-label="取得した利用状況">
    <div class="billing-report-heading">
      <div>
        <h3>API利用状況 — {{ serviceLabel }}</h3>
        <p class="muted small">{{ snapshot.connection_name || '設定済み接続' }}
          ／ 対象：{{ utcDate(snapshot.period_start) }} ～ {{ utcDate(snapshot.fetched_at) }}（UTC・当月）</p>
      </div>
      <span class="status" role="status">利用量：{{ stateText(activity?.status) }}
        ／ 費用：{{ stateText(charges?.status) }}</span>
    </div>

    <div class="billing-metrics">
      <article class="billing-metric billing-cost">
        <p>当月の利用費用</p>
        <strong>{{ costDisplay }}</strong>
        <small>{{ charges?.status === 'known' ? '取得した費用の合計' : '費用を取得できていません（0円ではありません）' }}</small>
      </article>
      <article class="billing-metric">
        <p>リクエスト</p>
        <strong>{{ hasUsage ? number(totals?.requests) : '—' }}</strong>
        <small>件</small>
      </article>
      <article class="billing-metric">
        <p>入力トークン</p>
        <strong>{{ hasUsage ? number(totals?.input_tokens) : '—' }}</strong>
        <small>tokens</small>
      </article>
      <article class="billing-metric">
        <p>出力トークン</p>
        <strong>{{ hasUsage ? number(totals?.output_tokens) : '—' }}</strong>
        <small>tokens</small>
      </article>
      <article class="billing-metric">
        <p>合計トークン</p>
        <strong>{{ hasUsage && totals?.input_tokens != null && totals.output_tokens != null
          ? number(totals.input_tokens + totals.output_tokens) : '—' }}</strong>
        <small>Input + Output</small>
      </article>
    </div>

    <section v-if="hasUsage" class="billing-report-section">
      <h3>モデル別利用内訳</h3>
      <div v-if="models.length" class="billing-table-scroll">
        <table class="billing-table">
          <thead><tr><th scope="col">モデル</th><th scope="col">Requests</th>
            <th scope="col">Input</th><th scope="col">Output</th><th scope="col">合計Tokens</th></tr></thead>
          <tbody>
            <tr v-for="row in models" :key="row.model">
              <th scope="row">{{ row.model }}</th>
              <td>{{ number(row.requests) }}</td>
              <td>{{ number(row.input_tokens) }}</td>
              <td>{{ number(row.output_tokens) }}</td>
              <td>{{ row.input_tokens != null && row.output_tokens != null
                ? number(row.input_tokens + row.output_tokens) : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="muted">モデル別の内訳データはありません。</p>
    </section>

    <section class="billing-report-section">
      <h3>費用・契約・残高の取得状況</h3>
      <dl class="billing-status-grid">
        <div><dt>費用</dt><dd>{{ stateText(charges?.status) }}</dd></div>
        <div><dt>前払いクレジット</dt><dd>{{ stateText(snapshot.credits?.status) }}</dd></div>
        <div><dt>契約</dt><dd>{{ stateText(snapshot.subscription?.status) }}</dd></div>
        <div><dt>利用上限</dt><dd>{{ stateText(snapshot.limits?.status) }}</dd></div>
      </dl>
      <p v-if="snapshot.service === 'google_cloud' && charges?.status !== 'known'" class="muted small">
        Google Cloud Monitoringの利用量と実際の請求金額は別情報です。費用・前払い残高が未取得の場合、金額は推定しません。
      </p>
    </section>

    <div class="billing-report-meta">
      <span>取得：{{ utcDate(snapshot.fetched_at) }} UTC</span>
      <span>費用・利用量は現在のService Billing APIから取得</span>
    </div>
    <details class="billing-diagnostics">
      <summary>取得結果の詳細（JSON・診断用）</summary>
      <pre>{{ JSON.stringify(snapshot, null, 2) }}</pre>
    </details>
  </section>
</template>

<style scoped>
.billing-report { display:flex; flex-direction:column; gap:16px; margin-top:18px; }
.billing-report-heading { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; flex-wrap:wrap; }
.billing-report-heading h3 { font-size:17px; }
.billing-report-heading p { margin:5px 0 0; }
.billing-metrics { display:grid; grid-template-columns:repeat(auto-fit,minmax(165px,1fr)); gap:10px; }
.billing-metric { border:1px solid #d4e0e9; border-radius:9px; background:#f8fbfd; padding:13px 14px; min-width:0; display:flex; flex-direction:column; gap:2px; }
.billing-metric p { margin:0; color:#5b6f81; font-size:12px; font-weight:700; }
.billing-metric strong { color:#203b59; font-size:clamp(19px,2vw,25px); font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
.billing-metric small { color:#64748b; }
.billing-cost { background:#f2faf8; border-color:#a9d9d0; }
.billing-report-section { border-top:1px solid #dbe4eb; padding-top:13px; }
.billing-report-section h3 { margin-bottom:12px; font-size:15px; }
.billing-table-scroll { max-width:100%; overflow-x:auto; }
.billing-table { border-collapse:collapse; width:100%; min-width:625px; font-size:13px; }
.billing-table th,.billing-table td { padding:10px 9px; border-bottom:1px solid #e0e9ef; }
.billing-table thead th { background:#f1f6fa; text-align:right; }
.billing-table thead th:first-child { text-align:left; }
.billing-table tbody th { text-align:left; font-weight:600; overflow-wrap:anywhere; }
.billing-table td { text-align:right; font-variant-numeric:tabular-nums; }
.billing-status-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:10px; margin:0; }
.billing-status-grid div { padding:9px 11px; background:#f7f9fb; border-radius:7px; }
.billing-status-grid dt { color:#64748b; font-size:12px; }
.billing-status-grid dd { margin:3px 0 0; font-weight:700; }
.billing-report-meta { display:flex; flex-wrap:wrap; gap:5px 18px; color:#64748b; font-size:12px; }
.billing-diagnostics { margin-top:5px; }
.billing-diagnostics summary { cursor:pointer; color:#35516c; font-weight:650; }
.billing-diagnostics pre { white-space:pre-wrap; overflow-wrap:anywhere; background:#f1f5f9; border-radius:6px; padding:12px; font-size:12px; }
</style>
