<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** Vue日常画面の共通ヘッダー。Vue未移行の機能は稼働中のNiceGUIで開く。 */
import { legacyPageUrl, navigationItems } from '../shared/navigation'

const pkbSelected = new URLSearchParams(window.location.search).get('page') === 'pkb'
</script>

<template>
  <div class="shell">
    <header class="site-header">
      <span class="eyebrow">Personal Local Secretary AI</span>
      <nav class="site-nav" aria-label="秘書AIの機能">
        <a v-for="item in navigationItems" :key="item.path"
          :href="item.path === '/pkb' ? '/ui/?page=pkb' : item.current ? '/ui/' : legacyPageUrl(item.path)"
          :aria-current="(item.current && !pkbSelected) || (item.path === '/pkb' && pkbSelected) ? 'page' : undefined"
          :title="item.current || item.path === '/pkb' ? 'Vue日常UI' : '既存NiceGUI画面で開きます'"
          :class="{ 'nav-active': (item.current && !pkbSelected) || (item.path === '/pkb' && pkbSelected) }"
        >{{ item.label }}<small v-if="!item.current && item.path !== '/pkb'" class="nav-legacy">既存</small></a>
      </nav>
    </header>
    <slot />
  </div>
</template>
