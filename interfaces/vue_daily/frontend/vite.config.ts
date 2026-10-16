// Provenance-Origin: original
// Provenance-AI: AI-generated
/** Vue画面を/ui/配下へビルドし、開発時はTask参照API（8097）へ転送する設定。 */
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: '/ui/',
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5174,
    strictPort: true,
    proxy: {
      '/api/v1/ritsuko': {
        target: 'http://127.0.0.1:8097',
        changeOrigin: true
      }
    }
  }
})
