<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/**
 * 保存済みRITSUKO Taskの読取専用Vue画面。
 * 状態・完了は常にRITSUKO DB-backed APIを正本とし、ブラウザでは確定しない。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'

type Task = {
  id: string
  request: string
  status: string
  revision: number
  core_slice?: string
  updated_at?: string
}
type Trace = {
  task: Task & {
    message?: string
    question?: string
    phase?: string
    magi_session?: { status?: string; next_step?: string }
  }
  actions: Array<{
    action_id: string
    tool?: string
    operation?: string
    action_status?: string
    result_id?: string | null
    outcome?: string | null
    summary?: string | null
  }>
  advisor_events: Array<{ event_type: string }>
}

const tasks = ref<Task[]>([])
const view = ref<'recent' | 'open' | 'completed'>('recent')
const selectedId = ref(window.location.hash.match(/^#task=([a-f\d-]{36})$/)?.[1] ?? '')
const trace = ref<Trace | null>(null)
const connected = ref(false)
const errorText = ref('')
let refreshTimer: ReturnType<typeof setInterval> | undefined

const statusName: Record<string, string> = {
  pending: '受付', running: '処理中', waiting_external: '本人への確認待ち',
  waiting_user: '本人の回答待ち', paused: '停止', completed: '完了',
  failed: '失敗', cancelled: '取消',
}
const selected = computed(() => trace.value?.task ?? null)
const magiStatus = computed(() => selected.value?.magi_session?.status ?? '未記録')

async function get<T>(endpoint: string): Promise<T> {
  const response = await fetch(endpoint, { cache: 'no-store', credentials: 'same-origin' })
  if (!response.ok) throw new Error('API応答 ' + response.status)
  return await response.json() as T
}

async function loadSelected(id: string): Promise<void> {
  if (!id) { trace.value = null; return }
  const response = await get<{ trace: Trace; source: string }>(
    '/api/v1/ritsuko/tasks/' + encodeURIComponent(id)
  )
  // 画面再取得が先に完了しても、現在選択中のTask以外は上書きしない。
  if (selectedId.value === id) trace.value = response.trace
}

async function refresh(): Promise<void> {
  try {
    const query = new URLSearchParams({ view: view.value, limit: '20' })
    const response = await get<{ items: Task[]; source: string }>(
      '/api/v1/ritsuko/tasks?' + query.toString()
    )
    tasks.value = response.items
    if (!selectedId.value && tasks.value.length) {
      selectedId.value = tasks.value[0]!.id
      window.location.hash = 'task=' + selectedId.value
    }
    if (selectedId.value) await loadSelected(selectedId.value)
    errorText.value = ''
    connected.value = true
  } catch (error) {
    connected.value = false
    errorText.value = 'Task APIに接続できません。表示内容は直近取得時点のものです。' +
      '（' + String(error) + '）'
  }
}

function chooseTask(id: string): void {
  selectedId.value = id
  trace.value = null
  window.location.hash = 'task=' + id
  void refresh()
}

function chooseView(next: 'recent' | 'open' | 'completed'): void {
  view.value = next
  selectedId.value = ''
  trace.value = null
  window.location.hash = ''
  void refresh()
}

onMounted(() => {
  void refresh()
  refreshTimer = setInterval(() => { void refresh() }, 3000)
})
onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<template>
  <div class="shell">
    <header class="header">
      <div>
        <span class="eyebrow">Personal Local Secretary AI</span>
        <h1>秘書AI / Vue日常UI</h1>
      </div>
      <div class="connection" :class="{ disconnected: !connected }">
        <span class="dot" />{{ connected ? '保存済みTask API接続中' : 'API未接続' }}
      </div>
    </header>
    <div class="warning" role="note">
      <strong>実Task・読取専用の移行段階</strong>
      — 本画面は既存RITSUKOの保存済みTaskを表示します。
      新規依頼、MAGI呼出し、回答、停止、設定変更はまだ行いません。
      従来の日常画面は引き続き利用できます。
    </div>
    <div v-if="errorText" class="error" role="alert">{{ errorText }}</div>
    <div class="workspace">
      <aside class="sidebar card">
        <h2>秘書AI</h2>
        <div class="nav-current">依頼・Task</div>
        <p class="muted small">PKB・設定・家計等は既存画面で利用できます。</p>
        <div class="divider" />
        <div class="side-heading">
          <h3>保存済みTask一覧</h3>
          <span class="muted small">{{ tasks.length }}件表示</span>
        </div>
        <div class="form-actions" style="justify-content: flex-start; flex-wrap: wrap">
          <button v-for="item in (['recent','open','completed'] as const)" :key="item"
            class="secondary" :aria-pressed="view === item"
            @click="chooseView(item)">
            {{ item === 'recent' ? '最近' : item === 'open' ? '進行中' : '完了' }}
          </button>
        </div>
        <button v-for="task in tasks" :key="task.id"
          class="task-item" :class="{ active: selectedId === task.id }"
          :aria-current="selectedId === task.id ? 'true' : undefined"
          @click="chooseTask(task.id)">
          <span class="item-title">{{ task.request }}</span>
          <span class="small">{{ statusName[task.status] ?? task.status }}
            / rev={{ task.revision }}</span>
        </button>
        <p v-if="!tasks.length" class="muted small">該当する保存済みTaskはありません。</p>
      </aside>

      <main class="main">
        <div class="card details">
          <div class="section-head">
            <h2>Taskの確定状態と結果</h2>
            <span v-if="selected" class="status" :class="'status-' + selected.status">
              {{ statusName[selected.status] ?? selected.status }}
            </span>
          </div>
          <template v-if="selected">
            <div class="field">
              <div class="field-name">Task ID / Revision</div>
              <code>{{ selected.id }} / {{ selected.revision }}</code>
            </div>
            <div class="field">
              <div class="field-name">依頼</div>
              <p>{{ selected.request }}</p>
            </div>
            <div class="field">
              <div class="field-name">RITSUKO確定状態 / MAGI候補状態</div>
              <p>{{ statusName[selected.status] ?? selected.status }} /
                {{ magiStatus }}</p>
              <span class="muted small">
                MAGIの候補状態はTaskの完了判定ではありません。
              </span>
            </div>
            <div v-if="selected.question" class="question">
              <strong>本人への質問（保存済み）</strong>
              <p>{{ selected.question }}</p>
            </div>
            <div v-if="selected.message" class="result">
              <strong>保存済みTask結果</strong>
              <p>{{ selected.message }}</p>
            </div>
          </template>
          <p v-else class="muted">左のTaskを選択してください。</p>
        </div>
        <div v-if="trace" class="card">
          <div class="section-head">
            <h2>Action / Result の保存記録</h2>
            <span class="muted small">{{ trace.actions.length }}件</span>
          </div>
          <p v-if="!trace.actions.length" class="muted">記録されたActionはありません。</p>
          <div v-for="action in trace.actions" :key="action.action_id" class="field">
            <div class="field-name">{{ action.tool || 'Action' }} /
              {{ action.operation || '-' }} / {{ action.action_status || '-' }}</div>
            <p>{{ action.summary || action.outcome || 'Result未記録' }}</p>
            <code v-if="action.result_id">Result ID: {{ action.result_id }}</code>
          </div>
        </div>
      </main>
    </div>
  </div>
</template>
