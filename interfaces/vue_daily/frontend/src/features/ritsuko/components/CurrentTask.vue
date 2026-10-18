<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** Task確定状態・回答・出典を先に示す。MAGI候補は詳細で区別する。 */
import { computed, ref } from 'vue'
import { taskStatusLabel } from '../types'
import type { TaskRecord, TaskTrace } from '../types'
import SafeMarkdown from './SafeMarkdown.vue'

const props = defineProps<{ task: TaskRecord | null; trace: TaskTrace | null; loading: boolean }>()
const copyFeedback = ref('')
const verifiedActions = computed(() => (props.trace?.actions ?? [])
  .filter(action => action.action_status === 'succeeded' && action.result_id))
const sourceLabels = computed(() => Array.from(new Set(verifiedActions.value
  .map(action => action.tool === 'pkb' ? '個人PKB' : action.tool === 'web'
    ? 'Web検索' : action.tool === 'finance' ? '家計' : action.tool ?? 'その他'))))
const observedCount = computed(() => (props.task?.magi_session?.observations ?? [])
  .filter(obs => obs.verified && obs.result_id)
  .reduce((total, obs) => total + (obs.result_count ?? 0), 0))

/** 本人操作時だけクリップボードへコピーする。 */
async function copyText(text: string, label: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    copyFeedback.value = label + 'をコピーしました。'
  } catch {
    copyFeedback.value = 'コピーできませんでした。ブラウザのClipboard許可を確認してください。'
  }
}
</script>

<template>
  <section class="card current-task" aria-label="現在のTask">
    <div class="section-head">
      <h2>現在のTask</h2>
      <span v-if="task" class="status" :class="'status-' + task.status">
        {{ taskStatusLabel(task.status) }}
      </span>
    </div>
    <template v-if="task">
      <p class="task-request-preview">{{ task.request }}</p>
      <p class="muted small">{{ taskStatusLabel(task.status) }}（Task確定状態）</p>

      <div v-if="task.status === 'waiting_external' && task.phase === 'awaiting_clarification'"
        class="question">
        <strong>次の操作：本人回答</strong>
        <p>{{ task.question || task.magi_session?.user_question || '追加情報を送信してください。' }}</p>
        <a href="#task-actions" class="small">下の回答入力欄へ進む</a>
      </div>
      <div v-else-if="task.status === 'waiting_external' && task.phase === 'awaiting_review'"
        class="question">
        <strong>次の操作：回答候補の確認</strong>
        <p>内容を確認してから、下のReview操作で完了方法を選んでください。</p>
        <a href="#task-actions" class="small">Review操作へ進む</a>
      </div>
      <div v-if="task.status === 'running' && task.stop_requested" class="question">
        <strong>停止要求を受付済み</strong>
        <p>Workerの停止完了はまだ確認できていません。</p>
      </div>

      <div v-if="task.status === 'waiting_external' &&
        task.magi_session?.status === 'candidate_ready' &&
        task.magi_session?.detail?.answer_candidate" class="result">
        <strong>MAGI回答候補（未確定・本人確認待ち）</strong>
        <SafeMarkdown :text="task.magi_session.detail.answer_candidate" />
        <p class="muted small">MAGI候補はRITSUKOの完了判断ではありません。</p>
      </div>
      <div v-if="task.message" class="result" aria-label="Taskの最新回答">
        <strong>最新結果（保存済み）</strong>
        <SafeMarkdown :text="task.message" />
      </div>
      <div v-if="verifiedActions.length" class="evidence-summary" aria-label="回答の根拠">
        <strong>参照した情報源：{{ sourceLabels.join('・') }}</strong>
        <p>保存済みの読み取り {{ verifiedActions.length }} 回
          <template v-if="observedCount"> / 取得した記録 {{ observedCount }} 件</template>
        </p>
        <p v-if="sourceLabels.includes('個人PKB')" class="muted small">
          PKBに登録された情報の取得結果です。登録内容の正しさや実機状態の直接確認とは別です。
        </p>
        <p class="muted small">検索内容・Result ID・MAGIの判断は下の「MAGI判断履歴・実行詳細」で確認できます。</p>
      </div>
      <p v-else-if="task.reason === 'goal_evaluated_source_free'" class="muted small">
        情報源の検索を必要としない回答としてRITSUKOが完了を確定しました。
      </p>
      <div class="task-copy-actions">
        <button v-if="task.message" type="button" class="secondary"
          @click="copyText(task.message, '最新結果')">最新結果をコピー</button>
        <button v-if="trace" type="button" class="secondary"
          @click="copyText(JSON.stringify(trace, null, 2), 'Task全情報')">Task全情報をコピー</button>
      </div>
      <p v-if="copyFeedback" class="muted small" role="status">{{ copyFeedback }}</p>

      <details class="task-details">
        <summary>依頼・Goal・MAGI中間状態・Task ID</summary>
        <div class="task-field"><span class="field-name">元の依頼</span><p>{{ task.request }}</p></div>
        <div v-if="task.goal_definition" class="task-field">
          <span class="field-name">Goal</span>
          <div>
            <p>{{ task.goal_definition.interpreted_goal || 'Goal本文は保存されていません。' }}</p>
            <p class="muted small">{{ task.goal_definition.resolution === 'confirmed' ?
              'Goal確定済み' : 'MAGIの解釈候補（本人未確定）' }} /
              v{{ task.goal_definition.version }}</p>
            <ul v-if="task.goal_definition.criteria?.length" class="goal-criteria">
              <li v-for="(criterion, index) in task.goal_definition.criteria" :key="index">
                {{ criterion }}
              </li>
            </ul>
          </div>
        </div>
        <div class="task-field">
          <span class="field-name">MAGI保存状態</span>
          <p>{{ task.magi_session?.status || '候補なし' }} /
            {{ task.magi_session?.next_step || '-' }}（Task確定状態とは別）</p>
        </div>
        <div v-if="task.phase" class="task-field">
          <span class="field-name">処理段階</span><p>{{ task.phase }}</p>
        </div>
        <div v-if="task.question" class="task-field">
          <span class="field-name">本人への質問</span><p>{{ task.question }}</p>
        </div>
        <div class="task-meta">
          <span>Task ID: <code>{{ task.id }}</code></span>
          <span>Revision: {{ task.revision }}</span>
        </div>
      </details>
    </template>
    <p v-else class="muted">{{ loading ? '保存済みTaskを読み込み中です。' : '右側からTaskを選択してください。' }}</p>
  </section>
</template>

<style scoped>
.task-request-preview { font-weight: 600; line-height: 1.55; margin: 0 0 5px;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2;
  overflow: hidden; overflow-wrap: anywhere; }
.current-task .result { margin-top: 11px; }
.current-task .result :deep(.safe-markdown) { margin-top: 8px; }
.evidence-summary { margin-top: 11px; border-left: 3px solid #409888;
  background: #f1f8f6; padding: 10px 13px; border-radius: 5px; }
.evidence-summary p { margin: 5px 0 0; font-size: 13px; }
.task-details { border-top: 1px solid #d6e4e8; padding-top: 12px; margin-top: 14px; }
.task-details > summary { cursor: pointer; font-weight: 650; color: #35516c; }
.task-details[open] > summary { margin-bottom: 12px; }
</style>
