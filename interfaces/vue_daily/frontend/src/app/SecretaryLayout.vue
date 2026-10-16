<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** 全画面の共通ナビ。未移行機能は既存NiceGUI画面へつなぐ。 */
import { legacyPageUrl, navigationItems } from '../shared/navigation'
</script>

<template>
  <div class="shell">
    <header class="site-header">
      <span class="eyebrow">Personal Local Secretary AI</span>
      <nav class="site-nav" aria-label="秘書AIの機能">
        <a v-for="item in navigationItems" :key="item.path"
          :href="item.current ? '/ui/' : legacyPageUrl(item.path)"
          :aria-current="item.current ? 'page' : undefined"
          :title="item.current ? 'Vue日常UI' : '既存NiceGUI画面で開きます'"
          :class="{ 'nav-active': item.current }"
        >{{ item.label }}<small v-if="!item.current" class="nav-legacy">既存</small></a>
      </nav>
    </header>
    <slot />
  </div>
</template>
