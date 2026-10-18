// Provenance-Origin: original
// Provenance-AI: AI-generated
/**
 * 保存済みTaskを表示するための画面側の選択・再取得処理。
 * Taskの完了判断と保存はRITSUKOが行う。MAGIの候補状態は別に表示する。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { readLayoutPreferences } from '../../shared/layoutPreferences'
import type { LayoutPreferences } from '../../shared/layoutPreferences'
import {
  answerTask, getJudgmentPreferences, getTaskApiMode, getTaskOperation,
  getTaskTrace, listTasks, reviewTask, resumeTask, saveJudgmentPreferences, startTask, stopTask,
} from './api'
import type { DecisionStrategy, ReviewDecision, TaskOperation } from './api'
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
  // NiceGUIの追加読み込みと同様に、右一覧で見る件数は本人が増やす。
  const visibleLimit = ref(5)
  const layout = ref<LayoutPreferences | null>(null)
  function defaultListLimit(next: TaskListView): number {
    const count = layout.value?.core[next === 'completed' ? 'completed_limit' : 'open_limit']
    return typeof count === 'number' && count >= 1 && count <= 10 ? count : 5
  }
  const hasMore = ref(false)
  const selectedId = ref(taskIdFromHash())
  const trace = ref<TaskTrace | null>(null)
  const connected = ref(false)
  const refreshing = ref(false)
  const problem = ref('')
  const selected = computed(() => trace.value?.task ?? null)
  const commandsEnabled = ref(false)
  const commandBusy = ref(false)
  const commandFeedback = ref('')
  const commandError = ref('')
  const operation = ref<TaskOperation | null>(null)
  /** 受付中のOperationはDB Taskが確認待ちでも裏でMAGIを実行し得る。 */
  function hasActiveOperation(id: string): boolean {
    return operation.value?.task_id === id &&
      ['accepted', 'running'].includes(operation.value.status)
  }
  const pendingKey = 'ritsuko.pending-start.v1'

  type PendingStart = { id: string; request: string; strategy: DecisionStrategy }
  /** 通信途絶時の同一UUID再送。タブごとのsessionStorageのみ使用する。 */
  function loadPending(): PendingStart | null {
    try {
      const raw = window.sessionStorage.getItem(pendingKey)
      if (!raw) return null
      const item = JSON.parse(raw) as PendingStart
      return /^[a-f0-9-]{36}$/i.test(item.id) &&
        item.request.length > 0 && item.request.length <= 4000 &&
        ['standard', 'llm'].includes(item.strategy) ? item : null
    } catch {
      return null
    }
  }
  let pendingStart: PendingStart | null = loadPending()
  const draftRequest = ref(pendingStart?.request ?? '')
  const strategy = ref<DecisionStrategy>(pendingStart?.strategy ?? 'standard')
  // 編集中の設定と保存済みの実行設定を分け、保存前のUI操作をTaskへ反映しない。
  const defaultStrategy = ref<DecisionStrategy>('standard')
  const showStrategySelector = ref(true)
  const activeDefaultStrategy = ref<DecisionStrategy>('standard')
  const activeShowStrategySelector = ref(true)
  const preferencesLoaded = ref(false)
  const preferencesBusy = ref(false)
  const preferencesMessage = ref('')
  const reply = ref('')

  /** 通常操作時は既存NiceGUIの永続設定を正本とし、方式をTask開始時に固定する。 */
  async function loadPreferences(): Promise<void> {
    try {
      const saved = await getJudgmentPreferences()
      defaultStrategy.value = saved.default_strategy
      showStrategySelector.value = saved.show_strategy_selector
      activeDefaultStrategy.value = saved.default_strategy
      activeShowStrategySelector.value = saved.show_strategy_selector
      if (!pendingStart) strategy.value = saved.default_strategy
      preferencesLoaded.value = true
      preferencesMessage.value = ''
    } catch (error) {
      preferencesLoaded.value = false
      preferencesMessage.value = '保存済み判断設定を取得できません。（' + String(error) + '）'
    }
  }

  /** 判断方式の既定値と選択欄表示は別設定。MAGI席数には触れない。 */
  async function savePreferences(): Promise<void> {
    if (!commandsEnabled.value || preferencesBusy.value) return
    preferencesBusy.value = true
    preferencesMessage.value = ''
    try {
      const saved = await saveJudgmentPreferences({
        default_strategy: defaultStrategy.value,
        show_strategy_selector: showStrategySelector.value,
      })
      defaultStrategy.value = saved.default_strategy
      showStrategySelector.value = saved.show_strategy_selector
      activeDefaultStrategy.value = saved.default_strategy
      activeShowStrategySelector.value = saved.show_strategy_selector
      if (!pendingStart) strategy.value = saved.default_strategy
      preferencesLoaded.value = true
      preferencesMessage.value = '保存しました。これから開始するTaskに適用します。'
    } catch (error) {
      preferencesMessage.value = '設定を保存できません。（' + String(error) + '）'
    } finally {
      preferencesBusy.value = false
    }
  }
  function storePending(value: PendingStart | null): void {
    pendingStart = value
    try {
      if (value) window.sessionStorage.setItem(pendingKey, JSON.stringify(value))
      else window.sessionStorage.removeItem(pendingKey)
    } catch {
      // Storage禁止時も同一画面内ではUUIDを保持する。
    }
  }

  /**
   * 起動時だけではなく再照会時にも操作可能モードを更新する。
   * 8099が再起動／8097読取専用へ入れ替わった場合、古い許可表示を保持しない。
   */
  async function detectCommands(): Promise<boolean> {
    try {
      return await getTaskApiMode() === 'commands_enabled'
    } catch {
      return false
    }
  }

  /** Task APIの1回50件という上限を守りながら、追加読み込み分を再取得する。 */
  async function loadTaskWindow(filter: TaskListView, limit: number): Promise<TaskSummary[]> {
    const records: TaskSummary[] = []
    for (let offset = 0; offset < limit; offset += 50) {
      const requested = Math.min(50, limit - offset)
      const page = await listTasks(filter, requested, offset)
      records.push(...page)
      if (page.length < requested) break
    }
    return records
  }

  /** HTTP 202は受付。Task.status の完了とは区別してメッセージを出す。 */
  async function sendCommand(
    command: () => Promise<TaskOperation>, message: string,
  ): Promise<TaskOperation | null> {
    if (!commandsEnabled.value || commandBusy.value) return null
    commandBusy.value = true
    commandError.value = ''
    commandFeedback.value = ''
    try {
      const result = await command()
      commandFeedback.value = message
      return result
    } catch (error) {
      commandError.value = '操作を受付できませんでした。（' + String(error) + '）'
      return null
    } finally {
      commandBusy.value = false
    }
  }

  /** 自然言語受付。曖昧なHTTP失敗時は同一UUIDを保持して再送する。 */
  async function sendStart(): Promise<void> {
    const request = draftRequest.value.trim()
    if (!request || request.length > 4000) {
      commandError.value = '依頼は1〜4000文字で入力してください。'
      return
    }
    if (!preferencesLoaded.value) {
      commandError.value = '判断方式の保存済み設定を取得できていません。'
      return
    }
    // 書込結果不明の再送は、設定が変わっても同じTask IDと方式を維持する。
    const chosen = activeShowStrategySelector.value ? strategy.value : activeDefaultStrategy.value
    const intent = pendingStart && pendingStart.request === request ? pendingStart : {
      id: crypto.randomUUID(), request, strategy: chosen,
    }
    storePending(intent)
    const result = await sendCommand(
      () => startTask(intent.request, intent.strategy, intent.id),
      'Taskを受付けました。結果の確定は保存済みTaskを再照会して確認します。',
    )
    if (result === null) return
    storePending(null)
    draftRequest.value = ''
    selectedId.value = result.task_id
    view.value = 'recent'
    reply.value = ''
    operation.value = result
    trace.value = null
    window.history.replaceState(null, '', '#task=' + result.task_id)
    await refresh()
  }

  async function sendAnswer(): Promise<void> {
    const task = selected.value
    const text = reply.value.trim()
    if (!task || task.status !== 'waiting_external' ||
        task.phase !== 'awaiting_clarification' || !text) return
    const result = await sendCommand(
      () => answerTask(task.id, text),
      '本人回答を受付けました。Taskの確定状態を再照会します。',
    )
    if (result) {
      operation.value = result
      reply.value = ''
      await refresh()
    }
  }

  async function sendReview(decision: ReviewDecision = 'answer_only'): Promise<void> {
    const task = selected.value
    // RITSUKO's persisted Task phase is authoritative. Do not independently
    // reconstruct review eligibility from MAGI's provisional session shape.
    if (!task || task.status !== 'waiting_external' ||
        task.phase !== 'awaiting_review' ||
        (decision === 'remember'
          ? (!task.can_remember || !task.memory_review_candidate)
          : !task.can_answer_only)) return
    const result = await sendCommand(
      () => reviewTask(task.id, decision),
      decision === 'remember'
        ? '記憶反映Reviewを受付けました。Memory IntakeとTask確定の結果を再照会します。'
        : '回答のみのReviewを受付けました。完了の確定ではありません。',
    )
    if (result) {
      operation.value = result
      await refresh()
    }
  }

  async function sendRemember(): Promise<void> {
    await sendReview('remember')
  }

  /** 再開可否はCoreの保存済み判定に従い、同一Task IDへ受付を送る。 */
  async function sendResume(): Promise<void> {
    const task = selected.value
    if (!task || !task.can_resume || task.status !== 'paused') return
    const result = await sendCommand(
      () => resumeTask(task.id),
      '同じTask IDで再開を受付けました。DB上の処理状況を再照会します。',
    )
    if (result) {
      operation.value = result
      await refresh()
    }
  }

  async function sendStop(): Promise<void> {
    const task = selected.value
    if (!task || !['pending', 'running'].includes(task.status)) return
    const result = await sendCommand(
      () => stopTask(task.id),
      '停止要求を記録しました。Workerの停止完了とは別です。',
    )
    if (result) {
      operation.value = result
      await refresh()
    }
  }
  // 同時に走った照会のうち最新の呼び出しだけを採用するための世代番号。
  let version = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  let queuedRefresh = false
  let queuedForceTrace = false
  let mounted = false

  /**
   * 処理中だけ頻度を上げ、待機中はDB照会とログを減らす。
   * setTimeoutで前回応答完了後に次を予約し、重複pollingを防ぐ。
   */
  function schedulePoll(): void {
    if (timer !== undefined) clearTimeout(timer)
    timer = undefined
    if (!mounted || document.visibilityState === 'hidden') return
    // A persisted "running" status does not establish that this API process
    // still owns a Worker. After a restart, avoid a 3-second DB polling loop
    // unless a live in-process Operation is confirmed.
    const workerUnknown = operation.value?.task_id === selectedId.value &&
      operation.value.status === 'operation_not_in_process' &&
      operation.value.execution_reconciliation_required === true
    const running = connected.value && (commandBusy.value ||
      hasActiveOperation(selectedId.value) ||
      (['pending', 'running'].includes(selected.value?.status ?? '') && !workerUnknown))
    timer = setTimeout(() => { void refresh() }, running ? 3000 : 45000)
  }

  function onVisibilityChange(): void {
    if (document.visibilityState === 'visible') void refresh()
    else if (timer !== undefined) {
      clearTimeout(timer)
      timer = undefined
    }
  }

  /**
   * 現在の表示条件で一覧を取得し、選択IDのTraceを続けて取得する。
   * 遅れて返る旧条件の結果は破棄する。失敗時は取得済み一覧を残すが、
   * Taskを選び直した直後の詳細は未取得のままになることがある。
   */
  async function refresh(forceTrace = false): Promise<void> {
    if (refreshing.value) {
      queuedRefresh = true
      queuedForceTrace = queuedForceTrace || forceTrace
      ++version
      return
    }
    const seq = ++version
    const filter = view.value
    refreshing.value = true
    try {
      const [enabled, list] = await Promise.all([
        detectCommands(), loadTaskWindow(filter, visibleLimit.value + 1),
      ])
      if (seq !== version) return
      commandsEnabled.value = enabled
      const id = selectedId.value || list[0]?.id || ''
      const summary = list.find(item => item.id === id)
      const unchanged = !forceTrace && Boolean(summary && trace.value?.task.id === id &&
        trace.value?.task.revision === summary.revision)
      const nextTrace = id ? (unchanged ? trace.value : await getTaskTrace(id)) : null
      const activeTask = ['pending', 'running'].includes(nextTrace?.task.status ?? '')
      // While Answer/Review runs, the last persisted Task phase may still be
      // awaiting input. Poll the in-process operation until it ends, then idle.
      // When selecting a waiting Task, check once to find an existing worker.
      const newlySelected = trace.value?.task.id !== id
      const checkOperation = activeTask || hasActiveOperation(id) ||
        (newlySelected && nextTrace?.task.status === 'waiting_external')
      const nextOperation = id && enabled && checkOperation
        ? await getTaskOperation(id).catch(() => null) : null
      if (seq !== version || (selectedId.value && selectedId.value !== id)) return
      hasMore.value = list.length > visibleLimit.value
      items.value = list.slice(0, visibleLimit.value)
      if (!selectedId.value && id) {
        selectedId.value = id
        window.history.replaceState(null, '', '#task=' + id)
      }
      trace.value = nextTrace
      operation.value = nextOperation
      connected.value = true
      problem.value = ''
    } catch (error) {
      if (seq !== version) return
      // API断で直前のcommands_enabledを残すと、送信可能だと誤認させる。
      commandsEnabled.value = false
      connected.value = false
      problem.value = 'Task情報を取得できません。取得済みの一覧は保持しています。' +
        '選択中の詳細は未取得の場合があります。（' +
        String(error) + '）'
    } finally {
      refreshing.value = false
      if (queuedRefresh && mounted) {
        queuedRefresh = false
        const fullReload = queuedForceTrace
        queuedForceTrace = false
        void refresh(fullReload)
      } else {
        schedulePoll()
      }
    }
  }

  /** Task一覧で選択したIDを画面とURLへ反映し、履歴を取り直す。 */
  function selectTask(id: string): void {
    if (selectedId.value === id) return
    selectedId.value = id
    reply.value = ''
    operation.value = null
    commandFeedback.value = ''
    commandError.value = ''
    trace.value = null
    window.location.hash = 'task=' + id
    void refresh()
  }

  /** 右側Task一覧に未表示の過去Taskを取得する。DB上限を超える場合もOffsetで分割する。 */
  function loadMore(): void {
    if (refreshing.value || !hasMore.value) return
    visibleLimit.value += 10
    void refresh()
  }

  /** 最近・進行中・完了の表示条件を切り替え、前の選択を解除する。 */
  function selectView(next: TaskListView): void {
    if (view.value === next) return
    view.value = next
    visibleLimit.value = defaultListLimit(next)
    selectedId.value = ''
    reply.value = ''
    operation.value = null
    commandFeedback.value = ''
    commandError.value = ''
    trace.value = null
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
    void refresh()
  }

  /** ブラウザ履歴からTask IDが変わったときに選択内容を更新する。 */
  function onHashChange(): void {
    const id = taskIdFromHash()
    if (id !== selectedId.value) {
      selectedId.value = id
      reply.value = ''
      operation.value = null
      commandFeedback.value = ''
      commandError.value = ''
      trace.value = null
      void refresh()
    }
  }

  // 進行中は3秒、待機中は45秒。非表示タブでは定期照会しない。
  onMounted(() => {
    mounted = true
    window.addEventListener('hashchange', onHashChange)
    document.addEventListener('visibilitychange', onVisibilityChange)
    void readLayoutPreferences().then(saved => {
      layout.value = saved
      visibleLimit.value = defaultListLimit(view.value)
    }).catch(() => { /* UI still loads Task data with safe defaults. */ })
      .finally(() => { void refresh() })
    void loadPreferences()
  })
  onUnmounted(() => {
    mounted = false
    ++version
    window.removeEventListener('hashchange', onHashChange)
    document.removeEventListener('visibilitychange', onVisibilityChange)
    if (timer !== undefined) clearTimeout(timer)
  })
  return {
    layout, items, view, visibleLimit, hasMore, selectedId, trace, selected, connected, refreshing,
    problem, refresh, selectTask, selectView, loadMore,
    commandsEnabled, commandBusy, commandFeedback, commandError, operation,
    draftRequest, strategy, defaultStrategy, showStrategySelector,
    activeDefaultStrategy, activeShowStrategySelector,
    preferencesLoaded, preferencesBusy, preferencesMessage, savePreferences,
    reply, sendStart, sendAnswer, sendReview, sendRemember, sendStop, sendResume,
  }
}
