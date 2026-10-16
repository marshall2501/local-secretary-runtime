// Provenance-Origin: original
// Provenance-AI: AI-generated
/**
 * 保存済みTaskを表示するための画面側の選択・再取得処理。
 * Taskの完了判断と保存はRITSUKOが行う。MAGIの候補状態は別に表示する。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { getTaskTrace, listTasks } from './api'
import type { TaskListView, TaskSummary, TaskTrace } from './types'

/** URLハッシュから選択TaskのIDを読み取る。見つからなければ初回一覧で選ぶ。 */
function taskIdFromHash(): string {
  return window.location.hash.match(/^#task=([a-f\d-]{36})$/i)?.[1] ?? ''
}

/**
 * 一覧と選択TaskのTrace、接続失敗、フィルタの状態をまとめる。
 * DB由来のTask確定状態とMAGI候補を区別して保持し、Vueでは確定判断しない。
 */
export function useTasks() {
  const items = ref<TaskSummary[]>([])
  const view = ref<TaskListView>('recent')
  const selectedId = ref(taskIdFromHash())
  const trace = ref<TaskTrace | null>(null)
  const connected = ref(false)
  const refreshing = ref(false)
  const problem = ref('')
  const selected = computed(() => trace.value?.task ?? null)
  // 同時に走った照会のうち最新の呼び出しだけを採用するための世代番号。
  let version = 0
  let timer: ReturnType<typeof setInterval> | undefined

  /**
   * 現在の表示条件で一覧を取得し、選択IDのTraceを続けて取得する。
   * 遅れて返る旧条件の結果は破棄する。失敗時は取得済み一覧を残すが、
   * Taskを選び直した直後の詳細は未取得のままになることがある。
   */
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
      problem.value = 'Task情報を取得できません。取得済みの一覧は保持しています。' +
        '選択中の詳細は未取得の場合があります。（' +
        String(error) + '）'
    } finally {
      if (seq === version) refreshing.value = false
    }
  }

  /** Task一覧で選択したIDを画面とURLへ反映し、履歴を取り直す。 */
  function selectTask(id: string): void {
    if (selectedId.value === id) return
    selectedId.value = id
    trace.value = null
    window.location.hash = 'task=' + id
    void refresh()
  }

  /** 最近・進行中・完了の表示条件を切り替え、前の選択を解除する。 */
  function selectView(next: TaskListView): void {
    if (view.value === next) return
    view.value = next
    selectedId.value = ''
    trace.value = null
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
    void refresh()
  }

  /** ブラウザ履歴からTask IDが変わったときに選択内容を更新する。 */
  function onHashChange(): void {
    const id = taskIdFromHash()
    if (id !== selectedId.value) {
      selectedId.value = id
      trace.value = null
      void refresh()
    }
  }

  // 他の画面からTaskが進む可能性があるため、表示中は3秒ごとに再照会する。
  // 画面を閉じる際はTimerとイベント購読だけを解除する。Task停止要求は送らない。
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
