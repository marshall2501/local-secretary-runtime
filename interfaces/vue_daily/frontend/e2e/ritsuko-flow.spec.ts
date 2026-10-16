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

test('8099新規Task→本人回答→Review→確定→F5同IDで復元する', async ({ page }) => {
  await page.goto('http://127.0.0.1:8099/ui/')
  await page.getByRole('textbox', { name: '依頼内容' }).fill('Pythonの辞書を簡潔に説明してください')
  await page.getByRole('button', { name: '依頼を送信' }).click()
  await expect(page).toHaveURL(/#task=[a-f\d-]{36}$/i)
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

  await page.reload()
  await expect(page).toHaveURL(taskUrl)
  await expect(page.getByText('完了（Task確定状態）')).toBeVisible()
  await expect(page.getByText('架空Taskの回答を確定しました。')).toBeVisible()
})

test('停止は保存済み状態で確認し、Actionの出典と監査履歴を開ける', async ({ page }) => {
  await page.goto('http://127.0.0.1:8099/ui/')
  await page.getByRole('textbox', { name: '依頼内容' }).fill('停止試験として処理を開始する')
  await page.getByRole('button', { name: '依頼を送信' }).click()
  await expect(page.getByText('受付（Task確定状態）')).toBeVisible()
  await page.getByRole('button', { name: '停止を要求' }).click()
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
  await page.getByRole('button', { name: 'さらに読み込む' }).click()
  await expect(page.getByText('12件表示')).toBeVisible()
  await expect(page.getByText('Goal確定済み / v2。Task完了とは別です。')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Task履歴（既存画面）' }))
    .toHaveAttribute('href', 'http://127.0.0.1:8093/core/history')
})
