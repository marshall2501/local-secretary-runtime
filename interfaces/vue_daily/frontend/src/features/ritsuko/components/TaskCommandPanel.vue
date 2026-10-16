<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/**
 * Vueは既存RITSUKO Task APIへコマンドを配送するだけ。
 * Taskの完了判断、実行、権限処理、DBへの保存は行わない。
 */
import { computed } from 'vue'
import { legacyPageUrl } from '../../../shared/navigation'
import type { DecisionStrategy, TaskOperation } from '../api'
import type { TaskRecord } from '../types'

const request = defineModel<string>('request', { required: true })
const strategy = defineModel<DecisionStrategy>('strategy', { required: true })
const reply = defineModel<string>('reply', { required: true })
const props = defineProps<{
  commandsEnabled: boolean
  busy: boolean
  task: TaskRecord | null
  feedback: string
  error: string
  operation: TaskOperation | null
}>()
const emit = defineEmits<{
  start: []
  answer: []
  review: []
  stop: []
}>()
const answerable = computed(() =>
  props.task?.status === 'waiting_external' &&
  props.task.magi_session?.status === 'waiting_user')
const reviewable = computed(() =>
  props.task?.status === 'waiting_external' &&
  props.task.magi_session?.status === 'proposal_ready')
const stoppable = computed(() =>
  props.task !== null &&
  ['pending', 'running'].includes(props.task.status) &&
  props.task.stop_requested !== true)
const niceguiCore = legacyPageUrl('/core')
</script>

<template>
  <section class="card request-entry" aria-label="RITSUKO Task操作">
    <div class="section-head">
      <h2>新しい依頼・本人回答</h2>
      <span v-if="commandsEnabled" class="muted small">実Task API</span>
    </div>
    <template v-if="commandsEnabled">
      <form class="command-form" @submit.prevent="emit('start')">
        <label for="task-request">依頼内容</label>
        <textarea id="task-request" v-model="request" rows="3"
          maxlength="4000" placeholder="してほしいことを入力してください"
          :disabled="busy" required />
        <div class="command-row">
          <label for="task-strategy">判断方式</label>
          <select id="task-strategy" v-model="strategy" :disabled="busy">
            <option value="standard">通常（MAGI）</option>
            <option value="llm">Goal判断（有効MAGI 1席）</option>
          </select>
          <button type="submit" class="primary-command"
            :disabled="busy || !request.trim()">依頼を受付</button>
        </div>
      </form>
      <template v-if="task">
        <form v-if="answerable" class="command-form" @submit.prevent="emit('answer')">
          <label for="task-answer">保存済みの質問への回答</label>
          <textarea id="task-answer" v-model="reply" rows="2" maxlength="4000"
            :disabled="busy" required />
          <button type="submit" class="secondary"
            :disabled="busy || !reply.trim()">本人回答を送る</button>
        </form>
        <div v-if="reviewable" class="command-row">
          <span class="muted small">回答候補の確認</span>
          <button type="button" class="secondary" :disabled="busy"
            @click="emit('review')">回答だけで完了するよう依頼</button>
          <span class="muted small">記憶への反映は既存画面から操作してください。</span>
        </div>
        <div v-if="stoppable" class="command-row">
          <button type="button" class="secondary" :disabled="busy"
            @click="emit('stop')">停止を要求</button>
          <span class="muted small">停止要求とWorker停止完了は別です。</span>
        </div>
        <p v-else-if="task.stop_requested" class="muted small">
          停止要求は保存されています。Task確定状態は上の表示を確認してください。
        </p>
        <p v-if="operation" class="muted small">
          操作受付: {{ operation.status }}
          <span v-if="operation.execution_reconciliation_required">
            （Workerの実行状態は照合が必要）
          </span>
        </p>
      </template>
      <p v-if="feedback" class="command-feedback" role="status">{{ feedback }}</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <p class="muted small">Taskの確定状態と回答は、保存済みTask一覧から再照会して表示します。</p>
    </template>
    <template v-else>
      <p class="muted">
        現在のプレビューは読取専用です。依頼・回答・停止・Reviewは既存のRITSUKOから操作できます。
      </p>
      <a class="link-button" :href="niceguiCore">既存画面で依頼・回答する</a>
    </template>
  </section>
</template>
