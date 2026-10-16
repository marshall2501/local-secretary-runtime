<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/**
 * DBへ保存されたMAGI各席の最終Turn結果を表示する。
 * 現在通信中の席別状態を受信するAPIはまだないため「リアルタイム」と誤表示しない。
 */
import { computed } from 'vue'
import type { TaskRecord } from '../types'

const props = defineProps<{ task: TaskRecord | null }>()
const turn = computed(() => {
  const turns = props.task?.magi_session?.turns ?? []
  return turns.length ? turns[turns.length - 1] : null
})
const members = computed(() => {
  const configured = (props.task?.magi_session?.member_specs ?? []).filter(item => item.enabled)
  const results = turn.value?.member_results ?? []
  if (configured.length === 0) {
    return results.map(result => ({ name: result.name || 'MAGI', provider: result.provider || '',
      model: result.model || '', status: result.status || '', elapsed: result.elapsed_seconds }))
  }
  return configured.map(spec => {
    const result = results.find(item => item.name === spec.name)
    return { name: spec.name || 'MAGI', provider: spec.provider || '',
      model: spec.model || '', status: result?.status || '',
      elapsed: result?.elapsed_seconds }
  })
})
</script>

<template>
  <section v-if="task?.magi_session" class="card" aria-label="MAGI各席の保存済み処理状況">
    <div class="section-head">
      <h2>MAGI 各席の処理状況</h2>
      <span class="muted small">保存済みの最新Turn（リアルタイム通信表示ではありません）</span>
    </div>
    <p v-if="turn" class="muted small">
      {{ turn.stage || 'MAGI判断' }} / {{ turn.status || '結果記録なし' }}
    </p>
    <p v-else class="muted small">
      まだ席別のTurn結果は保存されていません。処理中でもこの表示は空の場合があります。
    </p>
    <div v-if="members.length" class="magi-members">
      <div v-for="member in members" :key="member.name" class="magi-member">
        <strong>{{ member.name }}</strong>
        <p class="muted small">{{ member.provider || 'Provider不明' }} / {{ member.model || 'Model不明' }}</p>
        <p>{{ member.status || '結果未記録' }}</p>
        <p v-if="member.elapsed != null" class="muted small">{{ member.elapsed }}秒</p>
      </div>
    </div>
    <p v-else class="muted small">MAGIメンバー設定は、この保存済みTaskに記録されていません。</p>
  </section>
</template>
