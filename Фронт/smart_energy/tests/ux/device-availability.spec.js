import { test, expect } from '@playwright/test'

test.use({ timezoneId: 'America/Los_Angeles' })

const rows = Array.from({ length: 16 }, (_, index) => ({ id: index + 1,
  sensor_name: index === 15 ? 'Климат CO₂ аудитории 238' : `Электросчётчик ${index + 1}`,
  kind: index === 15 ? 'climate' : 'electricity', model: index === 15 ? 'ELSYS ERS CO2' : 'Меркурий-234',
  room: '238', status: index < 12 ? 'no_data' : index === 14 ? 'unknown' : 'low_data',
  reason: index < 12 ? 'Записи есть, но измерения отсутствуют, равны 0 или некорректны' : index === 14 ? 'Эталон не определён' : 'Менее 90% эталона',
  valid_records: index < 12 ? 0 : 10, coverage_percent: index < 12 ? 0 : null,
  last_valid_at: index < 12 ? null : '2026-11-04T21:00:00+03:00', reference_method: 'own_history' }))

async function setup(page, pending = false) {
  let calls = 0
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const url = new URL(route.request().url())
    const path = url.pathname
    let data = {}
    if (path.includes('/auth/refresh')) data = { access: 'isolated-browser-token' }
    else if (path.includes('/auth/me')) data = { id: 1, email: 'test@example.invalid', is_staff: false }
    else if (path.includes('/capabilities')) data = { can_manage: false }
    else if (path.endsWith('/device-availability/')) {
      calls++
      data = pending && calls <= 2 ? { state: 'pending', poll_after_seconds: 2 } : {
        state: 'ready', period: url.searchParams.get('period'), rows,
        summary: { total: 16, no_data: 12, low_data: 3, unknown: 1, ok: 0 },
        from: '2026-11-04T00:00:00+03:00', to_exclusive: '2026-11-05T00:00:00+03:00', checked_at: '2026-11-05T00:30:00+03:00',
      }
    } else if (path.endsWith('/filters/')) data = { date_range: { date_from: '2026-11-03T00:00:00+03:00', date_to: '2026-11-04T23:00:00+03:00' }, devices: [] }
    else if (path.endsWith('/timeseries/')) data = { points: [] }
    else if (path.endsWith('/summary/')) data = { devices_count: 49 }
    else if (path.endsWith('/weekday-heatmap/')) data = { meters: [], count: 0 }
    else if (path.endsWith('/top-devices/') || path.endsWith('/room-loads/')) data = []
    await route.fulfill({ json: data })
  })
  await page.goto('/dashboard')
}

test('normal user can filter all devices, paginate, and keep consumption filters independent', async ({ page }, testInfo) => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await setup(page)
  const panel = page.getByRole('region', { name: 'Состояние датчиков' })
  await expect(panel.locator('tbody tr')).toHaveCount(10)
  await panel.getByRole('button', { name: 'Следующая страница датчиков' }).click()
  await expect(panel.locator('tbody tr')).toHaveCount(5)
  await panel.getByRole('combobox', { name: 'Тип датчика' }).selectOption('climate')
  await expect(panel.locator('tbody tr')).toHaveCount(1)
  await expect(panel.getByText('Климат CO₂ аудитории 238', { exact: true })).toBeVisible()
  await panel.getByRole('searchbox').fill('nonexistent')
  await expect(panel.getByRole('status')).toContainText('По выбранным фильтрам')
  await panel.getByRole('searchbox').clear()
  await panel.getByRole('button', { name: 'Неделя', exact: true }).click()
  await expect(panel.locator('tbody tr')).toHaveCount(1)
  await expect(page.getByRole('combobox', { name: 'Период', exact: true })).toHaveValue('24h')
  await expect(page.getByRole('combobox', { name: 'Дата', exact: true })).toHaveValue('2026-11-04')
  await panel.getByRole('combobox', { name: 'Тип датчика' }).selectOption('all')
  await panel.getByRole('button', { name: 'Все проблемы', exact: true }).click()
  await panel.getByText('Не удалось оценить: 1', { exact: true }).click()
  await expect(panel.locator('.availability-unknown li')).toContainText('Эталон не определён')
  await panel.scrollIntoViewIfNeeded()
  expect(errors).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.screenshot({ path: `../../.venv/aggregation-checks/availability-${testInfo.project.name}.png`, fullPage: true })
  await page.getByRole('button', { name: 'Включить тёмную тему' }).click()
  await panel.getByRole('heading', { name: 'Состояние датчиков' }).scrollIntoViewIfNeeded()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.screenshot({ path: `../../.venv/aggregation-checks/availability-dark-${testInfo.project.name}.png` })
})

test('polls pending report and displays the completed check', async ({ page }) => {
  await setup(page, true)
  const panel = page.getByRole('region', { name: 'Состояние датчиков' })
  await expect(panel.getByRole('status')).toContainText('Проверяем исходные данные')
  await expect(panel.locator('tbody tr')).toHaveCount(10, { timeout: 10000 })
  await expect(panel.getByText('04.11.2026 — 04.11.2026 · Москва', { exact: true })).toBeVisible()
})

test('late response of a previous period cannot overwrite the current report', async ({ page }) => {
  await setup(page)
  const panel = page.getByRole('region', { name: 'Состояние датчиков' })
  await expect(panel.locator('tbody tr')).toHaveCount(10)
  let release
  let complete
  const gate = new Promise(resolve => { release = resolve })
  const completed = new Promise(resolve => { complete = resolve })
  await page.route(url => url.pathname.endsWith('/device-availability/') && url.searchParams.get('period') === 'week', async route => {
    await gate
    try { await route.fulfill({ json: { state: 'ready', rows: [{ ...rows[0], sensor_name: 'УСТАРЕВШИЙ ОТВЕТ' }], summary: { total: 1, no_data: 1, low_data: 0, unknown: 0, ok: 0 } } }) } catch { /* request was aborted by the new period */ } finally { complete() }
  })
  const requested = page.waitForRequest(url => new URL(url.url()).searchParams.get('period') === 'week')
  await panel.getByRole('button', { name: 'Неделя', exact: true }).click()
  await requested
  await panel.getByRole('button', { name: 'Месяц', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(panel.locator('tbody tr')).toHaveCount(10)
  release()
  await completed
  await expect(panel.getByRole('button', { name: 'Месяц', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(panel.getByText('УСТАРЕВШИЙ ОТВЕТ', { exact: true })).toHaveCount(0)
})
