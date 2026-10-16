// Provenance-Origin: original
// Provenance-AI: AI-generated
/** Taskの画面状態と再照会だけを管理。完了判定・永続化はRITSUKOが所有する。 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { getTaskTrace, listTasks } from './api'
import type { TaskListView, TaskSummary, TaskTrace } from './types'

/** 既存URL #task=<UUID> のF5復元を維持する。 */
function taskIdFromHash(): string {
  return window.location.hash.match(/^#task=([a-f\d-]{36})$/i)?.[1] ?? ''
}

export function useTasks() {
  const items = ref<TaskSummary[]>([])
  const view = ref<TaskListView>('recent')
  const selectedId = ref(taskIdFromHash())
  const trace = ref<TaskTrace | null>(null)
  const connected = ref(false)
  const refreshing = ref(false)
  const problem = ref('')
  const selected = computed(() => trace.value?.task ?? null)
  let version = 0
  let timer: ReturnType<typeof setInterval> | undefined

  async function refresh(): Promise<void> {
    const seq = ++version
    const filter = view.value
    refreshing.value = true
    try {
      const list = await listTasks(filter)
      if (seq !== version) return
      const id = selectedId.value || list[0]?.id || ''
      const nextTrace = id ? await getTaskTrace(id) : null
      if (seq !== version || (selectedId.value && selectedId.value !== id)) return
      items.value = list
      if (!selectedId.value && id) {
        selectedId.value = id
        window.history.replaceState(null, '', '#task=' + id)
      }
      trace.value = nextTrace
      connected.value = true
      problem.value = ''
    } catch (error) {
      if (seq !== version) return
      connected.value = false
      problem.value = 'Task APIに接続できません。表示は最後に取得した内容です。（' +
        String(error) + '）'
    } finally {
      if (seq === version) refreshing.value = false
    }
  }

  function selectTask(id: string): void {
    if (selectedId.value === id) return
    selectedId.value = id
    trace.value = null
    window.location.hash = 'task=' + id
    void refresh()
  }

  function selectView(next: TaskListView): void {
    if (view.value === next) return
    view.value = next
    selectedId.value = ''
    trace.value = null
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
    void refresh()
  }

  function onHashChange(): void {
    const id = taskIdFromHash()
    if (id !== selectedId.value) {
      selectedId.value = id
      trace.value = null
      void refresh()
    }
  }

  onMounted(() => {
    window.addEventListener('hashchange', onHashChange)
    void refresh()
    timer = setInterval(() => { void refresh() }, 3000)
  })
  onUnmounted(() => {
    ++version
    window.removeEventListener('hashchange', onHashChange)
    if (timer !== undefined) clearInterval(timer)
  })
  return {
    items, view, selectedId, trace, selected, connected, refreshing,
    problem, refresh, selectTask, selectView,
  }
}
