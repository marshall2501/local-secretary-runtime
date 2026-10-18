<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** Useful single-origin entry: capability cards and live, read-only system summary. */
import { onMounted, ref } from 'vue'
import { readDatabase, readRuntime } from '../system/api'
import { listPending } from '../pkb/api'

const props = defineProps<{ features?: boolean }>()
const loading = ref(false)
const databaseStatus = ref('未取得')
const migration = ref('—')
const runtimeVersion = ref('—')
const pendingCount = ref<number | null>(null)
const capabilities = [
  { key: 'core', title: 'RITSUKO', hint: '自然言語依頼・Task・MAGI・結果の確認', state: '実機検証中', tone: 'teal' },
  { key: 'pkb', title: 'Personal Knowledge Base', hint: '記憶の検索・登録・訂正・確認待ち', state: '実機検証中', tone: 'green' },
  { key: 'finance', title: '家計・資産', hint: '保存済み家計・CSV取込プレビュー', state: '実機検証中', tone: 'blue' },
  { key: 'service-billing', title: '利用料金・契約', hint: '外部API使用量・費用・接続別内訳', state: '実機検証中', tone: 'blue' },
  { key: 'debug', title: 'システム状態 / デバッグ', hint: '稼働情報・DB・ER図・API一覧', state: '参照可能', tone: 'slate' },
  { key: 'settings', title: '設定', hint: 'Service Connection・MAGI・LLM Profile', state: '実機検証中', tone: 'slate' },
  { key: '', title: '予定', hint: 'Google Calendarの独立した日常画面は後続対象', state: '未実装', tone: 'neutral' },
] as const

/** A failed diagnostic cannot hide the other cards or pretend the DB is healthy. */
async function refreshSummary(): Promise<void> {
  if (loading.value) return
  loading.value = true
  const [db, runtime, pending] = await Promise.allSettled([
    readDatabase(), readRuntime(), listPending(50),
  ])
  if (db.status === 'fulfilled') {
    const info = db.value
    databaseStatus.value = info.status === 'ok' && info.boundary_ok === true
      ? '接続OK' : '要確認'
    const migrationInfo = info.migration as { latest?: string } | undefined
    migration.value = migrationInfo?.latest || '—'
  } else {
    databaseStatus.value = '取得不可'
  }
  if (runtime.status === 'fulfilled') {
    runtimeVersion.value = runtime.value.branch + ' / ' + runtime.value.commit.slice(0, 12)
  } else {
    runtimeVersion.value = '取得不可'
  }
  pendingCount.value = pending.status === 'fulfilled' ? pending.value.length : null
  loading.value = false
}
onMounted(() => {
  if (!props.features) void refreshSummary()
})
</script>

<template>
  <div class="page-title">
    <div>
      <h1>{{ features ? '機能一覧' : 'Personal Local Secretary AI' }}</h1>
      <p class="muted small">一つのSecretary Serverから、日常のTask・PKB・家計・サービス管理へ。</p>
    </div>
    <span class="muted small">Vue 3 / 共通Web API</span>
  </div>

  <section v-if="!features" class="card home-summary" aria-label="秘書AIの現在の状態">
    <div class="section-head">
      <h2>現在の状態</h2>
      <button type="button" class="secondary" :disabled="loading" @click="refreshSummary">
        {{ loading ? '取得中…' : '更新' }}
      </button>
    </div>
    <div class="home-summary-grid">
      <div><span>PostgreSQL</span><strong>{{ databaseStatus }}</strong></div>
      <div><span>Runtime</span><strong>{{ runtimeVersion }}</strong></div>
      <div><span>Migration</span><strong>{{ migration }}</strong></div>
      <div><span>PKB確認待ち</span><strong>{{ pendingCount === null ? '取得不可' : pendingCount + '件' }}</strong></div>
    </div>
    <p class="muted small">この概要は参照専用です。Taskの完了や各機能の受入状況を示すものではありません。</p>
  </section>

  <section class="card" aria-label="秘書AIの機能一覧">
    <div class="section-head">
      <h2>{{ features ? 'すべての機能' : '使いたい機能を選択' }}</h2>
      <span class="muted small">同じURL・同じ業務API</span>
    </div>
    <div class="feature-grid">
      <article v-for="item in capabilities" :key="item.title" class="feature-card" :class="'feature-' + item.tone">
        <div class="feature-head">
          <h3>{{ item.title }}</h3>
          <span class="feature-state">{{ item.state }}</span>
        </div>
        <p>{{ item.hint }}</p>
        <a v-if="item.key" class="feature-link" :href="'/ui/?page=' + item.key">開く →</a>
        <span v-else class="muted small">利用可能になるまでお待ちください</span>
      </article>
    </div>
  </section>
</template>

<style scoped>
.page-title p { margin:3px 0 0; }
.home-summary { margin-bottom:18px; }
.home-summary-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:12px; }
.home-summary-grid div { border:1px solid #dce7e5; border-radius:8px; background:#f3faf8; padding:13px; min-width:0; }
.home-summary-grid span { display:block; font-size:12px; color:#5c7280; }
.home-summary-grid strong { display:block; font-size:17px; margin-top:5px; overflow-wrap:anywhere; }
.home-summary > p { margin-bottom:0; }
.feature-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(235px,1fr)); gap:14px; margin-top:15px; }
.feature-card { border:1px solid #d5e2ea; border-top:3px solid #8eaabf; border-radius:8px; padding:15px; background:#fbfcfe; display:flex; flex-direction:column; gap:12px; min-height:162px; }
.feature-teal { background:#f2faf8; border-top-color:#0d8677; }
.feature-green { background:#f2fbf2; border-top-color:#4aa46a; }
.feature-blue { background:#f3f8fe; border-top-color:#4e8cba; }
.feature-head { display:flex; gap:10px; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; }
.feature-head h3 { font-size:16px; margin:0; }
.feature-state { font-size:11px; font-weight:650; color:#526879; background:#edf1f4; padding:3px 7px; border-radius:5px; }
.feature-card p { margin:0; color:#455970; font-size:13px; }
.feature-link { margin-top:auto; align-self:flex-start; font-size:13px; font-weight:700; text-decoration:none; }
.feature-link:hover { text-decoration:underline; }
</style>
