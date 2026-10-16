<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** Action/Resultの実保存記録を必要時に展開する（書き込み機能なし）。 */
import type { TaskTrace } from '../types'
defineProps<{ trace: TaskTrace | null }>()
</script>

<template>
  <section v-if="trace" class="card" aria-label="実行履歴と結果">
    <details>
      <summary class="activity-summary">検証情報・実行経緯
        <span class="muted small">Action / Result: {{ trace.actions.length }}件</span>
      </summary>
      <p v-if="!trace.actions.length" class="muted small">記録されたActionはありません。</p>
      <div v-for="(action, index) in trace.actions" :key="action.action_id ?? index"
        class="activity-entry">
        <div class="field-name">{{ action.tool || 'Action' }} /
          {{ action.operation || '-' }} / {{ action.action_status || '-' }}</div>
        <p>{{ action.summary || action.outcome || 'Result未記録' }}</p>
        <code v-if="action.result_id">Result ID: {{ action.result_id }}</code>
      </div>
    </details>
  </section>
</template>
