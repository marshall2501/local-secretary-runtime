<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** One Secretary origin for Service Connections, Billing and Runtime diagnostics. */
import { onMounted, ref } from 'vue'
import {
  readBillingProfiles, readBillingSnapshot, readConnections,
  readDatabase, readEnvironment, readRuntime, saveConnection, saveBillingProfile,
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
const formBusy = ref(false)
const formMessage = ref('')
const formError = ref('')
const connectionForm = ref({
  connection_id: '', display_name: '', adapter_key: 'openai',
  endpoint: '', credential_ref: '', connection_type: 'api_key',
  connection_role: '', capabilities: 'llm_inference',
  config_json: '{}', auth_json: '', enabled: true,
})
const profileForm = ref({
  profile_id: '', display_name: '', connection_id: '', enabled: true,
})
function editConnection(row: ConnectionSummary): void {
  connectionForm.value = {
    connection_id: row.id, display_name: row.display_name,
    adapter_key: row.adapter_key, endpoint: row.endpoint || '',
    credential_ref: row.credential_ref || '',
    connection_type: row.connection_type || 'none',
    connection_role: row.connection_role || '',
    capabilities: (row.capabilities || []).join(', '),
    config_json: JSON.stringify(row.config_data || {}, null, 2),
    auth_json: '', enabled: row.enabled,
  }
  formError.value = ''
  formMessage.value = ''
}
async function writeConnection(): Promise<void> {
  if (formBusy.value) return
  formBusy.value = true
  formError.value = ''
  formMessage.value = ''
  try {
    const form = connectionForm.value
    const config = JSON.parse(form.config_json) as Record<string, unknown>
    const auth = form.auth_json.trim()
      ? JSON.parse(form.auth_json) as Record<string, unknown> : undefined
    const result = await saveConnection({
      ...(form.connection_id ? { connection_id: form.connection_id } : {}),
      display_name: form.display_name, adapter_key: form.adapter_key,
      endpoint: form.endpoint || undefined, credential_ref: form.credential_ref || undefined,
      connection_type: form.connection_type,
      connection_role: form.connection_role || undefined,
      capabilities: form.capabilities.split(',').map(x => x.trim()).filter(Boolean),
      config_data: config, ...(auth ? { auth_data: auth } : {}),
      enabled: form.enabled,
    })
    formMessage.value = '保存しました：' + result.display_name
    connectionForm.value.auth_json = ''
    connections.value = await readConnections()
  } catch (error) {
    formError.value = '接続設定の保存に失敗しました（' + String(error) + '）。'
  } finally {
    formBusy.value = false
  }
}
async function writeBillingProfile(): Promise<void> {
  if (formBusy.value) return
  formBusy.value = true
  formError.value = ''
  formMessage.value = ''
  try {
    const f = profileForm.value
    const result = await saveBillingProfile({
      ...(f.profile_id ? { profile_id: f.profile_id } : {}),
      display_name: f.display_name, connection_id: f.connection_id, enabled: f.enabled,
    })
    formMessage.value = '保存しました：' + result.display_name
    profiles.value = await readBillingProfiles()
  } catch (error) {
    formError.value = 'Billing Profile保存に失敗しました（' + String(error) + '）。'
  } finally {
    formBusy.value = false
  }
}

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
    <h3>接続設定の登録・編集</h3>
    <p class="muted small">一覧の接続を選択すると既存設定を編集できます。認証JSONは表示せず、空欄なら保持します。</p>
    <div class="command-row">
      <button type="button" class="secondary" @click="editConnection({
        id: '', display_name: '', adapter_key: 'openai', enabled: true,
        auth_configured: false, connection_type: 'api_key',
      })">新規接続</button>
    </div>
    <div class="command-row">
      <button v-for="item in connections" :key="item.id" type="button" class="secondary"
        @click="editConnection(item)">編集：{{ item.display_name }}</button>
    </div>
    <form class="command-form" @submit.prevent="writeConnection">
      <label>表示名 <input v-model="connectionForm.display_name" aria-label="接続表示名" maxlength="200" required /></label>
      <label>Adapter
        <select v-model="connectionForm.adapter_key" aria-label="接続Adapter">
          <option value="ollama">Ollama</option><option value="openai">OpenAI</option>
          <option value="google">Google</option><option value="google_cloud">Google Cloud</option>
        </select>
      </label>
      <label>Endpoint <input v-model="connectionForm.endpoint" aria-label="接続Endpoint" maxlength="500" /></label>
      <label>認証種別
        <select v-model="connectionForm.connection_type" aria-label="接続認証種別">
          <option value="none">なし</option><option value="api_key">API Key</option>
          <option value="username_password">ユーザー名・パスワード</option>
          <option value="oauth2">OAuth2</option>
          <option value="external_credentials">外部認証</option>
        </select>
      </label>
      <label>用途（カンマ区切り）
        <input v-model="connectionForm.capabilities" aria-label="接続用途" />
      </label>
      <label>Credential参照 <input v-model="connectionForm.credential_ref" aria-label="Credential参照" /></label>
      <label>Config JSON
        <textarea v-model="connectionForm.config_json" aria-label="接続Config JSON" rows="3" />
      </label>
      <label>認証JSON（新規・更新時のみ入力）
        <textarea v-model="connectionForm.auth_json" aria-label="接続認証JSON" rows="2"
          placeholder='{"api_key":"..."}' />
      </label>
      <label><input v-model="connectionForm.enabled" type="checkbox" /> 有効</label>
      <button type="submit" class="primary-command"
        :disabled="formBusy || !connectionForm.display_name.trim()">
        {{ formBusy ? '保存中…' : 'Service Connectionを保存' }}
      </button>
    </form>
    <p v-if="formError" role="alert" class="error">{{ formError }}</p>
    <p v-if="formMessage" role="status">{{ formMessage }}</p>
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
    <h3>Billing Profileの登録・編集</h3>
    <form class="command-form" @submit.prevent="writeBillingProfile">
      <label>Profile名 <input v-model="profileForm.display_name" aria-label="Billing Profile名" required /></label>
      <label>接続ID <input v-model="profileForm.connection_id" aria-label="Billing接続ID" required /></label>
      <label><input v-model="profileForm.enabled" type="checkbox" /> 有効</label>
      <button type="submit" class="secondary" :disabled="formBusy || !profileForm.display_name.trim() || !profileForm.connection_id.trim()">
        Profileを保存
      </button>
    </form>
    <p v-if="formError" role="alert" class="error">{{ formError }}</p>
    <p v-if="formMessage" role="status">{{ formMessage }}</p>
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
