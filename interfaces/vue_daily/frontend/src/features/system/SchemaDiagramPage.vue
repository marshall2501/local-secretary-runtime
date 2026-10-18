<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** Standalone schema diagram view: all data is read-only PostgreSQL metadata. */
import { onMounted, ref } from 'vue'
import { readSchemaDiagram } from './api'
import type { SchemaDiagramResult } from './api'

const keysOnly = ref(true)
const loading = ref(false)
const error = ref('')
const diagram = ref<SchemaDiagramResult | null>(null)
const mermaidSource = ref('')
const showMermaid = ref(false)
const fitWidth = ref(true)
const zoom = ref(100)

/** Only user-requested refresh causes DB schema introspection. */
async function refresh(): Promise<void> {
  if (loading.value) return
  loading.value = true
  error.value = ''
  diagram.value = null
  showMermaid.value = false
  mermaidSource.value = ''
  try {
    diagram.value = await readSchemaDiagram(keysOnly.value)
  } catch (reason) {
    error.value = 'ER図を生成できません（' + String(reason) + '）。'
  } finally {
    loading.value = false
  }
}

/** SVG is produced entirely by the server's escaped schema metadata renderer. */
async function toggleMermaid(): Promise<void> {
  if (showMermaid.value) {
    showMermaid.value = false
    return
  }
  if (!mermaidSource.value) {
    try {
      mermaidSource.value = (await readSchemaDiagram(keysOnly.value, 'mermaid')).content
    } catch (reason) {
      error.value = 'Mermaidソースを取得できません（' + String(reason) + '）。'
      return
    }
  }
  showMermaid.value = true
}

/** Download the current render; this does not create a file on the server. */
function saveSvg(): void {
  if (!diagram.value || diagram.value.output_format !== 'svg') return
  const blob = new Blob([diagram.value.content], { type: 'image/svg+xml;charset=utf-8' })
  const address = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = address
  link.download = 'secretary-er-' + (keysOnly.value ? 'keys' : 'all') + '.svg'
  link.click()
  URL.revokeObjectURL(address)
}
function zoomBy(delta: number): void {
  fitWidth.value = false
  zoom.value = Math.max(50, Math.min(250, zoom.value + delta))
}
onMounted(() => { void refresh() })
</script>

<template>
  <div class="page-title">
    <h1>DB ER図</h1>
    <span class="muted small">PostgreSQL schema metadata ／ 読み取り専用</span>
  </div>
  <p><a href="/ui/?page=debug">← システム診断へ戻る</a></p>
  <section class="card" aria-label="ER図の表示設定">
    <div class="section-head">
      <h2>テーブル・PK・FKの構造</h2>
      <button class="primary-command" type="button" :disabled="loading" @click="refresh">
        {{ loading ? '生成中…' : 'ER図を生成・更新' }}
      </button>
    </div>
    <div class="command-row">
      <label>
        表示するカラム
        <select v-model="keysOnly" aria-label="ER図の表示密度" @change="refresh">
          <option :value="true">PK・FK中心</option>
          <option :value="false">全カラム</option>
        </select>
      </label>
      <button class="secondary" type="button" :disabled="!diagram" @click="zoomBy(-25)">－</button>
      <button class="secondary" type="button" :disabled="!diagram" @click="fitWidth = false; zoom = 100">100%</button>
      <button class="secondary" type="button" :disabled="!diagram" @click="zoomBy(25)">＋</button>
      <button class="secondary" type="button" :disabled="!diagram" @click="fitWidth = true">全体</button>
      <button class="secondary" type="button" :disabled="!diagram" @click="saveSvg">SVGを保存</button>
      <button class="secondary" type="button" :disabled="!diagram" @click="toggleMermaid">
        {{ showMermaid ? 'Mermaidを閉じる' : 'Mermaidソース' }}
      </button>
    </div>
    <p class="muted small">図はDBの現在の定義から生成されます。データ行は参照しません。線はForeign Keyを表します。</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <template v-if="diagram">
      <div class="diagram-summary" role="status">
        <strong>{{ diagram.database }} / {{ diagram.schema }}</strong>
        <span>テーブル {{ diagram.table_count }}件</span>
        <span>FK {{ diagram.relation_count }}本</span>
        <span>{{ diagram.duration_ms }}ms</span>
        <span v-if="!fitWidth">{{ zoom }}%</span>
      </div>
      <div class="diagram-viewport" aria-label="DB ER図の可視化">
        <div class="diagram-render" :style="{ width: fitWidth ? '100%' : (1526 * zoom / 100) + 'px' }"
          v-html="diagram.content"></div>
      </div>
      <div v-if="showMermaid" class="mermaid-source">
        <h3>Mermaid ERソース</h3>
        <pre>{{ mermaidSource }}</pre>
      </div>
    </template>
  </section>
</template>

<style scoped>
.diagram-summary { display:flex; gap:1rem; align-items:center; flex-wrap:wrap; margin:0.75rem 0; }
.diagram-summary span { color:#475569; font-size:.9rem; }
.diagram-viewport { min-height:200px; max-height:72vh; overflow:auto; border:1px solid #cbd5e1; border-radius:10px; background:#f8fafc; }
.diagram-render { min-width:640px; }
.diagram-render :deep(svg) { display:block; width:100%; height:auto; }
.mermaid-source { max-height:28rem; overflow:auto; }
.mermaid-source pre { overflow:auto; padding:1rem; border-radius:8px; background:#f1f5f9; font-size:.78rem; }
</style>
