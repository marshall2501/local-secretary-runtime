<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** One-origin Vue navigation; capability code lives behind the common API. */
import SecretaryLayout from './app/SecretaryLayout.vue'
import HomePage from './features/home/HomePage.vue'
import RitsukoPage from './features/ritsuko/RitsukoPage.vue'
import PkbPage from './features/pkb/PkbPage.vue'
import FinancePage from './features/finance/FinancePage.vue'
import SystemPage from './features/system/SystemPage.vue'
import SchemaDiagramPage from './features/system/SchemaDiagramPage.vue'

const section = new URLSearchParams(window.location.search).get('page') || 'core'
</script>

<template>
  <SecretaryLayout>
    <HomePage v-if="section === 'home' || section === 'features'" :features="section === 'features'" />
    <PkbPage v-else-if="section === 'pkb'" />
    <FinancePage v-else-if="section === 'finance'" />
    <SystemPage v-else-if="section === 'service-billing'" section="service-billing" />
    <SystemPage v-else-if="section === 'settings'" section="settings" />
    <SystemPage v-else-if="section === 'debug'" section="debug" />
    <SchemaDiagramPage v-else-if="section === 'er'" />
    <RitsukoPage v-else />
  </SecretaryLayout>
</template>
