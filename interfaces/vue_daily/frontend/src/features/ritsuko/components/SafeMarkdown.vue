<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/**
 * MAGI/Task本文の限定Markdown表示。LLMの文字列をHTMLとして注入しない。
 * 強調・インラインコード・コードブロック・段落・箇条書きをVueテキストノードへ変換する。
 */
import { computed } from 'vue'

const props = defineProps<{ text: string }>()
type Segment = { kind: 'plain' | 'bold' | 'code'; text: string }
type Block = { kind: 'paragraph' | 'code' | 'list'; lines: Segment[][]; text: string }
function inlineSegments(value: string): Segment[] {
  const segments: Segment[] = []
  const pattern = /\*\*([^*\n]+)\*\*|`([^`\n]+)`/g
  let cursor = 0
  for (const match of value.matchAll(pattern)) {
    const pos = match.index ?? cursor
    if (pos > cursor) segments.push({ kind: 'plain', text: value.slice(cursor, pos) })
    segments.push({ kind: match[1] === undefined ? 'code' : 'bold',
      text: match[1] ?? match[2] ?? '' })
    cursor = pos + match[0].length
  }
  if (cursor < value.length || segments.length === 0) {
    segments.push({ kind: 'plain', text: value.slice(cursor) })
  }
  return segments
}
const blocks = computed<Block[]>(() => {
  const result: Block[] = []
  const lines = props.text.replace(/\r\n?/g, '\n').split('\n')
  let paragraph: string[] = []
  function pushParagraph(): void {
    if (paragraph.length) {
      result.push({ kind: 'paragraph', lines: [inlineSegments(paragraph.join('\n'))], text: '' })
      paragraph = []
    }
  }
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (line.trimStart().startsWith('```')) {
      pushParagraph()
      const code: string[] = []
      while (++i < lines.length && !lines[i].trimStart().startsWith('```')) code.push(lines[i])
      result.push({ kind: 'code', lines: [], text: code.join('\n') })
    } else if (/^\s*[-*] +/.test(line)) {
      pushParagraph()
      const items = [line.replace(/^\s*[-*] +/, '')]
      while (i + 1 < lines.length && /^\s*[-*] +/.test(lines[i + 1])) {
        items.push(lines[++i].replace(/^\s*[-*] +/, ''))
      }
      result.push({ kind: 'list', lines: items.map(inlineSegments), text: '' })
    } else if (!line.trim()) {
      pushParagraph()
    } else {
      paragraph.push(line)
    }
  }
  pushParagraph()
  return result
})
</script>

<template>
  <div class="safe-markdown">
    <template v-for="(block, blockIndex) in blocks" :key="blockIndex">
      <pre v-if="block.kind === 'code'"><code>{{ block.text }}</code></pre>
      <ul v-else-if="block.kind === 'list'">
        <li v-for="(line, lineIndex) in block.lines" :key="lineIndex">
          <template v-for="(part, partIndex) in line" :key="partIndex">
            <strong v-if="part.kind === 'bold'">{{ part.text }}</strong>
            <code v-else-if="part.kind === 'code'">{{ part.text }}</code>
            <span v-else>{{ part.text }}</span>
          </template>
        </li>
      </ul>
      <p v-else>
        <template v-for="(part, partIndex) in block.lines[0]" :key="partIndex">
          <strong v-if="part.kind === 'bold'">{{ part.text }}</strong>
          <code v-else-if="part.kind === 'code'">{{ part.text }}</code>
          <span v-else>{{ part.text }}</span>
        </template>
      </p>
    </template>
  </div>
</template>

<style scoped>
.safe-markdown { line-height: 1.65; overflow-wrap: anywhere; }
.safe-markdown p { white-space: pre-wrap; margin: 0 0 9px; }
.safe-markdown p:last-child { margin-bottom: 0; }
.safe-markdown ul { margin: 4px 0 10px; padding-left: 24px; }
.safe-markdown pre { overflow-x: auto; padding: 12px; border-radius: 7px;
  background: #f3f6fa; white-space: pre; }
.safe-markdown code { font-size: .9em; }
</style>
