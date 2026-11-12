import { test, expect } from '@playwright/test'

test.use({ timezoneId: 'America/Los_Angeles' })

for (const scenario of [
  { name: 'isolated zero', known: [1, 2, 6] },
  { name: 'one known day and missing last day', known: [1] },
  { name: 'all unknown', known: [] },
  { name: 'seven known days', known: [0, 1, 2, 3, 4, 5, 6] },
]) test(scenario.name, async ({ page }, testInfo) => {
  const pageErrors = []
  page.on('pageerror', error => pageErrors.push(error.message))
  const dates = ['2026-10-29', '2026-10-30', '2026-10-31', '2026-11-01', '2026-11-02', '2026-11-03', '2026-11-04']
  const points = dates.map((date, index) => ({ timestamp: `${date}T00:00:00+03:00`, value: null,
    available_value: scenario.known.includes(index) ? index === 6 ? 0 : 25 : null,
    available_meters: scenario.known.includes(index) ? 47 : 0, total_meters: 49,
    energy_kwh: null, available_energy_kwh: scenario.known.includes(index) ? index === 6 ? 0 : 1 : null }))
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const path = new URL(route.request().url()).pathname
    let data = {}
    if (path.includes('/auth/refresh')) data = { access: 'isolated-browser-test-token' }
    else if (path.includes('/auth/me')) data = { id: 1, email: 'test@example.invalid', is_staff: true }
    else if (path.includes('/capabilities')) data = { can_manage: false }
    else if (path.endsWith('/device-availability/')) data = { state: 'ready', rows: [], summary: { total: 0, no_data: 0, low_data: 0, unknown: 0, ok: 0 } }
    else if (path.endsWith('/filters/')) data = { date_range: { date_from: points[0].timestamp, date_to: points.at(-1).timestamp }, devices: [] }
    else if (path.endsWith('/timeseries/')) data = { points, granularity: 'day' }
    else if (path.endsWith('/summary/')) data = { devices_count: 49, points: 100, total_energy_kwh: null, available_energy_kwh: scenario.known.length ? scenario.known.filter(index => index !== 6).length : null }
    else if (path.endsWith('/weekday-heatmap/')) data = { meters: [], count: 0, total_pages: 0 }
    else if (path.endsWith('/top-devices/') || path.endsWith('/room-loads/')) data = []
    await route.fulfill({ json: data })
  })
  await page.goto('/dashboard')
  await page.getByRole('combobox', { name: 'Период', exact: true }).selectOption('7d')
  await expect(page.getByTestId('power-point')).toHaveCount(scenario.known.length)
  for (const dot of await page.getByTestId('power-point').all()) await expect(dot).toBeVisible()
  if (scenario.known.length) await expect(page.getByText('Энергия по доступным данным', { exact: true })).toBeVisible()
  await expect(page.getByText(`Позиций периода: 7 · с мощностью: ${scenario.known.length}`, { exact: true })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Дата', exact: true })).toHaveValue('2026-11-04')
  if (scenario.name === 'isolated zero') {
    await page.getByTestId('power-point').last().hover()
    await expect(page.locator('.recharts-tooltip-label')).toContainText('энергия по доступным данным: 0')
    await page.getByRole('heading', { name: 'Дашборд энергопотребления', exact: true }).hover()
  }
  expect(pageErrors).toEqual([])
  if (scenario.name === 'isolated zero') await page.screenshot({ path: `../../.venv/aggregation-checks/week-${testInfo.project.name}.png`, fullPage: true })
})
