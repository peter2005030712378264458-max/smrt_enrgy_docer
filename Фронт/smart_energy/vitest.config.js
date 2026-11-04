import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
export default defineConfig({
  plugins: [react()],
  test: { environment: 'jsdom', setupFiles: ['./tests/unitSetup.js'],
    include: ['src/pages/notifications/**/*.test.{js,jsx}', 'src/features/notifications/**/*.test.js'],
    coverage: { provider: 'v8', include: ['src/pages/notifications/curveUtils.js','src/pages/notifications/NotificationsPage.jsx','src/features/notifications/api/notificationsApi.js'],
      reporter: ['text','json-summary','html'] } }
})
