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
  <section class="card request-entry" aria-label="RITSUKOへの新しい依頼">
    <div class="section-head">
      <h2>新しい依頼を入力</h2>
      <span class="small" :class="commandsEnabled ? 'command-mode-enabled' : 'muted'">
        {{ commandsEnabled ? '入力・送信可能（8099）' : '読取専用' }}
      </span>
    </div>

    <p v-if="!commandsEnabled" class="read-only-notice" role="status">
      この画面ではTaskを送信できません。
      <a class="link-button" href="http://127.0.0.1:8099/ui/">実Task操作画面（8099）を開く</a>
    </p>

    <!-- textarea自体は常に配置し、読取専用の場合も入力場所を隠さない。 -->
    <form class="command-form" @submit.prevent="emit('start')">
      <label for="task-request">依頼内容</label>
      <textarea id="task-request" v-model="request" rows="4"
        maxlength="4000" autocomplete="off"
        :placeholder="commandsEnabled ? '秘書AIに依頼したいことを入力してください' : '8099番の実Task操作画面で入力できます'"
        :disabled="busy || !commandsEnabled" required />
      <div class="command-row">
        <label for="task-strategy">判断方式</label>
        <select id="task-strategy" v-model="strategy" :disabled="busy || !commandsEnabled">
          <option value="standard">従来方式（段階判断／MAGI）</option>
          <option value="llm">Goal方式（LLMによる次の判断／MAGI）</option>
        </select>
        <button type="submit" class="primary-command"
          :disabled="busy || !commandsEnabled || !request.trim()">
          {{ busy ? '受付中…' : '依頼を送信' }}
        </button>
      </div>
    </form>
    <p v-if="!commandsEnabled" class="muted small">
      8097では入力・送信を無効にしています。操作画面が使えない場合は
      <a :href="niceguiCore">既存のRITSUKO画面</a>を利用できます。
    </p>

    <template v-if="commandsEnabled && task">
      <form v-if="answerable" class="command-form" @submit.prevent="emit('answer')">
        <label for="task-answer">選択中のTaskへの本人回答</label>
        <textarea id="task-answer" v-model="reply" rows="2" maxlength="4000"
          :disabled="busy" required />
        <button type="submit" class="secondary"
          :disabled="busy || !reply.trim()">本人回答を送る</button>
      </form>
      <div v-if="reviewable" class="command-row">
        <span class="muted small">回答候補の確認</span>
        <button type="button" class="secondary" :disabled="busy"
          @click="emit('review')">回答のみで完了を依頼</button>
        <span class="muted small">記憶への反映は既存画面から操作してください。</span>
      </div>
      <div v-if="stoppable" class="command-row">
        <button type="button" class="secondary" :disabled="busy"
          @click="emit('stop')">停止を要求</button>
        <span class="muted small">停止要求とWorker停止完了は別です。</span>
      </div>
      <p v-else-if="task.stop_requested" class="muted small">
        停止要求は保存されています。Taskの確定状態を確認してください。
      </p>
      <p v-if="operation" class="muted small">
        操作状態: {{ operation.status }}
        <span v-if="operation.execution_reconciliation_required">
          （Workerの実行状態は照合が必要）
        </span>
      </p>
    </template>
    <p v-if="feedback" class="command-feedback" role="status">{{ feedback }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="commandsEnabled" class="muted small">
      送信後のTask確定状態と結果は、保存済みTaskから再照会します。
    </p>
  </section>
</template>
