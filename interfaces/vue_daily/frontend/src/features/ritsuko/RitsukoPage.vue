<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/** RITSUKO専用の画面構成。状態取得と描画を共通Shell・実行制御から分離する。 */
import { legacyPageUrl } from '../../shared/navigation'
import { useTasks } from './useTasks'
import CurrentTask from './components/CurrentTask.vue'
import TaskActivity from './components/TaskActivity.vue'
import TaskSidebar from './components/TaskSidebar.vue'

const {
  items, view, selectedId, trace, selected, connected, refreshing,
  problem, selectTask, selectView,
} = useTasks()
const niceguiCore = legacyPageUrl('/core')
</script>

<template>
  <div class="page-title">
    <h1>RITSUKO — Secretary Core / Orchestrator</h1>
    <div class="connection" :class="{ disconnected: !connected }">
      <span class="dot" />
      {{ connected ? '保存済みTask API接続中' : 'API未接続' }}
    </div>
  </div>
  <p class="read-only-notice">
    <strong>Vue移行中・実Task読取専用</strong>
    保存済みTask一覧・状態・結果を参照できます。依頼開始、回答、停止、Review、MAGI実行はまだ既存画面で操作してください。
  </p>
  <p v-if="problem" class="error" role="alert">{{ problem }}</p>
  <div class="workspace">
    <main class="ritsuko-main">
      <CurrentTask :task="selected" :loading="refreshing" />
      <section class="card request-entry" aria-label="新しい依頼">
        <div class="section-head"><h2>新しい依頼・本人回答</h2></div>
        <p class="muted">
          実Task操作APIの移行後は、この領域から新規受付・本人回答・停止を行えます。
          現在は既存NiceGUIのRITSUKOで操作できます。
        </p>
        <a class="link-button" :href="niceguiCore">既存画面で依頼・回答する</a>
      </section>
      <TaskActivity :trace="trace" />
    </main>
    <TaskSidebar :items="items" :selected-id="selectedId" :view="view"
      @select="selectTask" @filter="selectView"
    />
  </div>
</template>
