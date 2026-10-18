<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** Read-only Entity catalog and detailed State/History/Relations from the common PKB API. */
import { computed, onMounted, ref } from 'vue'
import { readLayoutPreferences } from '../../shared/layoutPreferences'
import type { LayoutPreferences } from '../../shared/layoutPreferences'
import { listEntities, readEntity } from './api'
import type { EntityCatalog, EntityDetail, EntityFact } from './api'

type Section = 'overview' | 'history' | 'relations' | 'sources'
const params = new URLSearchParams(window.location.search)
const entityId = params.get('id') || ''
const loading = ref(false)
const problem = ref('')
const catalog = ref<EntityCatalog | null>(null)
const detail = ref<EntityDetail | null>(null)
const selected = ref<Section>('overview')
const layout = ref<LayoutPreferences | null>(null)
const tabs = computed(() => (['overview', 'history', 'relations', 'sources'] as Section[])
  .filter(key => layout.value?.visibility.entity[key] !== false))
const sources = computed(() => {
  const found = new Map<string, Set<string>>()
  const data = detail.value
  if (!data) return []
  for (const [section, rows] of [
    ['現在値', data.current], ['履歴', data.history],
    ['Event', data.events], ['関連', data.relations],
  ] as const) {
    for (const row of rows) {
      const uri = row.source_uri?.trim()
      if (!uri) continue
      if (!found.has(uri)) found.set(uri, new Set())
      found.get(uri)?.add(section)
    }
  }
  return [...found.entries()].map(([uri, sections]) => ({
    uri, sections: [...sections].join(' / '),
  }))
})
const search = ref('')

function entityLink(id: string): string {
  return '/ui/?page=entities&id=' + encodeURIComponent(id)
}
function valueText(value: unknown): string {
  if (value == null) return '—'
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}
function sourceLink(uri: string | null | undefined): string {
  return uri && /^https?:\/\//i.test(uri) ? uri : ''
}

/** Every request uses existing bounded read-only API contracts. */
async function load(offset = 0): Promise<void> {
  if (loading.value) return
  loading.value = true
  problem.value = ''
  try {
    if (entityId) {
      detail.value = await readEntity(entityId)
    } else {
      catalog.value = await listEntities(100, offset)
    }
  } catch (error) {
    problem.value = 'Entity情報を取得できません（' + String(error) + '）。'
  } finally {
    loading.value = false
  }
}
onMounted(() => {
  void readLayoutPreferences().then(value => {
    layout.value = value
    const requested = value.entity.default_tab as Section
    selected.value = tabs.value.includes(requested) ? requested : tabs.value[0] || 'overview'
  }).catch(() => { /* The data viewer remains usable with legacy defaults. */ })
  void load()
})
</script>

<template>
  <div class="page-title">
    <div>
      <h1>{{ entityId ? 'Entity詳細' : 'Entity一覧' }}</h1>
      <p class="muted small">PKBの現在値・変更履歴・関係・出典を同じSecretary Serverで確認します。</p>
    </div>
    <span class="muted small">読み取り専用</span>
  </div>
  <p><a href="/ui/?page=pkb">← PKBへ戻る</a></p>
  <p v-if="problem" role="alert" class="error">{{ problem }}</p>
  <p v-if="loading" role="status">読み込み中…</p>

  <template v-if="!entityId && catalog">
    <section class="card" aria-label="Entity一覧">
      <div class="section-head">
        <h2>登録済みEntity（{{ catalog.total }}件）</h2>
        <button class="secondary" type="button" :disabled="loading" @click="load(catalog.offset)">更新</button>
      </div>
      <label class="entity-filter">一覧内を絞り込む
        <input v-model="search" aria-label="Entityの名前で絞り込む" placeholder="名前・ドメイン・種類" />
      </label>
      <p v-if="!catalog.total" class="muted">登録済みEntityはありません。</p>
      <div class="entity-grid">
        <a v-for="item in catalog.items.filter(v => (
          v.name + ' ' + v.domain + ' ' + v.entity_type
        ).toLowerCase().includes(search.toLowerCase()))" :key="item.id"
          :href="entityLink(item.id)" class="entity-card">
          <strong>{{ item.name }}</strong>
          <span>{{ item.domain }} / {{ item.entity_type }}</span>
          <small>現在値・履歴・関連を確認 →</small>
        </a>
      </div>
      <div class="command-row">
        <button class="secondary" type="button" :disabled="loading || catalog.offset === 0"
          @click="load(Math.max(0, catalog.offset - catalog.limit))">前へ</button>
        <span class="muted small">{{ catalog.offset + (catalog.items.length ? 1 : 0) }}〜{{ catalog.offset + catalog.items.length }} / {{ catalog.total }}</span>
        <button class="secondary" type="button" :disabled="loading || catalog.offset + catalog.items.length >= catalog.total"
          @click="load(catalog.offset + catalog.limit)">次へ</button>
      </div>
    </section>
  </template>

  <template v-if="entityId && detail">
    <section class="card entity-head" aria-label="Entity概要">
      <div class="section-head">
        <div>
          <h2>{{ detail.entity.name }}</h2>
          <p class="muted small">{{ detail.entity.domain }} / {{ detail.entity.entity_type }}</p>
        </div>
        <span class="status">{{ detail.current.length }}件の現在値</span>
      </div>
      <p class="muted small">Entity ID: <code>{{ detail.entity.id }}</code></p>
    </section>
    <section class="card" aria-label="Entityの記録">
      <div class="entity-tabs" role="tablist" aria-label="Entityの表示">
        <button v-for="tab in tabs" :key="tab"
          type="button" role="tab" :aria-selected="selected === tab" @click="selected = tab">
          {{ tab === 'overview' ? '現在値' : tab === 'history' ? '変更履歴・Event' :
            tab === 'relations' ? '関連Entity' : '出典' }}
        </button>
      </div>
      <template v-if="selected === 'overview'">
        <p v-if="!detail.current.length" class="muted">現在値として表示できるState・Attributeはありません。</p>
        <div v-for="fact in detail.current" :key="fact.id" class="entity-fact">
          <div><strong>{{ fact.predicate }}</strong><span class="status">{{ fact.semantic_kind || '現在値' }}</span></div>
          <p>{{ valueText(fact.value) }}</p>
          <small>有効開始：{{ fact.valid_from }} / {{ fact.verification_status || '—' }}</small>
          <p v-if="fact.source_uri" class="muted small">出典：
            <a v-if="sourceLink(fact.source_uri)" :href="sourceLink(fact.source_uri)"
              target="_blank" rel="noopener noreferrer">{{ fact.source_uri }}</a>
            <span v-else>{{ fact.source_uri }}</span>
          </p>
        </div>
      </template>
      <template v-if="selected === 'history'">
        <p v-if="!detail.history.length && !detail.events.length" class="muted">履歴・Eventはありません。</p>
        <div v-for="fact in ([...detail.events.map(v => ({ ...v, semantic_kind: 'event' })),
          ...detail.history] as EntityFact[])" :key="fact.id" class="entity-fact">
          <div><strong>{{ fact.predicate }}</strong><span class="status">{{ fact.semantic_kind }}</span></div>
          <p>{{ valueText(fact.value) }}</p>
          <small>{{ fact.valid_from }} {{ fact.valid_to ? '〜 ' + fact.valid_to : '' }}</small>
          <p v-if="fact.source_uri" class="muted small">出典：{{ fact.source_uri }}</p>
        </div>
      </template>
      <template v-if="selected === 'relations'">
        <p v-if="!detail.relations.length" class="muted">関連Entityはありません。</p>
        <div v-for="relation in detail.relations" :key="relation.id" class="entity-fact">
          <div><strong>{{ relation.predicate }}</strong>
            <span class="status">{{ relation.direction === 'outgoing' ? 'このEntityから' : 'このEntityへ' }}</span></div>
          <p><a :href="entityLink(relation.other_entity_id)">{{ relation.other_entity_name }}</a>
            <span class="muted small"> / {{ relation.other_entity_type }}</span></p>
          <small>{{ relation.relation_role || '—' }} / {{ relation.valid_from }}</small>
          <p v-if="relation.source_uri" class="muted small">出典：{{ relation.source_uri }}</p>
        </div>
      </template>
      <template v-if="selected === 'sources'">
        <p v-if="!sources.length" class="muted">参照できる出典はありません。</p>
        <div v-for="item in sources" :key="item.uri" class="entity-fact">
          <strong>{{ item.sections }}</strong>
          <p v-if="sourceLink(item.uri)"><a :href="sourceLink(item.uri)"
            target="_blank" rel="noopener noreferrer">{{ item.uri }}</a></p>
          <p v-else class="muted">{{ item.uri }}</p>
        </div>
      </template>
    </section>
  </template>
</template>

<style scoped>
.page-title p { margin:3px 0 0; }
.entity-filter { display:flex; flex-direction:column; gap:5px; max-width:370px; font-size:13px; font-weight:650; margin:14px 0; }
.entity-filter input { border:1px solid #aac2d0; border-radius:7px; padding:8px 10px; }
.entity-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:10px; margin:16px 0; }
.entity-card { display:flex; flex-direction:column; gap:5px; border:1px solid #d3e4eb; border-radius:8px; padding:13px 15px; text-decoration:none; background:#f9fcfe; min-width:0; }
.entity-card:hover { background:#edf8fa; border-color:#78b7bd; }
.entity-card span,.entity-card small { color:#5b6f83; }
.entity-card strong { overflow-wrap:anywhere; }
.entity-head { background:#f0f8fe; border-color:#add0ed; margin-bottom:14px; }
.entity-head p { margin:8px 0 0; }
.entity-tabs { display:flex; flex-wrap:wrap; gap:6px; border-bottom:1px solid #d7e4ec; padding:0 0 10px; margin-bottom:16px; }
.entity-tabs button { border:1px solid #aec2d0; border-radius:7px; padding:8px 13px; background:#fff; font-weight:700; color:#425770; }
.entity-tabs button[aria-selected="true"] { color:#076d63; background:#e4f6f1; border-color:#72bab0; }
.entity-fact { border-top:1px solid #e2e9ef; padding:13px 0; overflow-wrap:anywhere; }
.entity-fact > div { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
.entity-fact p { margin:7px 0; white-space:pre-wrap; }
.entity-fact small { color:#607287; }
</style>
