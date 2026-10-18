<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** Entry to the available Secretary capabilities, always on the same origin. */
import { navigationItems } from '../../shared/navigation'
defineProps<{ features?: boolean }>()
</script>

<template>
  <div class="page-title">
    <h1>{{ features ? '機能一覧' : 'Personal Local Secretary AI' }}</h1>
    <span class="muted small">Vue・共通Web API</span>
  </div>
  <section class="card" aria-label="秘書AIの機能一覧">
    <h2>秘書AIの機能</h2>
    <div v-for="item in navigationItems" :key="item.path" class="pkb-record">
      <a :href="item.path === '/' ? '/ui/' : '/ui/?page=' + item.path.slice(1)">
        <strong>{{ item.label }}</strong>
      </a>
    </div>
  </section>
</template>
