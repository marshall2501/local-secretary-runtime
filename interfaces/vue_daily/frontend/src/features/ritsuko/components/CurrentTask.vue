<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** 現在TaskとDB確定状態、MAGI中間候補を混同せず表示する。 */
import { taskStatusLabel } from '../types'
import type { TaskRecord } from '../types'
defineProps<{ task: TaskRecord | null; loading: boolean }>()
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
      <div class="task-field"><span class="field-name">MAGI</span>
        <p>{{ task.magi_session?.status ?? '保存済み候補なし' }}</p>
        <span class="muted small">MAGI候補だけではTask完了になりません。</span>
      </div>
      <div v-if="task.question" class="question">
        <strong>本人への質問（保存済み）</strong><p>{{ task.question }}</p>
      </div>
      <div v-if="task.message" class="result">
        <strong>最新結果（保存済み）</strong><p>{{ task.message }}</p>
      </div>
      <div class="task-meta">
        <span>Task ID: <code>{{ task.id }}</code></span>
        <span>Revision: {{ task.revision }}</span>
      </div>
    </template>
    <p v-else class="muted">{{ loading ? '保存済みTaskを読み込み中です。' : '右側からTaskを選択してください。' }}</p>
  </section>
</template>
