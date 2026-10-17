// Provenance-Origin: original
// Provenance-AI: AI-generated
/**
 * ブラウザによる日常UIの本人操作を隔離再現する。
 * すべてfake Taskであり、実MAGI・PKB・運用DB・外部Actionは使わない。
 */
import { expect, test } from '@playwright/test'

test('8097から8099へ通常リンク遷移し、フォームを実際に編集できる', async ({ page }) => {
  await page.goto('http://127.0.0.1:8097/ui/')
  await expect(page.getByRole('heading', { name: '新しい依頼を入力' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: '依頼内容' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '依頼を送信' })).toBeDisabled()
  await page.getByRole('link', { name: '実Task操作画面（8099）を開く' }).click()
  await expect(page).toHaveURL(/^http:\/\/127\.0\.0\.1:8099\/ui\//)
  const field = page.getByRole('textbox', { name: '依頼内容' })
  await expect(field).toBeEnabled()
  await field.fill('本当に入力できる')
  await expect(field).toHaveValue('本当に入力できる')
  await expect(page.getByRole('button', { name: '依頼を送信' })).toBeEnabled()
  await expect(page.getByText('実Task操作API接続中')).toBeVisible()
})

test('Vueの両判断方式は同じTask APIに保存され、方式はTask単位で固定される', async ({ page }) => {
  await page.goto('http://127.0.0.1:8099/ui/')
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
      'http://127.0.0.1:8099/api/v1/ritsuko/tasks/' + taskId,
    )
    expect(response.status()).toBe(200)
    const persisted = await response.json()
    expect(persisted.trace.task.magi_session.decision_strategy).toBe(item.value)
  }
})

test('待機中のVueは3秒毎の多重GETを発行しない', async ({ page }) => {
  await page.goto('http://127.0.0.1:8097/ui/')
  await expect(page.getByText('Task参照API接続中（読取専用）')).toBeVisible()
  // 初期の一覧・詳細・Healthが揃った後、ネットワーク実送信を観測する。
  await expect(page.locator('.task-item').first()).toBeVisible()
  const requests: string[] = []
  page.on('request', request => {
    if (request.url().includes('/api/v1/ritsuko/')) requests.push(request.url())
  })
  await page.waitForTimeout(7_000)
  expect(requests).toEqual([])
})

test('保存した既定方式で選択欄なしでも同じTask APIを利用する', async ({ page }) => {
  await page.goto('http://127.0.0.1:8099/')
  await expect(page).toHaveURL(/^http:\/\/127\.0\.0\.1:8099\/ui\//)
  const endpoint = 'http://127.0.0.1:8099/api/v1/ritsuko/preferences'
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
      'http://127.0.0.1:8099/api/v1/ritsuko/tasks/' + taskId,
    )
    expect((await saved.json()).trace.task.magi_session.decision_strategy).toBe('llm')
  } finally {
    const reset = await page.request.post(endpoint, { data: original })
    expect(reset.ok()).toBeTruthy()
  }
})

test('本人回答中はTaskが確認待ちでもMAGI実行進捗を表示する', async ({ page }) => {
  await page.goto('http://127.0.0.1:8099/ui/')
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

test('8099新規Task→本人回答→Review→確定→F5同IDで復元する', async ({ page, context }) => {
  await page.goto('http://127.0.0.1:8099/ui/')
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
    origin: 'http://127.0.0.1:8099',
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
  await page.goto('http://127.0.0.1:8099/ui/')
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
  await page.goto('http://127.0.0.1:8099/ui/')
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
  await page.goto('http://127.0.0.1:8099/ui/')
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
  for (let attempt = 0; attempt < 8; attempt++) {
    // While the current page is loading, the same button is disabled;
    // a count alone does not imply that it can be clicked. The final refresh
    // may remove it, so wait for either enabled or absent.
    await expect.poll(async () => {
      if (await more.count() === 0) return 'end'
      return await more.isEnabled() ? 'ready' : 'loading'
    }).not.toBe('loading')
    if (await more.count() === 0) break
    await more.click()
  }
  await expect(more).toHaveCount(0)
  // 先行の停止試験Taskが選択済みの場合もある。固定の旧Taskを明示的に選ぶ。
  await page.getByRole('button', { name: /架空の過去Task 12/ }).click()
  await expect(page.getByText('Goal確定済み / v2。Task完了とは別です。')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Task履歴（既存画面）' }))
    .toHaveAttribute('href', 'http://127.0.0.1:8093/core/history')
})
