<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/**
 * 右側の一覧で選択した保存済みTaskを表示する。
 * 「現在のTask」は画面の選択対象であり、必ずしも実行中という意味ではない。
 * Taskの確定状態とMAGIの途中状態は別項目で示す。
 */
import { ref } from 'vue'
import { taskStatusLabel } from '../types'
import type { TaskRecord, TaskTrace } from '../types'

defineProps<{ task: TaskRecord | null; trace: TaskTrace | null; loading: boolean }>()
const copyFeedback = ref('')
/** 本人操作時だけブラウザのClipboardへコピーする。送信・サーバー保存はしない。 */
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
      <div class="task-field"><span class="field-name">依頼</span><p>{{ task.request }}</p></div>
      <div class="task-field"><span class="field-name">現在</span>
        <p>{{ taskStatusLabel(task.status) }}（Task確定状態）</p>
      </div>
      <div v-if="task.goal_definition" class="task-field">
        <span class="field-name">Goal</span>
        <div v-if="task.goal_definition.resolution === 'proposed'">
          <p>{{ task.goal_definition.interpreted_goal || '解釈候補の本文が保存されていません。' }}</p>
          <p class="muted small">MAGIの解釈候補 / v{{ task.goal_definition.version }}。本人確定ではありません。</p>
          <ul v-if="task.goal_definition.criteria?.length" class="goal-criteria">
            <li v-for="(criterion, index) in task.goal_definition.criteria" :key="index">{{ criterion }}</li>
          </ul>
        </div>
        <div v-else-if="task.goal_definition.resolution === 'confirmed'">
          <p>{{ task.goal_definition.interpreted_goal || '確定済みGoal（本文は保存されていません）' }}</p>
          <p class="muted small">Goal確定済み / v{{ task.goal_definition.version }}。Task完了とは別です。</p>
          <ul v-if="task.goal_definition.criteria?.length" class="goal-criteria">
            <li v-for="(criterion, index) in task.goal_definition.criteria" :key="index">{{ criterion }}</li>
          </ul>
        </div>
        <p v-else class="muted">依頼固有の達成条件はまだ未確定です。</p>
      </div>
      <div class="task-field"><span class="field-name">MAGI</span>
        <div>
          <p>{{ task.magi_session?.status ?? '保存済み候補なし' }}</p>
          <p v-if="task.magi_session?.next_step" class="muted small">次の処理: {{ task.magi_session.next_step }}</p>
          <p class="muted small">MAGI候補だけではTask完了になりません。</p>
        </div>
      </div>
      <div v-if="task.phase" class="task-field">
        <span class="field-name">処理段階</span><p>{{ task.phase }}</p>
      </div>
      <div v-if="task.status === 'running' && task.stop_requested" class="task-field">
        <span class="field-name">停止</span>
        <p>停止要求を受付済み。Workerの停止完了は未確認です。</p>
      </div>
      <div v-if="task.status === 'waiting_external' && task.magi_session?.status === 'waiting_user'"
        class="question">
        <strong>次の操作：本人回答</strong>
        <p>{{ task.question || task.magi_session.user_question || '回答入力欄から追加情報を送信してください。' }}</p>
      </div>
      <div v-else-if="task.status === 'waiting_external' && task.magi_session?.status === 'proposal_ready'"
        class="question">
        <strong>次の操作：回答候補の確認</strong>
        <p>このTaskの回答候補を確認し、上のReview操作から完了判断を依頼してください。</p>
      </div>
      <div v-if="task.question && !(task.status === 'waiting_external' && task.magi_session?.status === 'waiting_user')" class="question">
        <strong>本人への質問（保存済み）</strong><p>{{ task.question }}</p>
      </div>
      <div v-if="task.message" class="result">
        <strong>最新結果（保存済み）</strong><p>{{ task.message }}</p>
      </div>
      <div class="task-copy-actions">
        <button v-if="task.message" type="button" class="secondary"
          @click="copyText(task.message, '最新結果')">最新結果をコピー</button>
        <button v-if="trace" type="button" class="secondary"
          @click="copyText(JSON.stringify(trace, null, 2), 'Task全情報')">Task全情報をコピー</button>
      </div>
      <p v-if="copyFeedback" class="muted small" role="status">{{ copyFeedback }}</p>
      <div class="task-meta">
        <span>Task ID: <code>{{ task.id }}</code></span>
        <span>Revision: {{ task.revision }}</span>
      </div>
    </template>
    <p v-else class="muted">{{ loading ? '保存済みTaskを読み込み中です。' : '右側からTaskを選択してください。' }}</p>
  </section>
</template>
