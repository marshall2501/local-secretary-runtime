<!-- Provenance-Origin: original; Provenance-AI: AI-generated -->
<script setup lang="ts">
/**
 * RITSUKO画面の中央に保存済みTask・操作・履歴、右にTask一覧を置く。
 * 実Task操作はcommands_enabledのときだけ既存APIへ配送する。
 */
import { useTasks } from './useTasks'
import CurrentTask from './components/CurrentTask.vue'
import TaskLiveStatus from './components/TaskLiveStatus.vue'
import TaskCommandPanel from './components/TaskCommandPanel.vue'
import MagiProgress from './components/MagiProgress.vue'
import TaskActivity from './components/TaskActivity.vue'
import TaskSidebar from './components/TaskSidebar.vue'
import MagiSettingsPanel from '../system/MagiSettingsPanel.vue'

const {
  layout, items, view, hasMore, selectedId, trace, selected, connected, refreshing,
  problem, refresh, selectTask, selectView, loadMore,
  commandsEnabled, commandBusy, commandFeedback, commandError, operation,
  draftRequest, strategy, defaultStrategy, showStrategySelector,
  activeDefaultStrategy, activeShowStrategySelector, preferencesLoaded, preferencesBusy, preferencesMessage, savePreferences,
  reply, sendStart, sendAnswer, sendReview, sendRemember, sendStop, sendResume,
} = useTasks()
</script>

<template>
  <div class="page-title">
    <h1>RITSUKO — 秘書AI</h1>
    <div class="connection" :class="{ disconnected: !connected }">
      <span class="dot" />
      {{ connected ? (commandsEnabled ? '実Task操作API接続中' : 'Task参照API接続中（読取専用）') : 'API未接続' }}
    </div>
  </div>
  <p v-if="problem" class="error" role="alert">{{ problem }}</p>
  <div class="workspace">
    <main class="ritsuko-main">
      <TaskLiveStatus :task="selected" :task-id="selectedId" :operation="operation"
        :connected="connected" :commands-enabled="commandsEnabled"
        :busy="commandBusy" @stop="sendStop" />
      <!-- 保存済みTaskを開いた場合は回答を先に。新規入力は直後で常時編集可能。 -->
      <CurrentTask v-if="selected" :task="selected" :trace="trace" :loading="refreshing" />
      <TaskCommandPanel
        v-model:request="draftRequest" v-model:strategy="strategy"
        v-model:default-strategy="defaultStrategy"
        v-model:show-strategy-selector="showStrategySelector" v-model:reply="reply"
        :active-default-strategy="activeDefaultStrategy"
        :active-show-strategy-selector="activeShowStrategySelector"
        :preferences-loaded="preferencesLoaded" :preferences-busy="preferencesBusy"
        :preferences-message="preferencesMessage"
        :commands-enabled="commandsEnabled" :busy="commandBusy"
        :task="selected" :feedback="commandFeedback" :error="commandError"
        :operation="operation"
        @start="sendStart" @answer="sendAnswer" @review="sendReview" @remember="sendRemember" @resume="sendResume"
        @save-preferences="savePreferences" @refresh="() => refresh(true)"
      />
      <CurrentTask v-if="!selected" :task="selected" :trace="trace" :loading="refreshing" />
      <MagiProgress :task="selected" :default-open="layout?.core.verification === true" />
      <TaskActivity v-if="layout?.visibility.core.trace !== false"
        :trace="trace" :default-open="layout?.core.trace === true" />
      <details v-if="layout?.visibility.core.magi_configuration !== false"
        class="card magi-config-accordion" :open="layout?.core.magi_configuration === true">
        <summary>MAGI — 3席のLLM設定</summary>
        <MagiSettingsPanel />
      </details>
    </main>
    <TaskSidebar :items="items" :selected-id="selectedId" :view="view"
      :commands-enabled="commandsEnabled" :has-more="hasMore" :loading="refreshing"
      @select="selectTask" @filter="selectView" @more="loadMore"
    />
  </div>
</template>

<style scoped>
.magi-config-accordion { padding:12px 14px; border-color:#a6d9d1; background:#f0fbf8; }
.magi-config-accordion > summary { cursor:pointer; padding:4px 0; font-weight:700; }
.magi-config-accordion[open] > summary { border-bottom:1px solid #c6e1dc; padding-bottom:10px; margin-bottom:11px; }
.magi-config-accordion :deep(.magi-settings) { margin:0; box-shadow:none; }
</style>
