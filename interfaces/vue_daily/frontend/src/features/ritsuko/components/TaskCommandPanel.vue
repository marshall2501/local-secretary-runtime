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
const defaultStrategy = defineModel<DecisionStrategy>('defaultStrategy', { required: true })
const showStrategySelector = defineModel<boolean>('showStrategySelector', { required: true })
const reply = defineModel<string>('reply', { required: true })
const props = defineProps<{
  commandsEnabled: boolean
  activeDefaultStrategy: DecisionStrategy
  activeShowStrategySelector: boolean
  preferencesLoaded: boolean
  preferencesBusy: boolean
  preferencesMessage: string
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
  remember: []
  stop: []
  resume: []
  refresh: []
  savePreferences: []
}>()
// MAGI's intermediate state is informational; RITSUKO's persisted phase is
// the single authority for presenting Answer/Review controls.
const answerable = computed(() =>
  props.task?.status === 'waiting_external' &&
  props.task.phase === 'awaiting_clarification')
const reviewable = computed(() =>
  props.task?.status === 'waiting_external' &&
  props.task.phase === 'awaiting_review' &&
  (props.task.can_answer_only === true || props.task.can_remember === true))
const operationBusy = computed(() =>
  ['accepted', 'running'].includes(props.operation?.status ?? ''))
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
        <template v-if="activeShowStrategySelector">
          <label for="task-strategy">判断方式（検証用）</label>
          <select id="task-strategy" v-model="strategy"
            :disabled="busy || !commandsEnabled || !preferencesLoaded">
            <option value="standard">従来方式（段階判断／MAGI）</option>
            <option value="llm">Goal方式（LLMによる次の判断／MAGI）</option>
          </select>
        </template>
        <span v-else class="muted small">
          既定判断方式：{{ activeDefaultStrategy === 'llm' ? 'Goal / LLM' : '従来' }}
        </span>
        <button type="submit" class="primary-command"
          :disabled="busy || !commandsEnabled || !preferencesLoaded || !request.trim()">
          {{ busy ? '受付中…' : '依頼を送信' }}
        </button>
      </div>
    </form>
    <details class="command-preferences">
      <summary>判断方式の設定</summary>
      <p class="muted small">
        既定方式は新規Taskだけに適用。MAGIの有効席数・モデル・既存Taskは変更しません。
      </p>
      <div class="command-row">
        <label for="task-default-strategy">新規Taskの既定方式</label>
        <select id="task-default-strategy" v-model="defaultStrategy"
          :disabled="!commandsEnabled || preferencesBusy || !preferencesLoaded">
          <option value="standard">従来方式</option>
          <option value="llm">Goal / LLM方式</option>
        </select>
      </div>
      <label class="command-row">
        <input v-model="showStrategySelector" type="checkbox"
          :disabled="!commandsEnabled || preferencesBusy || !preferencesLoaded" />
        新規依頼に検証用の判断方式選択を表示
      </label>
      <button type="button" class="secondary"
        :disabled="!commandsEnabled || preferencesBusy || !preferencesLoaded"
        @click="emit('savePreferences')">
        {{ preferencesBusy ? '保存中…' : '設定を保存' }}
      </button>
      <p v-if="preferencesMessage" role="status" class="muted small">
        {{ preferencesMessage }}
      </p>
    </details>
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
      <div v-if="reviewable" class="command-form" aria-label="回答と記憶のReview">
        <span class="muted small">回答候補の確認</span>
        <div v-if="task?.can_remember && task.memory_review_candidate"
          class="task-field" aria-label="記憶反映前の登録候補">
          <strong>記憶へ登録する文章（本人確認前）</strong>
          <p>{{ task.memory_review_candidate.knowledge_candidate }}</p>
          <p class="muted small">
            この候補の根拠となった本人回答：{{ task.memory_review_candidate.user_text }}
          </p>
          <p class="muted small">
            記憶反映を選択すると、上記の文章をMemory Intakeへ渡します。
            内容によっては確認待ち候補となり、確定PKB事実とは限りません。
          </p>
        </div>
        <div class="command-row">
          <button v-if="task?.can_answer_only" type="button" class="secondary"
            :disabled="busy || operationBusy"
            @click="emit('review')">回答のみで完了を依頼</button>
          <button v-if="task?.can_remember && task.memory_review_candidate"
            type="button" class="secondary" :disabled="busy || operationBusy"
            @click="emit('remember')">記憶に反映して完了を依頼</button>
        </div>
      </div>
      <div v-if="stoppable" class="command-row">
        <button type="button" class="secondary" :disabled="busy"
          @click="emit('stop')">停止を要求</button>
        <span class="muted small">停止要求とWorker停止完了は別です。</span>
      </div>
      <p v-else-if="task.stop_requested" class="muted small">
        停止要求は保存されています。Taskの確定状態を確認してください。
      </p>
      <div v-if="task.status === 'paused'" class="command-row">
        <button v-if="task.can_resume" type="button" class="secondary"
          :disabled="busy || operationBusy" @click="emit('resume')">
          同じTaskを再開
        </button>
        <span v-else class="muted small">
          この停止済みTaskは先頭から安全に再開できません。
          実行済みの内容を重複させないため、途中復元は未対応です。
        </span>
      </div>
      <div v-if="operation?.execution_reconciliation_required"
        class="question" role="status" aria-label="Worker照合待ち">
        <strong>Workerの稼働状況を確認できません</strong>
        <p>DB上のTaskは処理中ですが、現在のAPIプロセスに実行中のOperationはありません。
          他のWorkerが動作している可能性は残るため、完了や停止と断定しません。</p>
        <p class="muted small">自動再実行は行いません。必要なら状態を再取得し、
          保存済みTaskの停止要求を利用してください。</p>
      </div>
      <p v-if="operation" class="muted small">
        操作状態: {{ operation.status }}
      </p>
    </template>
    <div class="command-row" v-if="task">
      <button type="button" class="secondary" :disabled="busy"
        @click="emit('refresh')">保存済みTaskを再取得</button>
      <span class="muted small">既存のTaskを再照会します。再実行やDB変更はしません。</span>
    </div>
    <p v-if="feedback" class="command-feedback" role="status">{{ feedback }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="commandsEnabled" class="muted small">
      送信後のTask確定状態と結果は、保存済みTaskから再照会します。
    </p>
  </section>
</template>
