<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** One Secretary origin for Service Connections, Billing and Runtime diagnostics. */
import { computed, onMounted, ref } from 'vue'
import BillingSnapshotView from './BillingSnapshotView.vue'
import LayoutSettings from './LayoutSettings.vue'
import MagiSettingsPanel from './MagiSettingsPanel.vue'
import {
  readBillingProfiles, readBillingSnapshot, readConnections,
  readDatabase, readEnvironment, readRuntime, readApiRoutes, saveConnection, saveBillingProfile,
} from './api'
import type { ConnectionSummary, BillingProfile, ServiceBillingSnapshot, RuntimeInfo, EnvironmentItem, ApiRouteInfo } from './api'

const props = defineProps<{ section: 'service-billing' | 'debug' | 'settings' }>()
const connections = ref<ConnectionSummary[]>([])
const profiles = ref<BillingProfile[]>([])
const usage = ref<ServiceBillingSnapshot | null>(null)
const runtime = ref<RuntimeInfo | null>(null)
const environment = ref<EnvironmentItem[]>([])
const database = ref<Record<string, unknown> | null>(null)
const routes = ref<ApiRouteInfo[]>([])
/** Present diagnostic metadata as named values, never as opaque JSON. */
const dbIdentity = computed(() => (database.value?.identity || {}) as Record<string, string | number>)
const dbMigration = computed(() => (database.value?.migration || {}) as Record<string, string | number>)
const dbCounts = computed(() => (Array.isArray(database.value?.counts) ? database.value.counts : []) as Array<{
  label: string; relation: string; status: string; count: number | null
}>)
const dbRelations = computed(() => (Array.isArray(database.value?.relations) ? database.value.relations : []) as Array<{
  name: string; type: string
}>)
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
  settings: '設定',
}[props.section]

/** Fetch existing state without any automatic provider calls or DB mutation. */
async function load(): Promise<void> {
  if (loading.value) return
  loading.value = true
  problem.value = ''
  try {
    if (props.section === 'service-billing') {
      const [nextProfiles, nextConnections] = await Promise.all([
        readBillingProfiles(), readConnections(),
      ])
      profiles.value = nextProfiles
      connections.value = nextConnections
      selected.value = profiles.value.find(p => p.enabled && p.connection_enabled)?.id || ''
    } else if (props.section === 'settings') {
      connections.value = await readConnections()
    } else {
      const [run, env, db, api] = await Promise.allSettled([
        readRuntime(), readEnvironment(), readDatabase(), readApiRoutes(),
      ])
      if (run.status === 'fulfilled') runtime.value = run.value
      if (env.status === 'fulfilled') environment.value = env.value
      if (api.status === 'fulfilled') routes.value = api.value
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
    <MagiSettingsPanel />
  </section>
  <section v-else-if="section === 'service-billing'" class="card" aria-label="サービス利用状況">
    <h2>API使用状況</h2>
    <p class="muted small">保存済みのService Billing接続から当月の実利用量・費用を取得します。期間フィルタ・推移は未対応です。</p>
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
    <BillingSnapshotView v-if="usage" :snapshot="usage" />
    <details class="billing-editor">
      <summary>Billing Profileの登録・編集</summary>
      <p class="muted small">接続先の変更・新規登録が必要な場合だけ開いてください。</p>
    <div class="command-row">
      <button v-for="item in profiles" :key="item.id" type="button" class="secondary"
        @click="profileForm = {
          profile_id: item.id, display_name: item.display_name,
          connection_id: item.connection_id, enabled: item.enabled,
        }">編集：{{ item.display_name }}</button>
      <button type="button" class="secondary"
        @click="profileForm = { profile_id: '', display_name: '', connection_id: '', enabled: true }">新規Profile</button>
    </div>
    <form class="command-form" @submit.prevent="writeBillingProfile">
      <label>Profile名 <input v-model="profileForm.display_name" aria-label="Billing Profile名" required /></label>
      <label>接続
        <select v-model="profileForm.connection_id" aria-label="Billing接続ID" required>
          <option value="">選択してください</option>
          <option v-for="item in connections.filter(c => c.capabilities?.includes('service_billing_read'))"
            :key="item.id" :value="item.id">
            {{ item.display_name }} / {{ item.adapter_key }}
          </option>
        </select>
      </label>
      <label><input v-model="profileForm.enabled" type="checkbox" /> 有効</label>
      <button type="submit" class="secondary" :disabled="formBusy || !profileForm.display_name.trim() || !profileForm.connection_id.trim()">
        Profileを保存
      </button>
    </form>
    <p v-if="formError" role="alert" class="error">{{ formError }}</p>
    <p v-if="formMessage" role="status">{{ formMessage }}</p>
    </details>
  </section>
  <template v-else-if="section === 'debug'">
    <section class="card" aria-label="システム概要">
      <div class="section-head">
        <h2>稼働状況</h2>
        <button type="button" class="secondary" :disabled="loading" @click="load">最新の状態を取得</button>
      </div>
      <div class="system-summary-grid">
        <article class="system-metric">
          <span>Secretary Server</span>
          <strong>{{ runtime ? runtime.branch + ' / ' + runtime.commit.slice(0, 12) : '未取得' }}</strong>
          <small>PID {{ runtime?.pid ?? '—' }} / Python {{ runtime?.python ?? '—' }}</small>
        </article>
        <article class="system-metric" :class="{ 'metric-warning': database?.status !== 'ok' || !database?.boundary_ok }">
          <span>PostgreSQL</span>
          <strong>{{ database?.status === 'ok' && database?.boundary_ok ? '接続OK' : '要確認' }}</strong>
          <small>{{ dbIdentity.database || '未取得' }} / {{ dbIdentity.database_size || '—' }}</small>
        </article>
        <article class="system-metric">
          <span>Migration</span>
          <strong>{{ dbMigration.latest || '未取得' }}</strong>
          <small>{{ dbMigration.count ?? '—' }}件 / {{ dbMigration.source || '—' }}</small>
        </article>
      </div>
      <p v-if="runtime" class="muted small">プロセス開始：{{ runtime.process_started_at }}</p>
    </section>
    <section class="card" aria-label="データベース状態">
      <h2>データベース詳細</h2>
      <p class="muted small">接続中のDBの状態だけを確認します。データ・Secretの変更は行いません。</p>
      <div v-if="database" class="diagnostic-kv">
        <div><dt>データベース</dt><dd>{{ dbIdentity.database || '—' }}</dd></div>
        <div><dt>接続ユーザー</dt><dd>{{ dbIdentity.user || '—' }}</dd></div>
        <div><dt>接続先</dt><dd>{{ dbIdentity.host || '—' }}:{{ dbIdentity.port || '—' }}</dd></div>
        <div><dt>PostgreSQL</dt><dd>{{ dbIdentity.server_version || '—' }}</dd></div>
        <div><dt>DB容量</dt><dd>{{ dbIdentity.database_size || '—' }}</dd></div>
        <div><dt>接続数</dt><dd>{{ dbIdentity.database_connections ?? '—' }}</dd></div>
      </div>
      <p v-else class="muted">DB診断はまだ取得できていません。</p>
      <details class="diagnostic-details" v-if="dbCounts.length">
        <summary>主要データの件数（{{ dbCounts.length }}項目）</summary>
        <div class="table-scroll">
          <table class="diagnostic-table">
            <thead><tr><th scope="col">対象</th><th scope="col">テーブル</th><th scope="col">件数</th><th scope="col">状態</th></tr></thead>
            <tbody>
              <tr v-for="item in dbCounts" :key="item.relation">
                <th scope="row">{{ item.label }}</th><td><code>{{ item.relation }}</code></td>
                <td class="numeric">{{ item.count ?? '—' }}</td><td>{{ item.status }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>
      <details class="diagnostic-details" v-if="dbRelations.length">
        <summary>DB Relation一覧（{{ dbRelations.length }}件）</summary>
        <div class="relation-grid">
          <span v-for="item in dbRelations" :key="item.name"><code>{{ item.name }}</code><small>{{ item.type }}</small></span>
        </div>
      </details>
    </section>
    <section class="card" aria-label="DB ER図">
      <div class="section-head">
        <div>
          <h2>DB ER図</h2>
          <p class="muted small">Mermaidの関係性に沿った配置でテーブル・PK・FKを表示します。</p>
        </div>
        <a class="link-button" href="/ui/?page=er">ER図を開く →</a>
      </div>
      <p class="muted small">読み取り専用。100%表示・全画面・全カラム切替・SVG保存に対応。</p>
    </section>
    <section class="card" aria-label="API一覧">
      <details class="diagnostic-details">
        <summary>API一覧（{{ routes.length }}件）</summary>
        <p class="muted small">現在のSecretary Serverが提供する機能別Routerを含む一覧です。</p>
        <div class="table-scroll">
          <table class="diagnostic-table">
            <thead><tr><th scope="col">Method</th><th scope="col">パス</th><th scope="col">処理名</th></tr></thead>
            <tbody>
              <tr v-for="item in routes" :key="item.path + item.methods.join(',')">
                <td><span class="method-chip" :class="{ 'method-write': item.methods.includes('POST') }">{{ item.methods.join(', ') }}</span></td>
                <td><code>{{ item.path }}</code></td><td>{{ item.name }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>
    </section>
    <section class="card" aria-label="環境設定状況">
      <details class="diagnostic-details">
        <summary>環境設定（{{ environment.length }}項目）</summary>
        <div class="table-scroll">
          <table class="diagnostic-table">
            <thead><tr><th scope="col">設定項目</th><th scope="col">状態</th></tr></thead>
            <tbody>
              <tr v-for="item in environment" :key="item.name">
                <th scope="row">{{ item.name }}</th><td>{{ item.value }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>
    </section>
  </template>
  <LayoutSettings v-if="section === 'settings'" />
</template>

<style scoped>
.system-summary-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; margin-top:12px; }
.system-metric { padding:16px; border:1px solid #c4ddd8; border-radius:9px; background:#f1faf7; display:flex; flex-direction:column; gap:5px; min-width:0; }
.system-metric span { font-size:12px; color:#587067; font-weight:650; }
.system-metric strong { font-size:19px; color:#146052; overflow-wrap:anywhere; }
.system-metric small { color:#536c73; overflow-wrap:anywhere; }
.system-metric.metric-warning { background:#fff8eb; border-color:#ead3aa; }
.system-metric.metric-warning strong { color:#95561b; }
.diagnostic-kv { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:10px; margin:15px 0; }
.diagnostic-kv div { padding:10px 12px; background:#f7f9fb; border:1px solid #e2eaf0; border-radius:7px; min-width:0; }
.diagnostic-kv dt { font-size:12px; color:#607287; }
.diagnostic-kv dd { margin:3px 0 0; font-weight:600; overflow-wrap:anywhere; }
.diagnostic-details { margin-top:14px; border-top:1px solid #dfe8ed; padding:12px 0 0; }
.diagnostic-details summary { cursor:pointer; font-size:14px; font-weight:700; color:#204c65; }
.table-scroll { overflow-x:auto; max-width:100%; margin-top:12px; }
.diagnostic-table { border-collapse:collapse; width:100%; font-size:13px; }
.diagnostic-table th,.diagnostic-table td { padding:10px 12px; text-align:left; border-bottom:1px solid #e2eaf0; vertical-align:top; }
.diagnostic-table thead { background:#f0f6fa; }
.diagnostic-table code { font-size:12px; overflow-wrap:anywhere; }
.diagnostic-table td.numeric { text-align:right; font-variant-numeric:tabular-nums; }
.relation-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:8px; margin-top:12px; }
.relation-grid span { border:1px solid #e2e8f0; border-radius:6px; padding:7px 10px; min-width:0; display:flex; flex-direction:column; }
.relation-grid small { color:#64748b; }
.method-chip { border-radius:5px; background:#e2f0fa; color:#155783; font-size:12px; font-weight:700; padding:4px 7px; }
.method-chip.method-write { background:#fff1d8; color:#92521a; }
</style>
