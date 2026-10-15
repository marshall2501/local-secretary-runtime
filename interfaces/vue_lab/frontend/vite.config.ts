/** 秘書AIのVue試作を隔離APIへ接続する開発設定。 */
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: '/ui/',
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api/lab': {
        target: 'http://127.0.0.1:8098',
        changeOrigin: true
      }
    }
  }
})
