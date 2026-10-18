<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/**
 * RITSUKO Taskの実行中Operationと、DBの保存済みMAGI最終Turnを別に表示する。
 * 進行中の席状態は同一APIプロセスの一時情報であり、確定DB状態ではない。
 */
import { computed } from 'vue'
import type { TaskOperation } from '../api'
import type { TaskRecord } from '../types'

const props = defineProps<{
  task: TaskRecord | null
  operation: TaskOperation | null
}>()
const turns = computed(() => props.task?.magi_session?.turns ?? [])
const turn = computed(() => turns.value.length ? turns.value[turns.value.length - 1] : null)
/** 内部next/statusと利用者向けの進み方を混同しない。Task確定は別領域。 */
const steps = computed(() => turns.value.map((item, i) => {
  const next = item.response?.next
  const source = item.response?.read_request?.source
  const sourceName = source === 'pkb' ? '個人PKB' : source === 'web' ? 'Web検索'
    : source === 'finance' ? '家計' : '情報源'
  const label = next === 'request_read' ? sourceName + 'の読み取りを依頼'
    : next === 'respond' ? '根拠を評価して回答を提案'
      : next === 'ask_user' ? '本人へ追加確認を提案' : '判断候補を作成'
  return { number: i + 1, label, reason: item.response?.reason ?? '',
    refs: item.response?.evidence?.result_ids?.length ?? 0 }
}))
const liveMembers = computed(() => {
  const running = ['accepted', 'running'].includes(props.operation?.status ?? '')
  if (!running) return []
  return Object.values(props.operation?.member_progress ?? {})
})
const savedMembers = computed(() => {
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
  <section v-if="task?.magi_session || liveMembers.length" class="card"
    aria-label="MAGI各席の処理状況">
    <div class="section-head">
      <h2>MAGI 各席の処理状況</h2>
    </div>
    <template v-if="liveMembers.length">
      <p class="muted small">
        このAPIプロセスでの進捗（Turn {{ operation?.live_turn ?? '-' }}）。
        途中表示であり、Task完了の証拠ではありません。
      </p>
      <div class="magi-members" aria-label="MAGI席別の一時進捗">
        <div v-for="member in liveMembers" :key="member.member" class="magi-member">
          <strong>{{ member.member }}</strong>
          <p class="muted small">
            {{ member.provider || 'Provider不明' }} / {{ member.model || 'Model不明' }}
          </p>
          <p>{{ member.state }}</p>
          <p v-if="member.elapsed_seconds != null" class="muted small">
            {{ member.elapsed_seconds }}秒
          </p>
        </div>
      </div>
    </template>
    <div v-if="steps.length" class="magi-timeline" aria-label="MAGI判断の経過">
      <div v-for="step in steps" :key="step.number" class="magi-step">
        <strong>Turn {{ step.number }}：{{ step.label }}</strong>
        <span v-if="step.refs" class="muted small">保存済みResult {{ step.refs }}件を参照</span>
        <p v-if="step.reason" class="muted small">{{ step.reason }}</p>
      </div>
      <p class="muted small">MAGIは判断候補を提案します。Taskの完了・停止はRITSUKOの保存状態を確認してください。</p>
    </div>
    <p class="muted small">
      保存済みの最新Turn（リアルタイム通信表示ではありません）
    </p>
    <p v-if="turn" class="muted small">
      {{ turn.stage || 'MAGI判断' }} / {{ turn.status || '結果記録なし' }}
    </p>
    <p v-else class="muted small">
      まだ席別のTurn結果は保存されていません。処理中でもこの表示は空の場合があります。
    </p>
    <div v-if="savedMembers.length" class="magi-members">
      <div v-for="member in savedMembers" :key="member.name" class="magi-member">
        <strong>{{ member.name }}</strong>
        <p class="muted small">
          {{ member.provider || 'Provider不明' }} / {{ member.model || 'Model不明' }}
        </p>
        <p>{{ member.status || '結果未記録' }}</p>
        <p v-if="member.elapsed != null" class="muted small">{{ member.elapsed }}秒</p>
      </div>
    </div>
    <p v-else class="muted small">
      MAGIメンバー設定は、この保存済みTaskに記録されていません。
    </p>
  </section>
</template>

<style scoped>
.magi-timeline { display: flex; flex-direction: column; gap: 8px; margin: 8px 0 12px; }
.magi-step { border-left: 3px solid #9dcac2; padding: 7px 12px; background: #f5faf9;
  border-radius: 5px; display: flex; flex-direction: column; gap: 2px; }
.magi-step p { margin: 0; overflow-wrap: anywhere; }
</style>
