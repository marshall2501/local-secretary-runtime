<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** Unified Secretary navigation: every feature stays on this web server. */
import { navigationItems } from '../shared/navigation'
const selected = new URLSearchParams(window.location.search).get('page') || 'core'
</script>

<template>
  <div class="shell">
    <header class="site-header">
      <span class="eyebrow">Personal Local Secretary AI</span>
      <nav class="site-nav" aria-label="秘書AIの機能">
        <a v-for="item in navigationItems" :key="item.path"
          :href="item.path === '/core' ? '/ui/' : '/ui/?page=' + item.path.slice(1)"
          :aria-current="item.path === '/' + selected ? 'page' : undefined"
          :title="'Secretary Vue — ' + item.label"
          :class="{ 'nav-active': item.path === '/' + selected }"
        >{{ item.label }}</a>
      </nav>
    </header>
    <slot />
  </div>
</template>
