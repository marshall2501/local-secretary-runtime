<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** Full saved Task history; the same RITSUKO Query API powers the sidebar. */
import { onMounted, ref } from 'vue'
import { listTasks } from './api'
import { taskStatusLabel } from './types'
import type { TaskSummary } from './types'

const pageSize = 20
const offset = ref(0)
const loading = ref(false)
const problem = ref('')
const items = ref<TaskSummary[]>([])

/** A page contains all persisted statuses, including failures and paused Tasks. */
async function load(nextOffset = 0): Promise<void> {
  if (loading.value) return
  loading.value = true
  problem.value = ''
  try {
    const next = await listTasks('recent', pageSize, nextOffset)
    items.value = next
    offset.value = nextOffset
  } catch (error) {
    problem.value = 'Task履歴を取得できません（' + String(error) + '）。'
  } finally {
    loading.value = false
  }
}
onMounted(() => { void load() })
</script>

<template>
  <div class="page-title">
    <div>
      <h1>Task履歴</h1>
      <p class="muted small">失敗・停止・確認待ちを含む、保存済みTaskの記録です。</p>
    </div>
    <span class="muted small">RITSUKO共通Task Query</span>
  </div>
  <p><a href="/ui/">← RITSUKOへ戻る</a></p>
  <section class="card" aria-label="保存済みTask履歴">
    <div class="section-head">
      <h2>全状態のTask一覧</h2>
      <button type="button" class="secondary" :disabled="loading" @click="load(offset)">
        {{ loading ? '読込中…' : '再読み込み' }}
      </button>
    </div>
    <p v-if="problem" role="alert" class="error">{{ problem }}</p>
    <p v-else-if="!loading && !items.length" class="muted">該当するTaskはありません。</p>
    <div class="history-table-scroll" v-if="items.length">
      <table class="history-table">
        <thead><tr><th scope="col">依頼</th><th scope="col">確定状態</th>
          <th scope="col">更新日時</th><th scope="col">Revision</th></tr></thead>
        <tbody><tr v-for="item in items" :key="item.id">
          <th scope="row"><a :href="'/ui/#task=' + encodeURIComponent(item.id)">{{ item.request }}</a></th>
          <td><span class="status">{{ taskStatusLabel(item.status) }}</span></td>
          <td>{{ item.updated_at || '—' }}</td>
          <td class="numeric">{{ item.revision }}</td>
        </tr></tbody>
      </table>
    </div>
    <div class="command-row">
      <button class="secondary" type="button" :disabled="loading || offset === 0"
        @click="load(Math.max(0, offset - pageSize))">前へ</button>
      <span class="muted small">{{ Math.floor(offset / pageSize) + 1 }}ページ / {{ items.length }}件表示</span>
      <button class="secondary" type="button" :disabled="loading || items.length < pageSize"
        @click="load(offset + pageSize)">次へ</button>
    </div>
  </section>
</template>

<style scoped>
.page-title p { margin:3px 0 0; }
.history-table-scroll { max-width:100%; overflow-x:auto; }
.history-table { width:100%; border-collapse:collapse; font-size:13px; min-width:650px; }
.history-table th,.history-table td { padding:11px 12px; text-align:left; border-bottom:1px solid #e0e9ef; vertical-align:top; }
.history-table thead { background:#eef5fa; }
.history-table tbody th { font-weight:600; max-width:500px; overflow-wrap:anywhere; }
.history-table td.numeric { text-align:right; font-variant-numeric:tabular-nums; }
</style>
