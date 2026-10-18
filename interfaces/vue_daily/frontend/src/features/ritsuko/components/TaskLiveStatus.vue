<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/**
 * 実Taskの動的な進行状況を、保存済みTaskの確定状態と分けて案内する。
 * PollingのOperationは一時情報であり、完了判断・実行権限を画面で作らない。
 */
import { computed } from 'vue'
import type { TaskOperation, MemberOperationProgress } from '../api'
import type { TaskRecord } from '../types'

const props = defineProps<{
  task: TaskRecord | null
  taskId: string
  operation: TaskOperation | null
  connected: boolean
  commandsEnabled: boolean
  busy: boolean
}>()
const emit = defineEmits<{ stop: [] }>()

const currentOperation = computed(() =>
  props.operation?.task_id === (props.task?.id || props.taskId) ? props.operation : null)
const active = computed(() =>
  ['accepted', 'running'].includes(currentOperation.value?.status ?? ''))
const pending = computed(() => ['pending', 'running'].includes(props.task?.status ?? ''))
const waitingForWorker = computed(() => pending.value && !active.value)
const show = computed(() => {
  if (['completed', 'failed', 'cancelled', 'stopped'].includes(props.task?.status ?? '')) return false
  return active.value || pending.value || currentOperation.value?.execution_reconciliation_required === true
})
const heading = computed(() => {
  if (!props.connected) return '実行状況を確認できません'
  if (currentOperation.value?.execution_reconciliation_required) return 'Workerの稼働状況を確認中'
  if (currentOperation.value?.status === 'accepted') return '依頼を受付・実行準備中'
  if (currentOperation.value?.status === 'running') return 'RITSUKOが処理中'
  if (currentOperation.value?.status === 'execution_failed') return '処理中に問題が発生'
  if (currentOperation.value?.status === 'interrupted') return '処理が中断されました'
  return '保存済みTaskの状態を照合中'
})
const explanation = computed(() => {
  if (!props.connected) return 'API接続がありません。以前の処理状況を現在の稼働とみなしません。'
  if (currentOperation.value?.execution_reconciliation_required) {
    return 'DB上のTaskは処理中ですが、このAPIプロセスにはWorkerの記録がありません。自動再実行は行いません。'
  }
  if (currentOperation.value?.status === 'accepted') return '受付は完了しました。MAGIの開始を待っています。'
  if (currentOperation.value?.status === 'running') return 'MAGIの回答や情報取得を待っています。結果はDBの確定後に表示します。'
  if (currentOperation.value?.status === 'execution_failed') return 'Worker側の実行が失敗しました。Taskの確定状態とは分けて確認してください。'
  if (currentOperation.value?.status === 'interrupted') return '実行が中断されました。Taskの確定結果を再取得して確認してください。'
  if (currentOperation.value?.status === 'execution_finished') return '実行処理は戻りましたが、Taskの完了はDBの記録で確認します。'
  return 'DBでは処理中です。実行中のWorkerを確認できるまで完了とは表示しません。'
})
type Seat = MemberOperationProgress & { confirmed: boolean }
const members = computed<Seat[]>(() => {
  if (!active.value) return []
  const byName = currentOperation.value?.member_progress ?? {}
  const configured = props.task?.magi_session?.member_specs?.filter(spec => spec.enabled) ?? []
  if (configured.length) {
    return configured.map(spec => {
      const live = Object.values(byName).find(item => item.member === spec.name)
      return {
        member: spec.name ?? 'MAGI',
        provider: live?.provider || spec.provider || '',
        model: live?.model || spec.model || '',
        state: live?.state ?? 'queued',
        turn: live?.turn ?? 0,
        elapsed_seconds: live?.elapsed_seconds,
        confirmed: live !== undefined,
      }
    })
  }
  return Object.values(byName).map(item => ({ ...item, confirmed: true }))
})
function seatLabel(seat: Seat): string {
  if (!seat.confirmed) return '開始待ち（席別記録待ち）'
  const labels: Record<string, string> = {
    queued: '待機中', running: '実行中', completed: '応答完了',
    finished: '応答完了', withheld: '送信対象外', invalid: '応答形式を確認',
    unavailable: '利用不可', failed: '応答失敗', timeout: 'タイムアウト',
  }
  return labels[seat.state] ?? '状態：' + seat.state
}
const stoppable = computed(() =>
  props.commandsEnabled && props.connected && !props.busy &&
  pending.value && props.task?.stop_requested !== true &&
  currentOperation.value?.stop_requested !== true)
</script>

<template>
  <section v-if="show" class="card live-status" aria-label="Taskの進行状況">
    <div class="section-head">
      <div class="live-heading">
        <span class="live-dot" :class="{ 'is-running': active && connected }" aria-hidden="true"></span>
        <h2 role="status" aria-live="polite">{{ heading }}</h2>
      </div>
      <span v-if="active && operation?.live_turn" class="live-turn">
        MAGI Turn {{ operation.live_turn }}
      </span>
    </div>
    <p class="live-explanation">{{ explanation }}</p>
    <p v-if="currentOperation?.execution_reconciliation_required"
      class="worker-warning" role="status" aria-label="Worker照合待ち">
      Workerの稼働状況を確認できません。DBの処理中表示だけでは実行中と断定できません。
    </p>
    <p v-if="task?.stop_requested || currentOperation?.stop_requested" class="muted small">
      停止を要求済みです。実際に停止したかどうかは保存済みTaskを確認してください。
    </p>
    <div v-if="active && members.length" class="magi-members" aria-label="MAGI席別の一時進捗">
      <div v-for="seat in members" :key="seat.member" class="magi-member">
        <strong>{{ seat.member }}</strong>
        <p class="seat-state" :class="'seat-' + seat.state">{{ seatLabel(seat) }}</p>
        <p class="muted small">{{ seat.provider || 'Provider未確認' }} /
          {{ seat.model || 'Model未確認' }}</p>
        <p v-if="seat.elapsed_seconds != null" class="muted small">
          API報告の経過時間：{{ seat.elapsed_seconds }}秒
        </p>
      </div>
    </div>
    <p v-else-if="active" class="muted small">
      MAGI各席の情報を待っています。進捗は約3秒ごとに再取得します。
    </p>
    <div class="live-actions">
      <button v-if="stoppable" type="button" class="secondary"
        @click="emit('stop')">停止を要求</button>
      <span class="muted small">{{ active ? '進捗はPollingで更新します。'
        : waitingForWorker ? '保存済み状態は自動で再照会します。' : '' }}</span>
    </div>
  </section>
</template>

<style scoped>
.live-status { border-color: #95baca; background: #f4fafc; padding: 14px 18px; }
.live-heading { display: flex; align-items: center; gap: 9px; }
.live-heading h2 { font-size: 16px; }
.live-dot { width: 9px; height: 9px; border-radius: 50%; background: #8c9ca9; flex: 0 0 9px; }
.live-dot.is-running { background: #187cbb; }
.live-turn { font-size: 13px; font-weight: 650; color: #286a91; }
.live-explanation { margin: 2px 0 8px; font-size: 13px; }
.worker-warning { margin: 8px 0; padding: 8px 10px; border-left: 3px solid #b08240;
  background: #fff6e8; font-size: 13px; }
.live-actions { display: flex; align-items: center; gap: 12px; margin-top: 9px; flex-wrap: wrap; }
.seat-state { font-weight: 650; margin-top: 5px; }
.seat-running { color: #146995; }
.seat-completed, .seat-finished { color: #19734d; }
.seat-failed, .seat-unavailable, .seat-timeout { color: #9e323e; }
@media(max-width:680px) { .live-status { padding: 12px; } }
</style>
