<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** Shared three-seat MAGI editor. RITSUKO and Settings use the same business API. */
import { onMounted, ref } from 'vue'
import {
  readConnections, readMagiProfiles, readMagiMembers,
  saveMagiProfile, saveMagiMembers,
} from './api'
import type { ConnectionSummary, LlmProfile, MagiMember } from './api'

const connections = ref<ConnectionSummary[]>([])
const profiles = ref<LlmProfile[]>([])
const members = ref<MagiMember[]>([])
const busy = ref(false)
const fetching = ref(false)
const problem = ref('')
const notice = ref('')
const newModel = ref('')
const newProfileName = ref('')
const newProfileConnection = ref('')

async function load(): Promise<void> {
  if (fetching.value) return
  fetching.value = true
  problem.value = ''
  try {
    const [nextConnections, nextProfiles, nextMembers] = await Promise.all([
      readConnections(), readMagiProfiles(), readMagiMembers(),
    ])
    connections.value = nextConnections
    profiles.value = nextProfiles
    const byName = new Map(nextMembers.map(item => [item.name, item]))
    members.value = (['MELCHIOR','CASPER','BALTHASAR'] as const).map(name =>
      byName.get(name) || { name, profile_id: '', weight: 1,
        timeout_seconds: 120, retry_within_turn: false, enabled: false })
  } catch (error) {
    problem.value = 'MAGI設定を取得できません（' + String(error) + '）。'
  } finally {
    fetching.value = false
  }
}
async function createProfile(): Promise<void> {
  if (busy.value || !newProfileConnection.value || !newModel.value.trim()) return
  busy.value = true
  notice.value = ''
  problem.value = ''
  try {
    const profile = await saveMagiProfile({
      connection_id: newProfileConnection.value, model: newModel.value,
      display_name: newProfileName.value || newModel.value, enabled: true,
    })
    notice.value = 'LLM Profile登録：' + profile.display_name
    profiles.value = await readMagiProfiles()
    newModel.value = ''
    newProfileName.value = ''
  } catch (error) {
    problem.value = 'LLM Profileの登録に失敗しました（' + String(error) + '）。'
  } finally {
    busy.value = false
  }
}
async function saveMembers(): Promise<void> {
  if (busy.value || members.value.some(m => !m.profile_id)) return
  busy.value = true
  notice.value = ''
  problem.value = ''
  try {
    members.value = await saveMagiMembers(members.value)
    notice.value = 'MAGIの3席を保存しました。新規Taskから反映されます。'
  } catch (error) {
    problem.value = 'MAGI割当を保存できません（' + String(error) + '）。'
  } finally {
    busy.value = false
  }
}
onMounted(() => { void load() })
</script>

<template>
  <section class="card magi-settings" aria-label="MAGIメンバー設定">
    <div class="section-head">
      <div>
        <h2>MAGI member configuration</h2>
        <p class="muted small">3席は論理名です。モデル・重み・Timeoutと有効状態を同じDBで管理します。</p>
      </div>
      <button class="secondary" type="button" :disabled="busy || fetching" @click="load">更新</button>
    </div>
    <p v-if="problem" class="error" role="alert">{{ problem }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="fetching" role="status">MAGI設定を読み込み中…</p>
    <div class="magi-config-grid">
      <article v-for="member in members" :key="member.name" class="magi-config-card">
        <div class="section-head">
          <h3>{{ member.name }}</h3>
          <label class="toggle"><input v-model="member.enabled" type="checkbox"
            :aria-label="member.name + ' 有効'" /> 有効</label>
        </div>
        <label>LLM Profile
          <select v-model="member.profile_id" :aria-label="member.name + ' Profile'">
            <option value="">選択してください</option>
            <option v-for="item in profiles" :key="item.id" :value="item.id">
              {{ item.display_name }} / {{ item.model }}
            </option>
          </select>
        </label>
        <div class="member-numbers">
          <label>Weight
            <input v-model.number="member.weight" :aria-label="member.name + ' 重み'"
              type="number" step="0.1" min="0.1" max="10" />
          </label>
          <label>Timeout（秒）
            <input v-model.number="member.timeout_seconds" :aria-label="member.name + ' Timeout'"
              type="number" step="1" min="10" max="1800" />
          </label>
        </div>
        <label class="toggle"><input v-model="member.retry_within_turn" type="checkbox" />
          Turn内リトライ</label>
      </article>
    </div>
    <div class="command-row">
      <button class="primary-command" type="button" :disabled="busy || fetching || members.length !== 3 ||
        members.some(m => !m.profile_id)" @click="saveMembers">LLM設定を保存</button>
    </div>
    <details class="profile-editor">
      <summary>LLM Profileの追加・確認</summary>
      <div class="profile-fields">
        <label>接続
          <select v-model="newProfileConnection" aria-label="新規LLM接続">
            <option value="">接続を選択してください</option>
            <option v-for="item in connections.filter(v => v.capabilities?.includes('llm_inference'))"
              :key="item.id" :value="item.id">{{ item.display_name }}</option>
          </select>
        </label>
        <label>モデル名 <input v-model="newModel" aria-label="新規LLMモデル名" maxlength="200" /></label>
        <label>Profile表示名 <input v-model="newProfileName" aria-label="新規LLM Profile名" maxlength="200" /></label>
      </div>
      <button class="secondary" type="button" :disabled="busy || !newProfileConnection || !newModel.trim()"
        @click="createProfile">LLM Profileを追加</button>
    </details>
  </section>
</template>

<style scoped>
.magi-settings { background:#f0fbf8; border-color:#9edbd0; }
.magi-settings p { margin:4px 0 0; }
.magi-config-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:14px 0; }
.magi-config-card { background:#fff; border:1px solid #afd9d2; border-radius:8px; padding:14px; min-width:0; }
.magi-config-card h3 { margin:0; font-size:13px; }
.magi-config-card label { display:flex; flex-direction:column; font-size:12px; font-weight:650; gap:5px; margin:9px 0; }
.magi-config-card .toggle { flex-direction:row; align-items:center; gap:7px; }
.magi-config-card input:not([type=checkbox]),.magi-config-card select { width:100%; min-width:0; padding:8px; border:1px solid #a7becd; border-radius:6px; }
.member-numbers { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.profile-editor { border-top:1px solid #c2dcd8; margin-top:12px; padding-top:9px; }
.profile-editor summary { cursor:pointer; font-weight:650; }
.profile-fields { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin:12px 0; }
.profile-fields label { display:flex; flex-direction:column; gap:5px; font-size:12px; }
.profile-fields input,.profile-fields select { min-width:0; padding:8px; border:1px solid #a7becd; border-radius:6px; }
@media(max-width:840px) { .magi-config-grid { grid-template-columns:1fr; } }
</style>
