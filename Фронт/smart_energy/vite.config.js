import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const projectRoot = fileURLToPath(new URL('.', import.meta.url))
  const env = loadEnv(mode, projectRoot, '')
  const apiProxyTarget = env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:5000'

  return {
    plugins: [react()],
    server: {
      host: env.VITE_DEV_HOST ?? '127.0.0.1',
      port: Number(env.VITE_DEV_PORT ?? 5173),
      proxy: {
        '/api': {
          target: apiProxyTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
