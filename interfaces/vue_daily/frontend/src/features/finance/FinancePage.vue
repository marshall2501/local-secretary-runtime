<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** Saved Finance dashboard; shared Application remains the calculation source. */
import { onMounted, ref } from 'vue'
import { loadFinanceDashboard, loadFinanceOptions } from './api'
import type { FinanceDashboard, FinanceOptions, FinanceFilters } from './api'

const data = ref<FinanceDashboard | null>(null)
const options = ref<FinanceOptions>({ accounts: [], major_categories: [] })
const loading = ref(false)
const problem = ref('')
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

onMounted(() => {
  void loadFinanceOptions().then(v => { options.value = v })
    .catch(() => { /* The dashboard is still usable without suggestions. */ })
  void refresh()
})
</script>

<template>
  <div class="page-title">
    <h1>家計・資産</h1>
    <span class="muted small">保存済みデータ・Finance Application</span>
  </div>
  <section class="card" aria-label="家計フィルタ">
    <h2>家計フィルタ・検索</h2>
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
  </section>
  <p v-if="problem" role="alert" class="error">{{ problem }}</p>
  <template v-if="data">
    <section class="card" aria-label="家計集計">
      <h2>集計（{{ data.transaction_count }}件）</h2>
      <div class="pkb-search-fields">
        <div><strong>収入</strong><p>{{ yen(data.income_total) }}</p></div>
        <div><strong>支出</strong><p>{{ yen(data.expense_total) }}</p></div>
        <div><strong>差引</strong><p>{{ yen(data.net_total) }}</p></div>
      </div>
      <p class="muted small">対象期間：{{ data.start_date || '—' }} 〜 {{ data.end_date || '—' }}</p>
    </section>
    <section class="card" aria-label="家計カテゴリ">
      <h2>支出カテゴリ</h2>
      <p v-if="!data.categories.length" class="muted">カテゴリ集計はありません。</p>
      <div v-for="(item, index) in data.categories" :key="index" class="pkb-record">
        <strong>{{ item.major || '未分類' }} / {{ item.minor || '—' }}</strong>
        <span>{{ yen(item.expense) }}</span>
      </div>
    </section>
    <section class="card" aria-label="家計明細">
      <h2>取引明細</h2>
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
    </section>
    <section class="card" aria-label="家計取込履歴">
      <h2>取込履歴</h2>
      <p v-if="!data.import_batches.length" class="muted">取込履歴はありません。</p>
      <div v-for="batch in data.import_batches" :key="batch.id" class="pkb-record">
        <strong>{{ batch.source_filename }}</strong>
        <p class="muted small">{{ batch.imported_at }} / {{ batch.row_count }}件 / {{ batch.status }}</p>
      </div>
    </section>
  </template>
</template>
