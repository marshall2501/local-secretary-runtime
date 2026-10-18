<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** 既存PKB Applicationの検索・登録・訂正・Pending操作を同一Vue日常UIで行う。 */
import { onMounted, ref } from 'vue'
import { acceptPending, correctMemory, listPending, listReviewed, registerMemory, reviewPending, searchMemory } from './api'
import type { MemoryKind, MemoryResult, PendingClaim, PkbCommandResult } from './api'

const text = ref('')
const domain = ref('')
const kind = ref<MemoryKind | ''>('')
const includeHistory = ref(false)
const pageSize = 20
const loading = ref(false)
const problem = ref('')
const result = ref<MemoryResult | null>(null)
const entryText = ref('')
const writing = ref(false)
const writeMessage = ref('')
const writeError = ref('')
const pending = ref<PendingClaim[]>([])
const reviewed = ref<PendingClaim[]>([])
const pendingLoading = ref(false)
const pendingError = ref('')
const reviewBusy = ref('')
const reviewMessage = ref('')

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

/** A write returns PKB's actual outcome; a Pending candidate is not a saved claim. */
async function submitMemory(mode: 'register' | 'correct'): Promise<void> {
  if (writing.value || !entryText.value.trim()) return
  writing.value = true
  writeError.value = ''
  writeMessage.value = ''
  try {
    const answer: PkbCommandResult = mode === 'register'
      ? await registerMemory(entryText.value.trim())
      : await correctMemory(entryText.value.trim())
    writeMessage.value = answer.message || ('結果: ' + answer.status + (answer.reason ? ' / ' + answer.reason : ''))
    if (answer.status === 'registered' || answer.status === 'accepted' || answer.status === 'corrected') {
      entryText.value = ''
    }
    await Promise.all([load(0), reloadPending()])
  } catch (error) {
    writeError.value = 'PKB操作に失敗しました（' + String(error) + '）。'
  } finally {
    writing.value = false
  }
}

/** Load both unreviewed and reviewed records without guessing approval. */
async function reloadPending(): Promise<void> {
  if (pendingLoading.value) return
  pendingLoading.value = true
  pendingError.value = ''
  try {
    const [nextPending, nextReviewed] = await Promise.all([
      listPending(), listReviewed(),
    ])
    pending.value = nextPending
    reviewed.value = nextReviewed
  } catch (error) {
    pending.value = []
    reviewed.value = []
    pendingError.value = '確認待ちを取得できません（' + String(error) + '）。'
  } finally {
    pendingLoading.value = false
  }
}

/** An explicit user decision is sent for exactly one stored candidate. */
async function decide(id: string, decision: 'accept' | 'rejected' | 'needs_edit'): Promise<void> {
  if (reviewBusy.value) return
  reviewBusy.value = id
  reviewMessage.value = ''
  pendingError.value = ''
  try {
    const outcome = decision === 'accept'
      ? await acceptPending(id)
      : await reviewPending(id, decision)
    reviewMessage.value = '候補 ' + id + '：' + outcome.status + (outcome.reason ? ' / ' + outcome.reason : '')
    await Promise.all([reloadPending(), load(0)])
  } catch (error) {
    pendingError.value = '候補の確認操作に失敗しました（' + String(error) + '）。'
  } finally {
    reviewBusy.value = ''
  }
}

onMounted(() => { void load(); void reloadPending() })
</script>

<template>
  <div class="page-title">
    <h1>PKB — 個人ナレッジベース</h1>
    <span class="muted small">SQL-first・登録・訂正・確認待ち</span>
  </div>
  <section class="card" aria-label="PKB登録訂正">
    <h2>記憶の登録・訂正</h2>
    <p class="muted small">本人の文章をPKBへ登録します。不確かな内容は確認待ちとなることがあります。</p>
    <textarea v-model="entryText" aria-label="登録・訂正する文章" maxlength="10000"
      rows="3" placeholder="例: サブPCのOSを更新した。" class="pkb-input" />
    <div class="command-row">
      <button type="button" class="primary-command" :disabled="writing || !entryText.trim()"
        @click="submitMemory('register')">{{ writing ? '処理中…' : '記憶を登録' }}</button>
      <button type="button" class="secondary" :disabled="writing || !entryText.trim()"
        @click="submitMemory('correct')">訂正として登録</button>
    </div>
    <p v-if="writeMessage" role="status">{{ writeMessage }}</p>
    <p v-if="writeError" role="alert" class="error">{{ writeError }}</p>
  </section>
  <section class="card" aria-label="PKB確認待ち">
    <div class="section-head">
      <h2>確認待ち・判断履歴</h2>
      <button type="button" class="secondary" :disabled="pendingLoading" @click="reloadPending">
        {{ pendingLoading ? '取得中…' : '更新' }}
      </button>
    </div>
    <p v-if="pendingError" role="alert" class="error">{{ pendingError }}</p>
    <p v-if="reviewMessage" role="status">{{ reviewMessage }}</p>
    <p v-if="!pendingLoading && pending.length === 0" class="muted">確認待ちの候補はありません。</p>
    <div v-for="item in pending" :key="item.id" class="pkb-record">
      <div class="pkb-record-heading"><strong>{{ item.entity_name || '候補' }}</strong>
        <span class="status">{{ item.reason || 'pending' }}</span></div>
      <p>{{ item.raw_text || item.predicate || item.id }}</p>
      <p v-if="item.proposed_value != null" class="muted small">候補値：{{ item.proposed_value }}</p>
      <div class="command-row">
        <button type="button" class="primary-command" :disabled="!!reviewBusy"
          @click="decide(item.id, 'accept')">承認して登録</button>
        <button type="button" class="secondary" :disabled="!!reviewBusy"
          @click="decide(item.id, 'needs_edit')">要修正</button>
        <button type="button" class="secondary" :disabled="!!reviewBusy"
          @click="decide(item.id, 'rejected')">却下</button>
      </div>
    </div>
    <h3>判断済み：{{ reviewed.length }}件（最新20件）</h3>
    <p v-if="reviewed.length === 0" class="muted">判断履歴はありません。</p>
    <div v-for="item in reviewed" :key="item.id" class="pkb-record">
      <strong>{{ item.entity_name || item.id }}</strong>
      <span class="muted small"> {{ item.review_status || item.reason }}</span>
      <p v-if="item.raw_text">{{ item.raw_text }}</p>
    </div>
  </section>
  <section class="card" aria-label="PKB検索">
    <div class="section-head">
      <h2>記憶・Source検索</h2>
      <a class="link-button" href="/ui/?page=entities">Entity一覧・履歴・関連 →</a>
    </div>
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
