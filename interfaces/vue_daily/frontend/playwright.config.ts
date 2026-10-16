// Provenance-Origin: original
// Provenance-AI: AI-generated
/** 本番DBへ接続せず、8097/8099 fake HTTP serversを実Chromiumで操作する。 */
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:8097',
    browserName: 'chromium',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'cd ../../.. && python -m interfaces.vue_daily.browser_fixture',
    url: 'http://127.0.0.1:8097/api/v1/ritsuko/health',
    timeout: 45_000,
    reuseExistingServer: false,
  },
})
