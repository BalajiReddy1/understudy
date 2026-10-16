/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The API runs on 8000. 127.0.0.1 rather than localhost: on Windows, localhost adds a
// ~225 ms IPv6 fallback to every request.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  test: {
    environment: 'node',
  },
})
