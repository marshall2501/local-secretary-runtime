<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** One Secretary origin for Service Connections, Billing and Runtime diagnostics. */
import { onMounted, ref } from 'vue'
import {
  readBillingProfiles, readBillingSnapshot, readConnections,
  readDatabase, readEnvironment, readRuntime, readApiRoutes, saveConnection, saveBillingProfile,
  readMagiProfiles, readMagiMembers, saveMagiProfile, saveMagiMembers,
} from './api'
import type { ConnectionSummary, BillingProfile, RuntimeInfo, EnvironmentItem, ApiRouteInfo, LlmProfile, MagiMember } from './api'

const props = defineProps<{ section: 'service-billing' | 'debug' | 'settings' }>()
const connections = ref<ConnectionSummary[]>([])
const profiles = ref<BillingProfile[]>([])
const usage = ref<Record<string, unknown> | null>(null)
const runtime = ref<RuntimeInfo | null>(null)
const environment = ref<EnvironmentItem[]>([])
const database = ref<Record<string, unknown> | null>(null)
const routes = ref<ApiRouteInfo[]>([])
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
const llmProfiles = ref<LlmProfile[]>([])
const magiMembers = ref<MagiMember[]>([])
const newModel = ref('')
const newProfileName = ref('')
const newProfileConnection = ref('')
const magiMessage = ref('')
const magiError = ref('')
const magiBusy = ref(false)
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
      const [nextProfiles, nextConnections] = await Promise.all([
        readBillingProfiles(), readConnections(),
      ])
      profiles.value = nextProfiles
      connections.value = nextConnections
      selected.value = profiles.value.find(p => p.enabled && p.connection_enabled)?.id || ''
    } else if (props.section === 'settings') {
      const [nextConnections, nextProfiles, nextMembers] = await Promise.all([
        readConnections(), readMagiProfiles(), readMagiMembers(),
      ])
      connections.value = nextConnections
      llmProfiles.value = nextProfiles
      const byName = new Map(nextMembers.map(m => [m.name, m]))
      magiMembers.value = (['MELCHIOR', 'CASPER', 'BALTHASAR'] as const).map(name =>
        byName.get(name) || {
          name, profile_id: '', enabled: false, weight: 1,
          timeout_seconds: 120, retry_within_turn: false,
        },
      )
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

/** Persist new Profile using an already configured inference connection. */
async function writeMagiProfile(): Promise<void> {
  if (magiBusy.value) return
  magiBusy.value = true
  magiMessage.value = ''
  magiError.value = ''
  try {
    const created = await saveMagiProfile({
      connection_id: newProfileConnection.value, model: newModel.value,
      display_name: newProfileName.value || newModel.value, enabled: true,
    })
    magiMessage.value = 'LLM Profile登録：' + created.display_name
    llmProfiles.value = await readMagiProfiles()
    newModel.value = ''
    newProfileName.value = ''
  } catch (error) {
    magiError.value = 'LLM Profileを保存できません（' + String(error) + '）。'
  } finally {
    magiBusy.value = false
  }
}

/** Save all three logical slots together, honoring Core's constraints. */
async function writeMagiAssignments(): Promise<void> {
  if (magiBusy.value) return
  magiBusy.value = true
  magiMessage.value = ''
  magiError.value = ''
  try {
    magiMembers.value = await saveMagiMembers(magiMembers.value)
    magiMessage.value = 'MAGIメンバー3席の割当を保存しました。'
  } catch (error) {
    magiError.value = 'MAGI割当を保存できません（' + String(error) + '）。'
  } finally {
    magiBusy.value = false
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
    <section class="pkb-record" aria-label="MAGI設定">
      <h2>MAGI — LLM Profile・メンバー割当</h2>
      <p class="muted small">3席は論理名です。使用モデルと有効席数は同じ設定DBで管理します。</p>
      <div class="command-row">
        <label>接続
          <select v-model="newProfileConnection" aria-label="新規LLM接続">
            <option value="">選択してください</option>
            <option v-for="item in connections.filter(v => v.capabilities?.includes('llm_inference'))"
              :key="item.id" :value="item.id">{{ item.display_name }}</option>
          </select>
        </label>
        <label>モデル名 <input v-model="newModel" aria-label="新規LLMモデル名" /></label>
        <label>Profile名 <input v-model="newProfileName" aria-label="新規LLM Profile名" /></label>
        <button type="button" class="secondary"
          :disabled="magiBusy || !newProfileConnection || !newModel.trim()"
          @click="writeMagiProfile">LLM Profileを登録</button>
      </div>
      <div v-for="member in magiMembers" :key="member.name" class="pkb-record">
        <h3>{{ member.name }}</h3>
        <div class="command-row">
          <label>Profile
            <select v-model="member.profile_id" :aria-label="member.name + ' Profile'">
              <option value="">選択してください</option>
              <option v-for="p in llmProfiles" :key="p.id" :value="p.id">
                {{ p.display_name }} / {{ p.model }}
              </option>
            </select>
          </label>
          <label>重み <input v-model.number="member.weight" type="number" min="0.1" max="10"
            step="0.1" :aria-label="member.name + ' 重み'" /></label>
          <label>Timeout（秒） <input v-model.number="member.timeout_seconds" type="number"
            min="10" max="1800" :aria-label="member.name + ' Timeout'" /></label>
          <label><input v-model="member.enabled" type="checkbox"
            :aria-label="member.name + ' 有効'" /> 有効</label>
          <label><input v-model="member.retry_within_turn" type="checkbox" /> Turn内再試行</label>
        </div>
      </div>
      <button type="button" class="primary-command"
        :disabled="magiBusy || magiMembers.some(m => !m.profile_id)"
        @click="writeMagiAssignments">MAGI割当を保存</button>
      <p v-if="magiError" role="alert" class="error">{{ magiError }}</p>
      <p v-if="magiMessage" role="status">{{ magiMessage }}</p>
    </section>
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
    <section class="card" aria-label="API一覧">
      <h2>API一覧（{{ routes.length }}件）</h2>
      <div v-for="item in routes" :key="item.path + item.methods.join(',')" class="pkb-record">
        <strong>{{ item.methods.join(', ') }}</strong>
        <span> {{ item.path }} </span>
        <span class="muted small">{{ item.name }}</span>
      </div>
    </section>
    <section class="card" aria-label="環境設定状況">
      <h2>環境設定</h2>
      <div v-for="item in environment" :key="item.name" class="pkb-record">
        <strong>{{ item.name }}</strong><span> — {{ item.value }}</span>
      </div>
    </section>
  </template>
</template>
