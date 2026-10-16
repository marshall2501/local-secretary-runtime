<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/**
 * NiceGUIの配置を踏襲した右側Task一覧。取得済みの最大20件を表示する。
 * 進行中・完了の分類は表示用で、Taskの実行可否を決定するものではない。
 */
import { computed } from 'vue'
import { isTaskClosed, taskStatusLabel } from '../types'
import { legacyPageUrl } from '../../../shared/navigation'
import type { TaskListView, TaskSummary } from '../types'

const props = defineProps<{
  items: TaskSummary[]
  view: TaskListView
  selectedId: string
  commandsEnabled: boolean
  hasMore: boolean
  loading: boolean
}>()
const emit = defineEmits<{
  select: [id: string]
  filter: [view: TaskListView]
  more: []
}>()
const pending = computed(() => props.items.filter(task => !isTaskClosed(task.status)))
const closed = computed(() => props.items.filter(task => isTaskClosed(task.status)))
</script>

<template>
  <aside class="task-sidebar card" aria-label="保存済みTask一覧">
    <div class="section-head">
      <h2>既存Task</h2>
      <span class="muted small">{{ items.length }}件表示</span>
    </div>
    <div class="task-filters" aria-label="Task表示範囲">
      <button v-for="item in (['recent','open','completed'] as const)"
        :key="item" class="secondary filter-button" type="button"
        :aria-pressed="view === item" @click="emit('filter', item)">
        {{ item === 'recent' ? '最近' : item === 'open' ? '進行中' : '完了' }}
      </button>
    </div>
    <section v-if="pending.length" class="task-group">
      <h3>進行中・確認待ち</h3>
      <button v-for="task in pending" :key="task.id" type="button"
        class="task-item" :class="{ active: selectedId === task.id }"
        :aria-current="selectedId === task.id ? 'true' : undefined"
        @click="emit('select', task.id)">
        <span class="item-title">{{ task.request }}</span>
        <span class="small">{{ taskStatusLabel(task.status) }} / rev={{ task.revision }}</span>
      </button>
    </section>
    <section v-if="closed.length" class="task-group">
      <h3>完了・終了済み</h3>
      <button v-for="task in closed" :key="task.id" type="button"
        class="task-item" :class="{ active: selectedId === task.id }"
        :aria-current="selectedId === task.id ? 'true' : undefined"
        @click="emit('select', task.id)">
        <span class="item-title">{{ task.request }}</span>
        <span class="small">{{ taskStatusLabel(task.status) }} / rev={{ task.revision }}</span>
      </button>
    </section>
    <p v-if="!items.length" class="muted small">該当する保存済みTaskはありません。</p>
    <div class="task-list-more">
      <button v-if="hasMore" type="button" class="secondary" :disabled="loading"
        @click="emit('more')">さらに読み込む</button>
      <a :href="legacyPageUrl('/core/history')" class="small">Task履歴（既存画面）</a>
    </div>
    <p class="muted small task-sidebar-foot">{{ commandsEnabled ? "Taskの確定状態はDBから再照会します。" : "保存済みTaskの参照専用です。" }}</p>
  </aside>
</template>
