<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/**
 * ログインなしで架空Taskの受付・回答・停止を試すVue画面。
 * 本番DBや実LLMとは接続しない。成功・完了表示は隔離fixtureの結果に限る。
 * Task操作はAPIへ送り、応答後の状態は架空Taskストアから再取得する。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { callApi, type TaskView } from './api'

const requestText = ref('')
const replyText = ref('')
const tasks = ref<TaskView[]>([])
const selected = ref<TaskView | null>(null)
const busy = ref(false)
const connected = ref(false)
const info = ref('')
const problem = ref('')
let refreshTimer: ReturnType<typeof setInterval> | undefined

const statusText: Record<string, string> = {
  queued: '受付',
  running: '実行中',
  waiting_user: '本人の回答待ち',
  completed: '完了（架空Task）',
  failed: '失敗',
  paused: '停止',
}
const canAnswer = computed(() => selected.value?.status === 'waiting_user')
const canStop = computed(() => ['queued', 'running', 'waiting_user'].includes(selected.value?.status ?? ''))

/** F5しても同じ架空Taskを選べるよう、選択IDをURLハッシュに残す。 */
function selectTask(task: TaskView) {
  selected.value = task
  window.location.hash = 'task=' + encodeURIComponent(task.task_id)
  problem.value = ''
}

/** ログイン不要の隔離APIから、一覧と選択した架空Taskの状態を取得する。 */
async function refresh() {
  const response = await callApi<{ items: TaskView[] }>('/api/lab/tasks')
  tasks.value = response.items
  const id = selected.value?.task_id
    ?? window.location.hash.match(/^#task=([a-f\d-]{36})$/)?.[1]
  if (id) {
    selected.value = tasks.value.find(task => task.task_id === id) ?? null
  }
  if (!selected.value && tasks.value.length && !id) {
    selectTask(tasks.value[0]!)
  }
  connected.value = true
}

/** 架空Taskの受付応答だけ待ち、結果は後続の状態照会で確認する。 */
async function startTask() {
  if (!requestText.value.trim()) return
  busy.value = true
  problem.value = ''
  info.value = ''
  try {
    const task = await callApi<TaskView>(
      '/api/lab/tasks', 'POST', { request: requestText.value },
    )
    requestText.value = ''
    selectTask(task)
    await refresh()
    info.value = '架空Taskを受付けました。MAGIへの実送信はありません。'
  } catch (error) {
    problem.value = String(error)
  } finally {
    busy.value = false
  }
}

/** 質問待ちである同じTask IDへ本人回答を渡す。 */
async function answerTask() {
  if (!selected.value || !replyText.value.trim()) return
  busy.value = true
  problem.value = ''
  try {
    const task = await callApi<TaskView>(
      '/api/lab/tasks/' + selected.value.task_id + '/answer',
      'POST', { reply: replyText.value },
    )
    replyText.value = ''
    selectTask(task)
    await refresh()
  } catch (error) {
    problem.value = String(error)
  } finally {
    busy.value = false
  }
}

/** 停止要求を隔離APIに送り、停止確定と混同しないようTaskを再照会する。 */
async function stopTask() {
  if (!selected.value) return
  busy.value = true
  problem.value = ''
  try {
    const task = await callApi<TaskView>(
      '/api/lab/tasks/' + selected.value.task_id + '/stop', 'POST', {},
    )
    selectTask(task)
    await refresh()
  } catch (error) {
    problem.value = String(error)
  } finally {
    busy.value = false
  }
}

/** API切断時も最後に取得した表示を残し、次回の定期照会で接続を試す。 */
async function poll() {
  if (busy.value) return
  try {
    await refresh()
    info.value = ''
  } catch (error) {
    connected.value = false
    info.value = 'APIに再接続できません。画面は維持し、接続を再試行します。'
  }
}

onMounted(async () => {
  await poll()
  refreshTimer = setInterval(() => { void poll() }, 1000)
})
onUnmounted(() => {
  if (refreshTimer !== undefined) clearInterval(refreshTimer)
})
</script>

<template>
  <div class="shell">
    <header class="header">
      <div>
        <span class="eyebrow">Personal Local Secretary AI</span>
        <h1>秘書AI <span class="muted">/ Vue分離試作</span></h1>
      </div>
      <div class="connection" :class="{ disconnected: !connected }">
        <span class="dot" />{{ connected ? '隔離API接続中' : 'API未接続' }}
      </div>
    </header>
    <div class="warning" role="note">
      <strong>架空データ専用</strong> — 外部LLM・本番DB・実Actionには接続しません。
      ここに表示する「完了」は合成テスト上のTask状態です。
    </div>
    <div class="workspace">
      <aside class="sidebar card">
        <h2>秘書AI</h2>
        <div class="nav-current">依頼・Task</div>
        <p class="muted">PKB・設定・家計などは今回の試作対象外です。</p>
        <div class="divider" />
        <div class="side-heading">
          <h3>Task一覧</h3>
          <span class="muted">{{ tasks.length }}件</span>
        </div>
        <button
          v-for="task in tasks"
          :key="task.task_id"
          class="task-item"
          :class="{ active: task.task_id === selected?.task_id }"
          :aria-current="task.task_id === selected?.task_id ? 'true' : undefined"
          @click="selectTask(task)"
        >
          <span class="item-title">{{ task.request }}</span>
          <span class="small">{{ statusText[task.status] || task.status }}</span>
        </button>
        <p v-if="!tasks.length" class="muted">まだTaskはありません。</p>
      </aside>
      <main class="main">
        <div class="card request-card">
          <div class="section-head">
            <div>
              <span class="eyebrow">RITSUKO Application API</span>
              <h2>新しい依頼</h2>
            </div>
            <span class="muted small">受付後はTask IDで照会</span>
          </div>
          <form @submit.prevent="startTask">
            <label for="request">依頼内容</label>
            <textarea id="request" v-model="requestText" rows="3" maxlength="4000"
                      placeholder="架空の確認用依頼を入力してください" required />
            <div class="form-actions">
              <button class="primary" :disabled="busy || !requestText.trim()">新しい依頼を開始</button>
            </div>
          </form>
        </div>
        <p v-if="info" role="status" class="message">{{ info }}</p>
        <p v-if="problem" role="alert" class="error">{{ problem }}</p>
        <div class="card details">
          <div class="section-head">
            <h2>選択Taskの状態と結果</h2>
            <span v-if="selected" class="status" :class="'status-' + selected.status">
              {{ statusText[selected.status] || selected.status }}
            </span>
          </div>
          <template v-if="selected">
            <div class="field">
              <div class="field-name">Task ID</div>
              <code>{{ selected.task_id }}</code>
            </div>
            <div class="field">
              <div class="field-name">依頼</div>
              <p>{{ selected.request }}</p>
            </div>
            <div v-if="selected.question" class="question">
              <strong>RITSUKOからの質問（架空）</strong>
              <p>{{ selected.question }}</p>
              <form v-if="canAnswer" @submit.prevent="answerTask">
                <label for="reply">回答</label>
                <textarea id="reply" v-model="replyText" maxlength="4000" rows="2" required />
                <div class="form-actions">
                  <button class="primary" :disabled="busy || !replyText.trim()">同じTaskへ回答する</button>
                </div>
              </form>
            </div>
            <div v-if="selected.message" class="result">
              <strong>Task結果（架空）</strong>
              <p>{{ selected.message }}</p>
            </div>
            <p v-if="selected.error" class="error">{{ selected.error }}</p>
            <div class="form-actions">
              <button v-if="canStop" type="button" class="secondary" :disabled="busy"
                      @click="stopTask">停止を要求</button>
              <span class="muted small">状態はAPIのTask正本から取得します。</span>
            </div>
          </template>
          <p v-else class="muted">Taskを作成するか、一覧から選択してください。</p>
        </div>
      </main>
    </div>
  </div>
</template>
