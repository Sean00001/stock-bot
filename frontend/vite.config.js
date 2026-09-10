import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    // 開發用 ngrok 之類的通道把服務公開到外網時，連進來的 Host 不是
    // localhost/127.0.0.1，Vite 預設的 DNS rebinding 防護會直接擋掉，顯示
    // "Blocked request. This host ... is not allowed."。這裡設成 true
    // 代表開發階段允許任何 Host 連進來 —— ngrok 免費版網址每次重開都會換，
    // 寫死單一網址每次都要手動改很麻煩。正式上線／對外服務時不會用這個
    // vite dev server，是另外 build 出靜態檔案，所以這個設定只影響開發時
    // 的預覽，不是安全疑慮。
    allowedHosts: true,
    watch: {
      usePolling: true
    },
    proxy: {
      '/api': {
        target: 'http://backend:8000',
        changeOrigin: true
      }
    }
  }
})
