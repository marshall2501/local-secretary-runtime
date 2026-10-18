<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** Read-only ER viewer. Mermaid is the same graph layout engine used by the former NiceGUI view. */
import { onMounted, ref } from 'vue'
import mermaid from 'mermaid'
import { readSchemaDiagram } from './api'
import type { SchemaDiagramResult } from './api'

const keysOnly = ref(true)
const loading = ref(false)
const error = ref('')
const diagram = ref<SchemaDiagramResult | null>(null)
const renderedSvg = ref('')
const source = ref('')
const showSource = ref(false)
const fitWidth = ref(false)
const zoom = ref(100)
const intrinsicWidth = ref(1600)
const viewport = ref<HTMLElement | null>(null)
let renderNumber = 0

// The graph is rendered locally in the browser: no second server or CDN.
mermaid.initialize({
  startOnLoad: false,
  securityLevel: 'strict',
  theme: 'default',
  er: { useMaxWidth: false },
})

/** Refresh only from real schema metadata, retaining a legible graph scale by default. */
async function refresh(): Promise<void> {
  if (loading.value) return
  loading.value = true
  error.value = ''
  diagram.value = null
  renderedSvg.value = ''
  try {
    const next = await readSchemaDiagram(keysOnly.value, 'mermaid')
    if (next.status !== 'ok' || !next.content.trim()) {
      throw new Error('Mermaid ERソースを取得できませんでした')
    }
    // The former NiceGUI drew this same Mermaid source, rather than an
    // alphabetically sorted four-column approximation of the relationship graph.
    const rendered = await mermaid.render('secretary-db-er-' + (++renderNumber), next.content)
    const box = rendered.svg.match(/viewBox=["']([^"']+)["']/i)
    const width = box?.[1]?.trim().split(/[\s,]+/).map(Number)[2]
    intrinsicWidth.value = width && Number.isFinite(width) && width > 0 ? width : 1600
    source.value = next.content
    diagram.value = next
    renderedSvg.value = rendered.svg
  } catch (reason) {
    error.value = 'ER図の描画に失敗しました（' + String(reason) + '）。'
  } finally {
    loading.value = false
  }
}

function zoomBy(delta: number): void {
  fitWidth.value = false
  zoom.value = Math.min(300, Math.max(50, zoom.value + delta))
}

function fullScreen(): void {
  if (viewport.value?.requestFullscreen) {
    void viewport.value.requestFullscreen()
  }
}

/** Download the actual Mermaid-rendered SVG visible in the viewer. */
function saveSvg(): void {
  if (!renderedSvg.value) return
  const blob = new Blob([renderedSvg.value], { type: 'image/svg+xml;charset=utf-8' })
  const address = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = address
  link.download = 'secretary-er-' + (keysOnly.value ? 'keys' : 'all') + '.svg'
  link.click()
  URL.revokeObjectURL(address)
}

onMounted(() => { void refresh() })
</script>

<template>
  <div class="page-title">
    <div>
      <h1>DB ER図</h1>
      <p class="muted small">接続中のPostgreSQL schemaから、テーブルとPK・FKの関係を表示します。</p>
    </div>
    <span class="muted small">読み取り専用 / Mermaid</span>
  </div>
  <p><a href="/ui/?page=debug">← システム診断へ戻る</a></p>

  <section class="card" aria-label="ER図の表示設定">
    <div class="section-head">
      <h2>テーブル間の関係</h2>
      <button class="primary-command" type="button" :disabled="loading" @click="refresh">
        {{ loading ? '描画中…' : 'ER図を生成・更新' }}
      </button>
    </div>
    <div class="command-row diagram-controls">
      <label>表示するカラム
        <select v-model="keysOnly" aria-label="ER図の表示密度" @change="refresh">
          <option :value="true">PK・FK中心</option>
          <option :value="false">全カラム</option>
        </select>
      </label>
      <span class="control-separator" aria-hidden="true"></span>
      <button class="secondary" type="button" :disabled="!renderedSvg" @click="zoomBy(-25)">－</button>
      <button class="secondary" type="button" :disabled="!renderedSvg" @click="fitWidth = false; zoom = 100">100%</button>
      <button class="secondary" type="button" :disabled="!renderedSvg" @click="zoomBy(25)">＋</button>
      <button class="secondary" type="button" :disabled="!renderedSvg" @click="fitWidth = true">全体</button>
      <button class="secondary" type="button" :disabled="!renderedSvg" @click="fullScreen">全画面</button>
      <button class="secondary" type="button" :disabled="!renderedSvg" @click="saveSvg">SVGを保存</button>
      <button class="secondary" type="button" :disabled="!renderedSvg" @click="showSource = !showSource">
        {{ showSource ? 'Mermaidを閉じる' : 'Mermaidソース' }}
      </button>
    </div>
    <p class="muted small">通常は100%でスクロールして閲覧します。「全体」は俯瞰用です（文字が小さくなります）。全画面はEscで解除できます。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="loading" role="status">現在のDB定義からER図を生成しています…</p>

    <template v-if="diagram && renderedSvg">
      <div class="diagram-summary" role="status">
        <strong>{{ diagram.database }} / {{ diagram.schema }}</strong>
        <span>テーブル {{ diagram.table_count }}件</span>
        <span>FK {{ diagram.relation_count }}本</span>
        <span>生成 {{ diagram.duration_ms }}ms</span>
        <span>{{ fitWidth ? '全体表示' : zoom + '%' }}</span>
      </div>
      <div ref="viewport" class="diagram-viewport" aria-label="DB ER図の可視化">
        <div class="diagram-render" :style="{
          width: fitWidth ? '100%' : Math.round(intrinsicWidth * zoom / 100) + 'px'
        }" v-html="renderedSvg"></div>
      </div>
      <details class="diagram-source" :open="showSource">
        <summary @click.prevent="showSource = !showSource">Mermaid ERソース</summary>
        <pre>{{ source }}</pre>
      </details>
    </template>
  </section>
</template>

<style scoped>
.page-title p { margin:3px 0 0; }
.diagram-controls { gap:8px; }
.control-separator { width:1px; height:26px; background:#d4e0e9; margin:0 3px; }
.diagram-summary { display:flex; gap:8px 18px; align-items:center; flex-wrap:wrap; padding:6px 0 12px; }
.diagram-summary span { color:#475569; font-size:.9rem; }
.diagram-viewport {
  height:72vh; min-height:520px; overflow:auto; overscroll-behavior:contain;
  border:1px solid #b7c9d8; border-radius:8px; background:#fff;
  padding:8px;
}
.diagram-viewport:fullscreen { height:100vh; width:100vw; border-radius:0; }
.diagram-render { min-width:640px; min-height:100px; }
.diagram-render :deep(svg) { display:block; width:100% !important; max-width:none !important; height:auto !important; }
.diagram-source { margin-top:12px; border-top:1px solid #e2e8f0; padding:10px 0 0; }
.diagram-source summary { cursor:pointer; font-weight:650; }
.diagram-source pre { white-space:pre; max-height:30rem; overflow:auto; background:#f1f5f9; padding:12px; border-radius:6px; font-size:12px; }
@media(max-width:680px) { .diagram-viewport { height:64vh; min-height:360px; } }
</style>
