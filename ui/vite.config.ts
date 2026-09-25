import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5200,
    strictPort: true,
    proxy: {
      '/api': {
        // The API's origin. CABINET_API_TARGET overrides it for a checkout
        // that must share the machine's ports (parallel worktrees run their
        // own API on a spare port); the default is the make api port.
        target: process.env.CABINET_API_TARGET ?? 'http://127.0.0.1:8910',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
