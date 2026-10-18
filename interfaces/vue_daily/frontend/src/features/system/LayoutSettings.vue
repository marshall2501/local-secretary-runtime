<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** Edit the owner's actual UI defaults, not duplicate application or Secret settings. */
import { onMounted, ref } from 'vue'
import {
  readLayoutDefaults, readLayoutPreferences, saveLayoutPreferences,
} from '../../shared/layoutPreferences'
import type { LayoutPreferences } from '../../shared/layoutPreferences'

const preferences = ref<LayoutPreferences | null>(null)
const loading = ref(false)
const saving = ref(false)
const problem = ref('')
const notice = ref('')
const dirty = ref(false)
const pkbFields = [
  { key: 'write', label: '記録' },
  { key: 'correction', label: '訂正' },
  { key: 'search', label: '検索・履歴' },
  { key: 'entities', label: 'Entity一覧' },
  { key: 'pending', label: '確認待ち' },
  { key: 'reviewed', label: '処理済みの確認待ち' },
] as const
const entityFields = [
  { key: 'overview', label: '現在値' },
  { key: 'history', label: '履歴・Event' },
  { key: 'relations', label: '関連Entity' },
  { key: 'sources', label: '出典' },
] as const
const financeFields = [
  { key: 'filter', label: '家計フィルタ・検索' },
  { key: 'stored', label: '保存済み家計' },
  { key: 'monthly', label: '月別集計' },
  { key: 'categories', label: 'カテゴリ別支出' },
  { key: 'details', label: '保存済み明細' },
  { key: 'imports', label: 'Import履歴' },
  { key: 'csv', label: 'CSV取込' },
] as const

async function load(): Promise<void> {
  loading.value = true
  problem.value = ''
  try {
    preferences.value = await readLayoutPreferences()
    dirty.value = false
  } catch (error) {
    problem.value = '画面設定を読み取れません（' + String(error) + '）。'
  } finally {
    loading.value = false
  }
}
async function factoryDefaults(): Promise<void> {
  problem.value = ''
  try {
    preferences.value = await readLayoutDefaults()
    dirty.value = true
    notice.value = '初期値を編集中です。保存するまでは反映されません。'
  } catch (error) {
    problem.value = '初期値を取得できません（' + String(error) + '）。'
  }
}
async function save(): Promise<void> {
  if (!preferences.value || saving.value) return
  saving.value = true
  problem.value = ''
  notice.value = ''
  try {
    preferences.value = await saveLayoutPreferences(preferences.value)
    dirty.value = false
    notice.value = '画面レイアウト設定を保存しました。対象画面を開き直すと反映されます。'
  } catch (error) {
    problem.value = '画面設定を保存できません（' + String(error) + '）。'
  } finally {
    saving.value = false
  }
}
onMounted(() => { void load() })
</script>

<template>
  <section class="card layout-settings" aria-label="画面レイアウト設定">
    <div class="section-head">
      <div>
        <h2>画面レイアウト設定</h2>
        <p class="muted small">表示・初期展開・件数を設定します。旧NiceGUIと同じローカル設定ファイルを使用します。</p>
      </div>
      <button class="secondary" type="button" :disabled="loading || saving" @click="load">再読み込み</button>
    </div>
    <p v-if="problem" class="error" role="alert">{{ problem }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="loading" role="status">画面設定を取得中…</p>
    <div v-if="preferences" class="layout-groups">
      <details class="layout-group" open>
        <summary>PKBの表示・初期展開</summary>
        <p class="muted small">確認待ちの表示設定はPKB画面の候補欄にも反映します。</p>
        <div v-for="item in pkbFields" :key="item.key" class="layout-setting-row">
          <span>{{ item.label }}</span>
          <label><input v-model="preferences.visibility.pkb[item.key]" type="checkbox" @change="dirty = true"/> 表示</label>
          <label v-if="item.key !== 'reviewed'"><input v-model="preferences.pkb[item.key]" type="checkbox" @change="dirty = true"/> 初期展開</label>
        </div>
      </details>
      <details class="layout-group">
        <summary>Entity詳細の表示</summary>
        <div v-for="item in entityFields" :key="item.key" class="layout-setting-row">
          <span>{{ item.label }}</span>
          <label><input v-model="preferences.visibility.entity[item.key]" type="checkbox" @change="dirty = true"/> 表示</label>
        </div>
        <label class="field-label">初期表示タブ
          <select v-model="preferences.entity.default_tab" @change="dirty = true">
            <option v-for="item in entityFields" :key="item.key"
              :disabled="!preferences.visibility.entity[item.key]"
              :value="item.key">{{ item.label }}</option>
          </select>
        </label>
        <p class="muted small">Entityのタブは最低1つ必要です。非表示タブを初期表示には指定できません。</p>
      </details>
      <details class="layout-group">
        <summary>家計・資産の表示・初期展開</summary>
        <div v-for="item in financeFields" :key="item.key" class="layout-setting-row">
          <span>{{ item.label }}</span>
          <label><input v-model="preferences.visibility.finance[item.key]" type="checkbox" @change="dirty = true"/> 表示</label>
          <label><input v-model="preferences.finance[item.key]" type="checkbox" @change="dirty = true"/> 初期展開</label>
        </div>
        <label class="field-label">保存済み明細の1ページ件数
          <select v-model.number="preferences.finance.recent_limit" @change="dirty = true">
            <option v-for="n in [25,50,100]" :key="n" :value="n">{{ n }}件</option>
          </select>
        </label>
      </details>
      <details class="layout-group">
        <summary>RITSUKOの表示・件数</summary>
        <div class="layout-setting-row">
          <span>MAGI設定を表示</span>
          <label><input v-model="preferences.visibility.core.magi_configuration"
            type="checkbox" @change="dirty = true"/> 表示</label>
          <label><input v-model="preferences.core.magi_configuration"
            type="checkbox" @change="dirty = true"/> 初期展開</label>
        </div>
        <div class="layout-setting-row">
          <span>Task検証・実行記録</span>
          <label><input v-model="preferences.visibility.core.trace"
            type="checkbox" @change="dirty = true"/> 表示</label>
          <label><input v-model="preferences.core.trace"
            type="checkbox" @change="dirty = true"/> 初期展開</label>
        </div>
        <label class="field-label">進行中Taskの初期表示件数
          <select v-model.number="preferences.core.open_limit" @change="dirty = true">
            <option v-for="n in 10" :key="n" :value="n">{{ n }}件</option>
          </select>
        </label>
        <label class="field-label">完了Taskの初期表示件数
          <select v-model.number="preferences.core.completed_limit" @change="dirty = true">
            <option v-for="n in 10" :key="n" :value="n">{{ n }}件</option>
          </select>
        </label>
        <p class="muted small">本人の入力・最終回答・操作ボタンは、非表示設定で隠しません。</p>
      </details>
      <div class="command-row">
        <button class="primary-command" type="button" :disabled="saving" @click="save">保存して反映</button>
        <button class="secondary" type="button" :disabled="saving" @click="factoryDefaults">初期値を読み込む</button>
        <span v-if="dirty" class="muted small">未保存の変更があります。</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.layout-settings { margin-top:17px; }
.layout-settings h2 { margin-bottom:5px; }
.layout-settings p { margin:0; }
.layout-groups { display:flex; flex-direction:column; gap:12px; }
.layout-group { border:1px solid #c9dbe6; border-radius:8px; background:#f8fbff; }
.layout-group summary { font-weight:700; cursor:pointer; padding:12px 15px; background:#eaf2fa; border-radius:8px; }
.layout-group > p { padding:7px 13px; }
.layout-setting-row { display:grid; grid-template-columns:minmax(120px,1fr) 100px 130px; gap:10px; align-items:center; border-top:1px solid #e0e8ef; padding:10px 14px; }
.layout-setting-row label { display:flex; align-items:center; gap:5px; font-size:13px; }
.field-label { display:flex; flex-direction:column; max-width:330px; gap:6px; margin:15px 14px; font-size:13px; font-weight:600; }
.field-label select { padding:8px; border:1px solid #acc0d0; border-radius:6px; background:#fff; }
@media(max-width:600px) { .layout-setting-row { grid-template-columns:1fr 95px; } }
</style>
