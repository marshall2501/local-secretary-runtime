<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/**
 * Vueは既存RITSUKO Task APIへコマンドを配送するだけ。
 * Taskの完了判断、実行、権限処理、DBへの保存は行わない。
 */
import { computed } from 'vue'
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
</script>

<template>
  <section id="task-actions" class="card request-entry" aria-label="RITSUKOへの新しい依頼">
    <div class="section-head">
      <h2>{{ answerable || reviewable || task?.status === 'paused'
        ? '選択中Taskへの操作' : '新しい依頼を入力' }}</h2>
      <span class="small" :class="commandsEnabled ? 'command-mode-enabled' : 'muted'">
        {{ commandsEnabled ? '入力・送信可能' : 'Task操作API未接続' }}
      </span>
    </div>

    <p v-if="!commandsEnabled" class="read-only-notice" role="status">
      Task操作APIが接続されていません。システム診断で状態を確認してください。
    </p>

    <!-- 本人回答・Review・停止など選択Taskの操作を新規依頼より優先表示する。 -->
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
      <div v-if="task.status === 'paused'" class="command-row">
        <button v-if="task.can_resume" type="button" class="secondary"
          :disabled="busy || operationBusy" @click="emit('resume')">
          同じTaskを再開
        </button>
        <span v-else class="muted small">
          このTaskには安全に使える再開点がありません。
          実行済み内容の重複を避けるため、自動的な再実行はできません。
        </span>
      </div>
    </template>

    <h3 v-if="answerable || reviewable || task?.status === 'paused'"
      class="new-request-heading">新しい依頼を入力</h3>
    <!-- textarea自体は常に配置し、読取専用の場合も入力場所を隠さない。 -->
    <form class="command-form" @submit.prevent="emit('start')">
      <label for="task-request">依頼内容</label>
      <textarea id="task-request" v-model="request" rows="2"
        maxlength="4000" autocomplete="off"
        :placeholder="commandsEnabled ? '秘書AIに依頼したいことを入力してください' : 'Task操作APIが接続されていません'"
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

    <div class="command-row" v-if="task">
      <button type="button" class="secondary" :disabled="busy"
        @click="emit('refresh')">保存済みTaskを再取得</button>
      <span class="muted small">既存のTaskを再照会します。再実行やDB変更はしません。</span>
    </div>
    <p v-if="feedback && task?.status !== 'completed' && task?.status !== 'failed'"
      class="command-feedback" role="status">{{ feedback }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>

  </section>
</template>

<style scoped>
/* 長文編集可能なまま、既存回答を押し下げない入力高さにする。 */
.new-request-heading { border-top: 1px solid #dce5ec; padding-top: 12px;
  margin-top: 15px; margin-bottom: 6px; font-size: 15px; }
.request-entry #task-request { min-height: 78px; max-width: 100%; }
.request-entry .command-preferences { margin-top: 2px; }
.request-entry .command-preferences > summary { cursor: pointer; font-size: 13px; }
.request-entry .command-form { margin-bottom: 7px; }
</style>
