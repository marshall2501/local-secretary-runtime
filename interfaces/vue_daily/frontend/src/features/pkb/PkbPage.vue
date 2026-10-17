<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** 既存PKBのSQL-first検索をVue日常UIから利用する読取専用画面。 */
import { onMounted, ref } from 'vue'
import { legacyPageUrl } from '../../shared/navigation'
import { searchMemory } from './api'
import type { MemoryKind, MemoryResult } from './api'

const text = ref('')
const domain = ref('')
const kind = ref<MemoryKind | ''>('')
const includeHistory = ref(false)
const pageSize = 20
const loading = ref(false)
const problem = ref('')
const result = ref<MemoryResult | null>(null)

/** フィルタ変更時は最初のページから取り直す。失敗時は古い結果を現在値にしない。 */
async function load(offset = 0): Promise<void> {
  if (loading.value) return
  loading.value = true
  problem.value = ''
  result.value = null
  try {
    result.value = await searchMemory({
      q: text.value, domain: domain.value,
      kind: kind.value || undefined,
      include_history: includeHistory.value,
      limit: pageSize, offset,
    })
  } catch (error) {
    problem.value = 'PKB検索に失敗しました（' + String(error) + '）。'
  } finally {
    loading.value = false
  }
}

onMounted(() => { void load() })
</script>

<template>
  <div class="page-title">
    <h1>PKB — 個人ナレッジベース</h1>
    <span class="muted small">SQL-first・読取専用</span>
  </div>
  <p class="read-only-notice">
    検索・現在値・履歴を表示します。記憶の登録、訂正、確認待ちの処理は
    <a :href="legacyPageUrl('/pkb')">既存PKB画面</a>で行えます。
  </p>
  <section class="card" aria-label="PKB検索">
    <h2>記憶・Source検索</h2>
    <form class="pkb-search" @submit.prevent="load(0)">
      <div class="pkb-search-fields">
        <label>キーワード
          <input v-model="text" aria-label="PKBキーワード" maxlength="200" placeholder="対象・値・出典を検索" />
        </label>
        <label>ドメイン（完全一致）
          <input v-model="domain" aria-label="PKBドメイン" maxlength="200" placeholder="例: pc" />
        </label>
        <label>種類
          <select v-model="kind" aria-label="PKB種類">
            <option value="">すべて</option>
            <option value="claim">Claim</option>
            <option value="issue">Issue</option>
            <option value="hypothesis">Hypothesis</option>
            <option value="source">Source</option>
          </select>
        </label>
      </div>
      <div class="command-row">
        <label><input v-model="includeHistory" type="checkbox" /> 過去のClaimも表示</label>
        <button type="submit" class="primary-command" :disabled="loading">
          {{ loading ? '検索中…' : '検索' }}
        </button>
      </div>
    </form>
    <p v-if="problem" role="alert" class="error">{{ problem }}</p>
    <template v-if="result">
      <div class="section-head">
        <h3>検索結果：{{ result.total }}件</h3>
        <span class="muted small">{{ result.offset + (result.items.length ? 1 : 0) }}〜{{ result.offset + result.items.length }}件</span>
      </div>
      <p v-if="result.items.length === 0" class="muted">該当する記録はありません。</p>
      <div v-for="item in result.items" :key="item.id + item.kind" class="pkb-record">
        <div class="pkb-record-heading">
          <strong>{{ item.entity_name || item.title }}</strong>
          <span class="status">{{ item.kind }}</span>
          <span v-if="item.state" class="muted small">{{ item.state }}</span>
        </div>
        <p v-if="item.entity_name">{{ item.title }}</p>
        <p v-if="item.value_text">{{ item.value_text }}</p>
        <p v-if="item.evidence" class="muted small">根拠：{{ item.evidence }}</p>
        <p v-if="item.source_citation" class="muted small">出典：{{ item.source_citation }}</p>
        <p v-if="item.source_uri" class="muted small source-uri">{{ item.source_uri }}</p>
        <div class="task-meta"><span>ドメイン：{{ item.domain || '未分類' }}</span><span>記録：{{ item.recorded_at || '不明' }}</span></div>
      </div>
      <div class="command-row">
        <button type="button" class="secondary" :disabled="loading || result.offset === 0"
          @click="load(Math.max(0, (result?.offset || 0) - pageSize))">前へ</button>
        <button type="button" class="secondary"
          :disabled="loading || result.offset + result.items.length >= result.total"
          @click="load((result?.offset || 0) + pageSize)">次へ</button>
      </div>
    </template>
  </section>
</template>
