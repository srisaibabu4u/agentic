import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the UI calls /api/...; Vite forwards those requests to FastAPI,
// so the browser sees one origin and no CORS setup is needed.
const API = process.env.KYC_API_URL ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: API, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, '') },
    },
  },
})
