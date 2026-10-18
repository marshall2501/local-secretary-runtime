// Provenance-Origin: original
// Provenance-AI: AI-generated
/**
 * 単一Secretary ServerのVue＋APIを実Chromiumで隔離再現する。
 * すべてfake Taskであり、実MAGI・PKB・運用DB・外部Actionは使わない。
 */
import { expect, test } from '@playwright/test'

test('単一Secretary ServerでVueからそのままTaskを送信できる', async ({ page }) => {
  await page.goto('http://127.0.0.1:8097/ui/')
  await expect(page.getByRole('heading', { name: '新しい依頼を入力' })).toBeVisible()
  const field = page.getByRole('textbox', { name: '依頼内容' })
  await expect(field).toBeEnabled()
  await field.fill('本当に入力できる')
  await expect(field).toHaveValue('本当に入力できる')
  await expect(page.getByRole('button', { name: '依頼を送信' })).toBeEnabled()
  await expect(page.getByText('実Task操作API接続中')).toBeVisible()
  // No cross-port navigation or second server is needed.
  expect(page.url()).toContain('127.0.0.1:8097/ui/')
})

test('Vueの両判断方式は同じTask APIに保存され、方式はTask単位で固定される', async ({ page }) => {
  await page.goto('http://127.0.0.1:8097/ui/')
  const selector = page.locator('#task-strategy')
  await expect(selector).toHaveValue('standard')
  const strategies = [
    { value: 'llm', label: 'LLM判断の受付試験' },
    { value: 'standard', label: '従来判断の受付試験' },
  ]
  for (const item of strategies) {
    await selector.selectOption(item.value)
    await page.getByRole('textbox', { name: '依頼内容' }).fill(item.label)
    await page.getByRole('button', { name: '依頼を送信' }).click()
    await expect(page.locator('.current-task').getByText(item.label, { exact: true }))
      .toBeVisible()
    const taskId = page.url().split('#task=')[1]
    const response = await page.request.get(
      'http://127.0.0.1:8097/api/v1/ritsuko/tasks/' + taskId,
    )
    expect(response.status()).toBe(200)
    const persisted = await response.json()
    expect(persisted.trace.task.magi_session.decision_strategy).toBe(item.value)
  }
})

test('待機中のVueは3秒毎の多重GETを発行しない', async ({ page }) => {
  await page.goto('http://127.0.0.1:8097/ui/')
  await expect(page.getByText('実Task操作API接続中')).toBeVisible()
  // 初期の一覧・詳細・Healthが揃った後、ネットワーク実送信を観測する。
  await expect(page.locator('.task-item').first()).toBeVisible()
  const requests: string[] = []
  page.on('request', request => {
    if (request.url().includes('/api/v1/ritsuko/')) requests.push(request.url())
  })
  await page.waitForTimeout(7_000)
  expect(requests).toEqual([])
})


test('API再起動でWorker所在不明のrunning Taskを誤完了せず低頻度で再照会できる', async ({ page }) => {
  const origin = 'http://127.0.0.1:8097'
  const list = await (await page.request.get(origin + '/api/v1/ritsuko/tasks?view=recent&limit=10&offset=0')).json()
  const id = list.items[0].id as string
  // The persisted Task may say running even if the API process has no
  // in-memory Operation. Only real Repository status is authoritative.
  // Freeze a response before navigation: route.fetch() can be disposed when
  // the browser tears down a request during reload, causing a flaky test.
  const saved = await (
    await page.request.get(origin + '/api/v1/ritsuko/tasks/' + id)
  ).json()
  saved.trace.task.status = 'running'
  saved.trace.task.stop_requested = false
  await page.route('**/api/v1/ritsuko/tasks/' + id, async route => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify(saved),
    })
  })
  await page.route('**/api/v1/ritsuko/operations/' + id, async route => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        task_id: id, status: 'operation_not_in_process', kind: null,
        task_status: 'running', source: 'persisted_task',
        execution_reconciliation_required: true,
      }),
    })
  })
  await page.goto(origin + '/ui/#task=' + id)
  await expect(page.getByRole('status', { name: 'Worker照合待ち' })).toBeVisible()
  await expect(page.getByText('処理中（Task確定状態）')).toBeVisible()

  // A detached worker should not trigger another 3-second DB request wave.
  const apiCalls: string[] = []
  page.on('request', request => {
    if (request.url().includes('/api/v1/ritsuko/')) apiCalls.push(request.url())
  })
  await page.waitForTimeout(7_000)
  expect(apiCalls).toEqual([])

  // Manual retry is one ordinary read-only query; it never re-executes Task.
  await page.getByRole('button', { name: '保存済みTaskを再取得' }).click()
  await expect.poll(() => apiCalls.length).toBeGreaterThan(0)
  await expect(page.getByRole('status', { name: 'Worker照合待ち' })).toBeVisible()
})

test('停止済みTaskの再開ボタンはCore許可時のみ表示し同じIDへ送信する', async ({ page }) => {
  const origin = 'http://127.0.0.1:8097'
  const list = await (await page.request.get(
    origin + '/api/v1/ritsuko/tasks?view=recent&limit=10&offset=0',
  )).json()
  const id = list.items[0].id as string
  let canResume = true
  const resumePosts: string[] = []

  await page.route('**/api/v1/ritsuko/tasks/' + id, async route => {
    const response = await route.fetch()
    const body = await response.json()
    body.trace.task.status = 'paused'
    body.trace.task.phase = 'paused'
    body.trace.task.stop_requested = true
    body.trace.task.can_resume = canResume
    await route.fulfill({ response, json: body })
  })
  await page.route('**/api/v1/ritsuko/tasks/' + id + '/resume', async route => {
    resumePosts.push(route.request().url())
    await route.fulfill({
      status: 202, contentType: 'application/json',
      body: JSON.stringify({
        task_id: id, status: 'accepted', kind: 'start', stop_requested: false,
      }),
    })
  })
  await page.goto(origin + '/ui/#task=' + id)
  const button = page.getByRole('button', { name: '同じTaskを再開' })
  await expect(button).toBeEnabled()
  await button.click()
  await expect.poll(() => resumePosts.length).toBe(1)
  expect(resumePosts[0]).toContain('/tasks/' + id + '/resume')

  // The UI must trust Core's saved eligibility rather than the paused
  // status alone. Source-executed tasks are not blind-restarted.
  canResume = false
  await page.getByRole('button', { name: '保存済みTaskを再取得' }).click()
  await expect(button).toHaveCount(0)
  await expect(page.getByText('このTaskには安全に使える再開点がありません。', {
    exact: false,
  })).toBeVisible()
})

test('保存した既定方式で選択欄なしでも同じTask APIを利用する', async ({ page }) => {
  await page.goto('http://127.0.0.1:8097/')
  await expect(page).toHaveURL(/^http:\/\/127\.0\.0\.1:8097\/ui\//)
  const endpoint = 'http://127.0.0.1:8097/api/v1/ritsuko/preferences'
  const original = await (await page.request.get(endpoint)).json() as {
    default_strategy: 'standard' | 'llm'; show_strategy_selector: boolean
  }
  try {
    await page.getByText('判断方式の設定').click()
    await page.getByLabel('新規Taskの既定方式').selectOption('llm')
    await page.getByLabel('新規依頼に検証用の判断方式選択を表示').uncheck()
    await page.getByRole('button', { name: '設定を保存' }).click()
    await expect(page.getByText('保存しました。これから開始するTaskに適用します。')).toBeVisible()
    await expect(page.locator('#task-strategy')).toHaveCount(0)
    await expect(page.getByText('既定判断方式：Goal / LLM')).toBeVisible()

    const request = '既定LLM方式の受付を確認する'
    await page.getByRole('textbox', { name: '依頼内容' }).fill(request)
    await page.getByRole('button', { name: '依頼を送信' }).click()
    await expect(page.locator('.current-task').getByText(request, { exact: true })).toBeVisible()
    const taskId = page.url().split('#task=')[1]
    const saved = await page.request.get(
      'http://127.0.0.1:8097/api/v1/ritsuko/tasks/' + taskId,
    )
    expect((await saved.json()).trace.task.magi_session.decision_strategy).toBe('llm')
  } finally {
    const reset = await page.request.post(endpoint, { data: original })
    expect(reset.ok()).toBeTruthy()
  }
})

test('本人回答中はTaskが確認待ちでもMAGI実行進捗を表示する', async ({ page }) => {
  await page.goto('http://127.0.0.1:8097/ui/')
  const request = '進捗を確認する回答試験'
  await page.getByRole('textbox', { name: '依頼内容' }).fill(request)
  await page.getByRole('button', { name: '依頼を送信' }).click()
  await expect(page.locator('.current-task').getByText(request, { exact: true })).toBeVisible()
  const taskId = page.url().split('#task=')[1]
  // Fake API simulates an in-process Answer MAGI call while the persisted
  // Task has not yet changed from awaiting_clarification/awaiting_review.
  await page.route('**/api/v1/ritsuko/operations/' + taskId, async route => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        task_id: taskId, status: 'running', kind: 'answer', live_turn: 2,
        member_progress: {
          MELCHIOR: {
            member: 'MELCHIOR', provider: 'ollama', model: 'fixture',
            state: 'running', turn: 2,
          },
        },
      }),
    })
  })
  await page.getByRole('textbox', { name: '選択中のTaskへの本人回答' }).fill('Python')
  await page.getByRole('button', { name: '本人回答を送る' }).click()
  await expect(page.getByLabel('MAGI席別の一時進捗').getByText('running')).toBeVisible()
  await expect(page.getByText('本人への確認待ち（Task確定状態）')).toBeVisible()
})

test('8097新規Task→本人回答→Review→確定→F5同IDで復元する', async ({ page, context }) => {
  await page.goto('http://127.0.0.1:8097/ui/')
  const previousUrl = page.url()
  await page.getByRole('textbox', { name: '依頼内容' }).fill('Pythonの辞書を簡潔に説明してください')
  await page.getByRole('button', { name: '依頼を送信' }).click()
  // 初期画面でも旧Task IDのhashが付くため、新しい受付IDへ変わるまで待つ。
  await expect.poll(() => page.url()).not.toBe(previousUrl)
  await expect(page).toHaveURL(/#task=[a-f\d-]{36}$/i)
  // 初期選択の旧Taskハッシュと、新規受付したIDを取り違えないよう本文で照合。
  await expect(page.locator('.current-task').getByText(
    'Pythonの辞書を簡潔に説明してください', { exact: true },
  )).toBeVisible()
  const taskUrl = page.url()
  await expect(page.getByRole('textbox', { name: '依頼内容' })).toHaveValue('')
  await expect(page.getByText('本人への確認待ち（Task確定状態）')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'MAGI 各席の処理状況' })).toBeVisible()
  for (const member of ['MELCHIOR', 'CASPER', 'BALTHASAR']) {
    await expect(page.locator('.magi-member', { hasText: member })).toBeVisible()
  }
  await page.getByRole('textbox', { name: '選択中のTaskへの本人回答' }).fill('Python')
  await page.getByRole('button', { name: '本人回答を送る' }).click()
  await expect(page.getByRole('button', { name: '回答のみで完了を依頼' })).toBeVisible()
  await page.getByRole('button', { name: '回答のみで完了を依頼' }).click()
  await expect(page.getByText('完了（Task確定状態）')).toBeVisible()
  await expect(page.getByText('架空Taskの回答を確定しました。')).toBeVisible()
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], {
    origin: 'http://127.0.0.1:8097',
  })
  await page.getByRole('button', { name: '最新結果をコピー' }).click()
  await expect(page.getByText('最新結果をコピーしました。')).toBeVisible()
  await expect.poll(async () => page.evaluate(() => navigator.clipboard.readText()))
    .toBe('架空Taskの回答を確定しました。')
  await page.getByRole('button', { name: 'Task全情報をコピー' }).click()
  const copied = await page.evaluate(() => navigator.clipboard.readText())
  expect(JSON.parse(copied) as { task: { request: string } }).toMatchObject({
    task: { request: 'Pythonの辞書を簡潔に説明してください' },
  })

  await page.reload()
  await expect(page).toHaveURL(taskUrl)
  await expect(page.getByText('完了（Task確定状態）')).toBeVisible()
  await expect(page.getByText('架空Taskの回答を確定しました。')).toBeVisible()
})

test('本人の確定した知識だけ記憶Reviewを表示し同一Taskへ送信する', async ({ page }) => {
  await page.goto('http://127.0.0.1:8097/ui/')
  const request = '記憶反映試験：本人が回答したOSを登録する'
  await page.getByRole('textbox', { name: '依頼内容' }).fill(request)
  await page.getByRole('button', { name: '依頼を送信' }).click()
  await expect(page.locator('.current-task').getByText(request, { exact: true })).toBeVisible()
  await expect(page.getByText('本人への確認待ち（Task確定状態）')).toBeVisible()
  const preview = page.getByLabel('記憶反映前の登録候補')
  await expect(preview.getByText('本人が確認したOSはFedoraです。', { exact: true })).toBeVisible()
  await expect(preview.getByText(/この候補の根拠となった本人回答：Fedora/)).toBeVisible()
  const remember = page.getByRole('button', { name: '記憶に反映して完了を依頼' })
  await expect(remember).toBeEnabled()
  const taskId = page.url().split('#task=')[1]
  await remember.click()
  await expect(page.getByText('完了（Task確定状態）')).toBeVisible()
  await expect(page.getByText('架空Taskの記憶反映を確定しました。')).toBeVisible()
  expect(page.url()).toContain('#task=' + taskId)

  // Source-free/ordinary candidate has no recorded user knowledge to write.
  await page.getByRole('textbox', { name: '依頼内容' }).fill('回答候補試験：一般説明')
  await page.getByRole('button', { name: '依頼を送信' }).click()
  await expect(page.getByText('MAGI回答候補（未確定・本人確認待ち）')).toBeVisible()
  await expect(page.getByRole('button', { name: '記憶に反映して完了を依頼' })).toHaveCount(0)
})

test('従来MAGI READY回答は候補を表示し、本人Reviewで同じTaskを完了できる', async ({ page }) => {
  await page.goto('http://127.0.0.1:8097/ui/')
  await page.locator('#task-strategy').selectOption('standard')
  const request = '回答候補試験：Pythonの辞書を説明する'
  await page.getByRole('textbox', { name: '依頼内容' }).fill(request)
  await page.getByRole('button', { name: '依頼を送信' }).click()
  await expect(page.locator('.current-task').getByText(request, { exact: true })).toBeVisible()
  await expect(page.getByText('MAGI回答候補（未確定・本人確認待ち）')).toBeVisible()
  await expect(page.getByText('辞書はキーと値の組です。')).toBeVisible()
  await page.getByRole('button', { name: '回答のみで完了を依頼' }).click()
  await expect(page.getByText('完了（Task確定状態）')).toBeVisible()
  await expect(page.getByText('架空Taskの回答を確定しました。')).toBeVisible()
})

test('停止は保存済み状態で確認し、Actionの出典と監査履歴を開ける', async ({ page }) => {
  await page.goto('http://127.0.0.1:8097/ui/')
  await page.getByRole('textbox', { name: '依頼内容' }).fill('停止試験として処理を開始する')
  await page.getByRole('button', { name: '依頼を送信' }).click()
  await expect(page.getByText('受付（Task確定状態）')).toBeVisible()
  await expect(page.getByText(/このAPIプロセスでの進捗/)).toBeVisible()
  await expect(page.getByLabel('MAGI席別の一時進捗').getByText('running')).toBeVisible()
  await page.getByRole('button', { name: '停止を要求' }).click()
  await expect(page.getByLabel('MAGI席別の一時進捗')).toHaveCount(0)
  await expect(page.getByText('停止（Task確定状態）')).toBeVisible()
  await page.getByText('検証情報・実行経緯').click()
  await expect(page.getByText('参照元: https://example.org/reference')).toBeVisible()
  await expect(page.getByText('確認経路: fixture')).toBeVisible()
  await page.getByText('監査イベント（1件）').click()
  await expect(page.getByText(/synthetic_verified/)).toBeVisible()
})

test('旧Taskを10件からさらに読み込み、確定Goalを未確定と表示しない', async ({ page }) => {
  await page.goto('http://127.0.0.1:8097/ui/')
  await expect(page.getByText('10件表示')).toBeVisible()
  await expect(page.getByRole('button', { name: 'さらに読み込む' })).toBeVisible()
  const more = page.getByRole('button', { name: 'さらに読み込む' })
  await more.click()
  // 先行テストが作成するTask数によって総件数は変わる。複数回の追加
  // 読込後に最終的な終端へ到達する契約を検査し、件数を固定しない。
  await expect.poll(async () => page.locator('.task-item').count()).toBeGreaterThan(10)
  await expect(page.locator('.task-sidebar .section-head .small')).not.toHaveText('10件表示')
  // The purpose of this regression is finding a confirmed historical Goal
  // beyond the first ten entries, not exhausting all pages. Repeatedly
  // requesting unrelated pages introduces races with asynchronous refreshes.
  // Historical Task 12 is in the first additional page of this fixture.
  await expect(page.getByRole('button', { name: /架空の過去Task 12/ })).toBeVisible()
  // 先行の停止試験Taskが選択済みの場合もある。固定の旧Taskを明示的に選ぶ。
  await page.getByRole('button', { name: /架空の過去Task 12/ }).click()
  await expect(page.getByText('Goal確定済み / v2。Task完了とは別です。')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Task履歴（既存画面）' })).toHaveCount(0)
  await expect(page.getByRole('complementary', { name: '保存済みTask一覧' })).toBeVisible()
})


test('Vue PKB検索は共通ReadServiceを使い、登録・訂正にも同一画面から進める', async ({ page }) => {
  await page.goto('http://127.0.0.1:8097/ui/')
  await page.getByRole('link', { name: 'PKB' }).click()
  await expect(page).toHaveURL('http://127.0.0.1:8097/ui/?page=pkb')
  await expect(page.getByRole('heading', { name: 'PKB — 個人ナレッジベース' }))
    .toBeVisible()
  await expect(page.getByText('架空サブPC')).toBeVisible()
  await expect(page.getByText('架空の本人申告')).toBeVisible()
  await expect(page.getByRole('button', { name: '記憶を登録' })).toBeVisible()
  await expect(page.getByRole('button', { name: '訂正として登録' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '確認待ち・判断履歴' })).toBeVisible()
  await page.getByRole('textbox', { name: 'PKBキーワード' }).fill('該当しない記憶')
  await page.getByRole('button', { name: '検索', exact: true }).click()
  await expect(page.getByText('該当する記録はありません。')).toBeVisible()
  await page.getByRole('textbox', { name: 'PKBキーワード' }).fill('架空GPU')
  await page.getByRole('checkbox', { name: '過去のClaimも表示' }).check()
  await page.getByRole('button', { name: '検索', exact: true }).click()
  await expect(page.getByText('架空サブPC')).toBeVisible()
  await page.getByRole('link', { name: 'RITSUKO' }).click()
  // RITSUKO automatically selects the most recent Task and sets #task=UUID.
  await expect(page).toHaveURL(/^http:\/\/127\.0\.0\.1:8097\/ui\/(?:#task=[a-f\d-]{36})?$/)
  await expect(page.getByRole('heading', { name: '新しい依頼を入力' })).toBeVisible()
})


test('同一Vue PKB画面で登録・訂正・Pending判断を操作できる', async ({ page }) => {
  await page.route('**/api/v1/pkb/pending?*', async route => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify([{ id: 'fake-pending-1', raw_text: '架空GPUを交換した',
        entity_name: '架空サブPC', reason: 'review' }]),
    })
  })
  await page.route('**/api/v1/pkb/reviewed?*', async route => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
  })
  const memoryWrites: string[] = []
  await page.route('**/api/v1/pkb/register', async route => {
    memoryWrites.push(route.request().postDataJSON().text)
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ status: 'review', reason: 'manual_check' }),
    })
  })
  await page.route('**/api/v1/pkb/correct', async route => {
    memoryWrites.push('correct:' + route.request().postDataJSON().text)
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ status: 'corrected' }),
    })
  })
  const reviews: string[] = []
  await page.route('**/api/v1/pkb/pending/fake-pending-1/review', async route => {
    reviews.push(route.request().postDataJSON().decision)
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ status: 'needs_edit' }),
    })
  })
  await page.goto('http://127.0.0.1:8097/ui/?page=pkb')
  const entry = page.getByRole('textbox', { name: '登録・訂正する文章' })
  await entry.fill('架空の登録テスト')
  await page.getByRole('button', { name: '記憶を登録' }).click()
  await expect(page.getByRole('status')).toContainText('review')
  await entry.fill('架空の訂正テスト')
  await page.getByRole('button', { name: '訂正として登録' }).click()
  await expect(page.getByRole('status')).toContainText('corrected')
  await page.getByRole('button', { name: '要修正' }).click()
  await expect(page.getByRole('status').last()).toContainText('needs_edit')
  expect(memoryWrites).toEqual(['架空の登録テスト', 'correct:架空の訂正テスト'])
  expect(reviews).toEqual(['needs_edit'])
})


test('FinanceとSettingsは単一Secretary Originで利用できる', async ({ page }) => {
  await page.goto('http://127.0.0.1:8097/ui/')
  await page.getByRole('link', { name: '家計・資産' }).click()
  await expect(page).toHaveURL('http://127.0.0.1:8097/ui/?page=finance')
  await expect(page.getByRole('heading', { name: '家計・資産' })).toBeVisible()
  await expect(page.getByRole('region', { name: '家計集計' }).getByText('￥1,500')).toBeVisible()
  await expect(page.getByText('架空の買い物')).toBeVisible()
  await page.getByRole('link', { name: '設定' }).click()
  await expect(page).toHaveURL('http://127.0.0.1:8097/ui/?page=settings')
  await expect(page.getByRole('region', { name: 'サービス接続一覧' }).getByText('架空 OpenAI', { exact: true })).toBeVisible()
  await expect(page.getByText('認証：設定済', { exact: false })).toBeVisible()
})

test('Billingは明示的な更新操作をするまで外部usage APIを呼び出さない', async ({ page }) => {
  const calls: string[] = []
  await page.route('**/api/v1/billing/profiles/fake-profile/snapshot', async route => {
    calls.push(route.request().url())
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        service: 'openai',
        connection_name: '架空 OpenAI',
        period_start: '2026-10-01T00:00:00+00:00',
        fetched_at: '2026-10-18T00:00:00+00:00',
        activity: {
          status: 'known',
          totals: { requests: 155, input_tokens: 73092, output_tokens: 104952 },
          by_model: [{ model: 'fake-model', requests: 155,
            input_tokens: 73092, output_tokens: 104952 }],
        },
        charges: { status: 'known', values: { usd: 2.5837 } },
        credits: { status: 'unknown', reason: 'not_supported' },
        limits: { status: 'unknown' }, subscription: { status: 'unknown' },
      }),
    })
  })
  await page.goto('http://127.0.0.1:8097/ui/?page=service-billing')
  await expect(page.getByRole('heading', { name: '利用料金・契約' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: '対象Billing Profile' })).toHaveValue('fake-profile')
  expect(calls).toEqual([])
  await page.getByRole('button', { name: '最新の利用状況を取得' }).click()
  await expect(page.getByRole('region', { name: '取得した利用状況' })).toBeVisible()
  await expect(page.getByText('$2.5837')).toBeVisible()
  await expect(page.locator('.billing-metric').filter({ hasText: '入力トークン' })).toContainText('73,092')
  await expect(page.locator('.billing-metric').filter({ hasText: '出力トークン' })).toContainText('104,952')
  await expect(page.getByRole('row', { name: /fake-model/ })).toContainText('155')
  await expect(page.getByText('取得結果の詳細（JSON・診断用）')).toBeVisible()
  expect(calls.length).toBe(1)
})

test('Debugも同一Originで実行中サーバー情報を参照できる', async ({ page }) => {
  await page.goto('http://127.0.0.1:8097/ui/?page=debug')
  await expect(page.getByRole('heading', { name: 'システム診断' })).toBeVisible()
  await expect(page.getByRole('region', { name: '実行プロセス' })).toBeVisible()
  await expect(page.getByRole('region', { name: '環境設定状況' })).toBeVisible()
})


test('MAGI Profileと3席割当はSettingsから同一APIで保存できる', async ({ page }) => {
  await page.goto('http://127.0.0.1:8097/ui/?page=settings')
  await expect(page.getByRole('heading', { name: 'MAGI — LLM Profile・メンバー割当' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'CASPER Profile' }))
    .toHaveValue('fake-llm-profile')
  await page.getByRole('button', { name: 'MAGI割当を保存' }).click()
  await expect(page.getByRole('status')).toContainText('MAGIメンバー3席の割当')
})

test('Debugから旧NiceGUIなしでDB ER図を表示・密度変更・Mermaid参照', async ({ page }) => {
  await page.goto('http://127.0.0.1:8097/ui/?page=debug')
  await page.getByRole('link', { name: 'ER図を開く' }).click()
  await expect(page).toHaveURL('http://127.0.0.1:8097/ui/?page=er')
  await expect(page.getByRole('heading', { name: 'DB ER図' })).toBeVisible()
  await expect(page.getByRole('status')).toContainText('テーブル 2件')
  const svg = page.locator('.diagram-render svg')
  await expect(svg).toBeVisible()
  await expect(svg).toContainText('tasks')
  await expect(svg).toContainText('results')
  await expect(svg).not.toContainText('request')
  // A successful API response alone is not acceptable: the relationship
  // diagram must be rendered by Mermaid and remain legible at 100% scale.
  await expect(page.getByRole('status')).toContainText('100%')
  await expect(page.getByRole('button', { name: '全画面' })).toBeEnabled()
  await page.getByRole('button', { name: '＋' }).click()
  await expect(page.getByRole('status')).toContainText('125%')
  await page.getByRole('button', { name: '全体', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('全体表示')
  await page.getByRole('button', { name: '100%' }).click()
  await expect(page.getByRole('status')).toContainText('100%')
  await page.getByRole('combobox', { name: 'ER図の表示密度' }).selectOption('false')
  await expect(svg).toContainText('request')
  await page.getByRole('button', { name: 'Mermaidソース' }).click()
  await expect(page.getByText('erDiagram', { exact: false })).toBeVisible()
})

test('Googleの料金未取得は0円と誤表示せず、実際の利用量だけ示す', async ({ page }) => {
  await page.route('**/api/v1/billing/profiles', async route => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify([{
        id: 'google-profile', display_name: '架空Google',
        connection_id: 'google-conn', connection_name: '架空Google Cloud',
        adapter_key: 'google_cloud', enabled: true, connection_enabled: true,
      }]),
    })
  })
  await page.route('**/api/v1/billing/profiles/google-profile/snapshot', async route => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        service: 'google_cloud', connection_name: '架空Google Cloud',
        period_start: '2026-10-01T00:00:00+00:00',
        fetched_at: '2026-10-18T00:00:00+00:00',
        activity: {
          status: 'known',
          totals: { requests: 22, input_tokens: 1500, output_tokens: 320 },
          by_model: [{ model: 'fixture-gemini', requests: 22,
            input_tokens: 1500, output_tokens: 320 }],
        },
        charges: { status: 'unknown', reason: 'google_cloud_cost_reader_not_implemented' },
        credits: { status: 'unknown' }, subscription: { status: 'unknown' },
        limits: { status: 'unknown' },
      }),
    })
  })
  await page.goto('http://127.0.0.1:8097/ui/?page=service-billing')
  await expect(page.getByRole('combobox', { name: '対象Billing Profile' })).toHaveValue('google-profile')
  await page.getByRole('button', { name: '最新の利用状況を取得' }).click()
  const report = page.getByRole('region', { name: '取得した利用状況' })
  await expect(report).toContainText('fixture-gemini')
  await expect(report).toContainText('費用を取得できていません（0円ではありません）')
  await expect(report).toContainText('Google Cloud Monitoringの利用量と実際の請求金額は別情報')
  await expect(report.locator('.billing-cost strong')).toHaveText('—')
})
