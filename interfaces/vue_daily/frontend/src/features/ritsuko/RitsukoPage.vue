<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/**
 * RITSUKO画面の中央に保存済みTask・操作・履歴、右にTask一覧を置く。
 * 実Task操作はcommands_enabledのときだけ既存APIへ配送する。
 */
import { useTasks } from './useTasks'
import CurrentTask from './components/CurrentTask.vue'
import TaskCommandPanel from './components/TaskCommandPanel.vue'
import MagiProgress from './components/MagiProgress.vue'
import TaskActivity from './components/TaskActivity.vue'
import TaskSidebar from './components/TaskSidebar.vue'

const {
  items, view, hasMore, selectedId, trace, selected, connected, refreshing,
  problem, selectTask, selectView, loadMore,
  commandsEnabled, commandBusy, commandFeedback, commandError, operation,
  draftRequest, strategy, defaultStrategy, showStrategySelector,
  preferencesLoaded, preferencesBusy, preferencesMessage, savePreferences,
  reply, sendStart, sendAnswer, sendReview, sendStop,
} = useTasks()
</script>

<template>
  <div class="page-title">
    <h1>RITSUKO — Secretary Core / Orchestrator</h1>
    <div class="connection" :class="{ disconnected: !connected }">
      <span class="dot" />
      {{ connected ? (commandsEnabled ? '実Task操作API接続中' : 'Task参照API接続中（読取専用）') : 'API未接続' }}
    </div>
  </div>
  <p v-if="problem" class="error" role="alert">{{ problem }}</p>
  <div class="workspace">
    <main class="ritsuko-main">
      <TaskCommandPanel
        v-model:request="draftRequest" v-model:strategy="strategy"
        v-model:default-strategy="defaultStrategy"
        v-model:show-strategy-selector="showStrategySelector" v-model:reply="reply"
        :preferences-loaded="preferencesLoaded" :preferences-busy="preferencesBusy"
        :preferences-message="preferencesMessage"
        :commands-enabled="commandsEnabled" :busy="commandBusy"
        :task="selected" :feedback="commandFeedback" :error="commandError"
        :operation="operation"
        @start="sendStart" @answer="sendAnswer" @review="sendReview" @stop="sendStop"
        @save-preferences="savePreferences"
      />
      <CurrentTask :task="selected" :trace="trace" :loading="refreshing" />
      <MagiProgress :task="selected" />
      <TaskActivity :trace="trace" />
    </main>
    <TaskSidebar :items="items" :selected-id="selectedId" :view="view"
      :commands-enabled="commandsEnabled" :has-more="hasMore" :loading="refreshing"
      @select="selectTask" @filter="selectView" @more="loadMore"
    />
  </div>
</template>
