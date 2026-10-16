<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/**
 * 保存済みAction/Result/SourceとTaskの監査イベントを表示する。
 * verified_byは検証経路の識別であり、文章の意味的正しさの証明ではない。
 */
import type { TaskTrace } from '../types'
defineProps<{ trace: TaskTrace | null }>()
</script>

<template>
  <section v-if="trace" class="card" aria-label="実行履歴と結果">
    <details>
      <summary class="activity-summary">検証情報・実行経緯
        <span class="muted small">Action: {{ trace.actions.length }}件 /
          監査イベント: {{ trace.advisor_events?.length || 0 }}件</span>
      </summary>
      <p v-if="!trace.actions.length" class="muted small">記録されたActionはありません。</p>
      <div v-for="(action, index) in trace.actions" :key="action.action_id ?? index"
        class="activity-entry">
        <div class="field-name">{{ action.tool || 'Action' }} /
          {{ action.operation || '-' }} / {{ action.action_status || '-' }}</div>
        <p>{{ action.summary || action.outcome || 'Result未記録' }}</p>
        <p v-if="action.outcome" class="muted small">結果状態: {{ action.outcome }}</p>
        <p v-if="action.verified_by" class="muted small">
          確認経路: {{ action.verified_by }}
          <span v-if="action.verified_at"> / {{ action.verified_at }}</span>
        </p>
        <p v-else-if="action.result_id" class="muted small">検証経路の記録なし</p>
        <p v-if="action.source_uri" class="source-uri">参照元: {{ action.source_uri }}</p>
        <code v-if="action.result_id">Result ID: {{ action.result_id }}</code>
      </div>
      <details v-if="trace.advisor_events?.length" class="audit-events">
        <summary>監査イベント（{{ trace.advisor_events.length }}件）</summary>
        <p v-for="(event, index) in trace.advisor_events" :key="index" class="muted small">
          {{ event.occurred_at || '時刻不明' }} / {{ event.event_type }}
        </p>
      </details>
      <p class="muted small">確認経路の記録は情報の取得手順を示し、回答内容の十分性・正確性を保証しません。</p>
    </details>
  </section>
</template>
