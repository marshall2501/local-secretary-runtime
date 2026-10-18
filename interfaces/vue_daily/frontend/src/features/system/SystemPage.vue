<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** One Secretary origin for Service Connections, Billing and Runtime diagnostics. */
import { onMounted, ref } from 'vue'
import {
  readBillingProfiles, readBillingSnapshot, readConnections,
  readDatabase, readEnvironment, readRuntime,
} from './api'
import type { ConnectionSummary, BillingProfile, RuntimeInfo, EnvironmentItem } from './api'

const props = defineProps<{ section: 'service-billing' | 'debug' | 'settings' }>()
const connections = ref<ConnectionSummary[]>([])
const profiles = ref<BillingProfile[]>([])
const usage = ref<Record<string, unknown> | null>(null)
const runtime = ref<RuntimeInfo | null>(null)
const environment = ref<EnvironmentItem[]>([])
const database = ref<Record<string, unknown> | null>(null)
const selected = ref('')
const loading = ref(false)
const reading = ref(false)
const problem = ref('')
const usageProblem = ref('')
const title = {
  'service-billing': '利用料金・契約',
  debug: 'システム診断',
  settings: '接続設定',
}[props.section]

/** Fetch existing state without any automatic provider calls or DB mutation. */
async function load(): Promise<void> {
  if (loading.value) return
  loading.value = true
  problem.value = ''
  try {
    if (props.section === 'service-billing') {
      profiles.value = await readBillingProfiles()
      selected.value = profiles.value.find(p => p.enabled && p.connection_enabled)?.id || ''
    } else if (props.section === 'settings') {
      connections.value = await readConnections()
    } else {
      const [run, env, db] = await Promise.allSettled([
        readRuntime(), readEnvironment(), readDatabase(),
      ])
      if (run.status === 'fulfilled') runtime.value = run.value
      if (env.status === 'fulfilled') environment.value = env.value
      if (db.status === 'fulfilled') database.value = db.value
      if (run.status === 'rejected') throw run.reason
      if (db.status === 'rejected') problem.value = 'DB診断は取得できません（' + String(db.reason) + '）。'
    }
  } catch (error) {
    problem.value = '情報を取得できません（' + String(error) + '）。'
  } finally {
    loading.value = false
  }
}

/** Provider access is deliberately initiated by an explicit user action. */
async function refreshUsage(): Promise<void> {
  if (reading.value || !selected.value) return
  reading.value = true
  usage.value = null
  usageProblem.value = ''
  try {
    usage.value = await readBillingSnapshot(selected.value)
  } catch (error) {
    usageProblem.value = '利用状況を取得できません（' + String(error) + '）。'
  } finally {
    reading.value = false
  }
}

onMounted(() => { void load() })
</script>

<template>
  <div class="page-title">
    <h1>{{ title }}</h1>
    <span class="muted small">Secretary Server / 共通Application</span>
  </div>
  <p v-if="problem" role="alert" class="error">{{ problem }}</p>
  <section v-if="section === 'settings'" class="card" aria-label="サービス接続一覧">
    <div class="section-head">
      <h2>Service Connections</h2>
      <button type="button" class="secondary" :disabled="loading" @click="load">更新</button>
    </div>
    <p v-if="!connections.length" class="muted">登録済みの接続はありません。</p>
    <div v-for="item in connections" :key="item.id" class="pkb-record">
      <div class="pkb-record-heading">
        <strong>{{ item.display_name }}</strong>
        <span class="status">{{ item.enabled ? '有効' : '無効' }}</span>
      </div>
      <p>{{ item.adapter_key }} / {{ item.endpoint }}</p>
      <p class="muted small">認証：{{ item.auth_configured ? '設定済' : '未設定' }}
        ／ 種別：{{ item.connection_type }} ／ 用途：{{ item.capabilities?.join(', ') || 'なし' }}</p>
    </div>
    <p class="muted small">既存登録の参照を新Vueへ移行しました。接続先・認証の編集は未移行です。</p>
  </section>
  <section v-else-if="section === 'service-billing'" class="card" aria-label="サービス利用状況">
    <h2>Service Billing Profile</h2>
    <p v-if="!profiles.length" class="muted">有効な利用料金Profileはありません。</p>
    <form @submit.prevent="refreshUsage">
      <label>対象Profile
        <select v-model="selected" aria-label="対象Billing Profile">
          <option value="">選択してください</option>
          <option v-for="item in profiles" :key="item.id" :value="item.id"
            :disabled="!item.enabled || !item.connection_enabled">
            {{ item.display_name }} / {{ item.connection_name || item.adapter_key }}
          </option>
        </select>
      </label>
      <div class="command-row">
        <button type="submit" class="primary-command" :disabled="reading || !selected">
          {{ reading ? '取得中…' : '最新の利用状況を取得' }}
        </button>
      </div>
    </form>
    <p v-if="usageProblem" role="alert" class="error">{{ usageProblem }}</p>
    <div v-if="usage" class="pkb-record">
      <h3>取得した利用状況</h3>
      <pre style="white-space:pre-wrap;overflow-wrap:anywhere">{{ JSON.stringify(usage, null, 2) }}</pre>
    </div>
  </section>
  <template v-else-if="section === 'debug'">
    <section class="card" aria-label="実行プロセス">
      <div class="section-head">
        <h2>実行プロセス</h2>
        <button type="button" class="secondary" :disabled="loading" @click="load">更新</button>
      </div>
      <p v-if="runtime">PID：{{ runtime.pid }} ／ Python：{{ runtime.python }}
        ／ Git：{{ runtime.branch }} / {{ runtime.commit }}</p>
      <p v-if="runtime" class="muted small">起動：{{ runtime.process_started_at }}</p>
    </section>
    <section class="card" aria-label="データベース状態">
      <h2>データベース</h2>
      <template v-if="database">
        <p>状態：{{ database.status }} ／ Identity：{{ JSON.stringify(database.identity || {}) }}</p>
        <pre style="white-space:pre-wrap;overflow-wrap:anywhere">{{ JSON.stringify(database.migration || {}, null, 2) }}</pre>
      </template>
      <p v-else class="muted">DB診断はまだ取得できていません。</p>
    </section>
    <section class="card" aria-label="環境設定状況">
      <h2>環境設定</h2>
      <div v-for="item in environment" :key="item.name" class="pkb-record">
        <strong>{{ item.name }}</strong><span> — {{ item.value }}</span>
      </div>
    </section>
  </template>
</template>
