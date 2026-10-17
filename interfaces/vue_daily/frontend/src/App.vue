<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** 現段階のRITSUKO画面を共通レイアウトへ載せる。業務処理やAPI通信は置かない。 */
import SecretaryLayout from './app/SecretaryLayout.vue'
import RitsukoPage from './features/ritsuko/RitsukoPage.vue'
import PkbPage from './features/pkb/PkbPage.vue'

const section = new URLSearchParams(window.location.search).get('page')
</script>

<template>
  <SecretaryLayout>
    <PkbPage v-if="section === 'pkb'" />
    <RitsukoPage v-else />
  </SecretaryLayout>
</template>
