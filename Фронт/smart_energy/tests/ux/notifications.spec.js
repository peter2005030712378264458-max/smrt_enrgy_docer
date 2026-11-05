import { test, expect } from '@playwright/test'

test.beforeEach(async ({ page }, testInfo) => {
  await page.addInitScript(theme => localStorage.setItem('smart-energy-theme', theme), testInfo.project.name === 'mobile' ? 'dark' : 'light')
  await page.route(url => url.pathname.startsWith('/api/'),async route => {
    const request=route.request(), path=new URL(request.url()).pathname
    let data={}
    if(path.includes('/auth/refresh')) data={access:'isolated-browser-test-token'}
    else if(path.includes('/auth/me')) data={id:1,email:'ux@example.invalid',first_name:'UX',is_staff:true}
    else if(path.includes('/notifications/settings')) data={enabled:false,threshold_percent:'30',mail_configured:false,email:'ux@example.invalid',baseline:{method:'mean',window_mode:'last_n',history_days:7,minimum_days:1,day_group:'day_type',mode:'daily',absolute_threshold:'0'}}
    else if(path.includes('/notifications/reports')) data={results:[],count:0,page_size:20}
    else if(path.includes('/notifications/preview')) {
      if(request.method()==='GET') data={last_day:'2026-10-30'}
      else {
        const body=request.postDataJSON()
        data={day:body.day,status:'critical',energy_kwh:240,average_kwh:120,checked_hours:24,closed_hours:24,
          baseline_dates:['2026-10-29'],explanation:'Есть отклонения',as_of:'2026-10-31T00:00:00Z',
          reference_days:[{day:'2026-10-29',hours:Array(24).fill(5)}],
          curve:Array.from({length:24},(_,hour)=>({hour,time:`${hour}:00`,actual_kwh:hour===3?null:hour===0?0:10,
            expected_kwh:5,lower:3.5,upper:6.5,deviation_percent:100,sample_count:1,anomaly:hour===12,reason:hour===3?'Нет полных показаний':''}))}
      }
    }
    await route.fulfill({json:data})
  })
})

test('displays graph and source data, changes N and previews without saving',async ({ page }, testInfo) => {
  const errors=[]
  page.on('pageerror',error=>errors.push(error.message))
  let saves=0
  page.on('request',r=>{if(r.method()==='PATCH'&&r.url().includes('/notifications/settings'))saves++})
  await page.goto('/notifications')
  await expect(page.getByRole('heading',{name:'Данные и база сравнения'})).toBeVisible()
  await expect(page.getByRole('img',{name:/Фактическое и ожидаемое/})).toBeVisible()
  await page.getByLabel('N — число дней',{exact:true}).fill('3')
  await expect(page.getByText(/Настройки изменены/)).toBeVisible()
  await page.getByRole('button',{name:'Рассчитать и показать'}).click()
  await expect(page.getByText(/Настройки изменены/)).toHaveCount(0)
  await page.getByText('Почасовые данные и отклонения',{exact:true}).click()
  await expect(page.getByRole('cell',{name:'Нет полных показаний',exact:true})).toBeVisible()
  await page.screenshot({path:testInfo.outputPath('notifications.png'),fullPage:true})
  expect(saves).toBe(0)
  expect(errors).toEqual([])
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1)).toBeTruthy()
})

test('switches live mode and fixed dates, supports keyboard and explicit saving',async ({ page }) => {
  await page.goto('/notifications')
  await page.getByLabel('Режим проверки').selectOption('live')
  await expect(page.getByLabel('Анализируемый день')).toBeDisabled()
  await page.getByLabel('Период для функции').selectOption('range')
  await page.getByLabel('Начало диапазона').fill('2026-10-01')
  await page.getByLabel('Конец диапазона включительно').fill('2026-10-20')
  await page.getByLabel('Функция сравнения').selectOption('median')
  await page.getByLabel('Включить фоновую проверку и письма').check()
  const saved=page.waitForRequest(r=>r.url().includes('/notifications/settings')&&r.method()==='PATCH')
  await page.getByRole('button',{name:'Сохранить правило'}).focus()
  await page.keyboard.press('Enter')
  const request=await saved
  expect(request.postDataJSON().baseline).toMatchObject({mode:'live',window_mode:'range',method:'median'})
  await expect(page.getByText(/Правило сохранено/)).toBeVisible()
})

test('invalid settings do not trigger preview or save',async ({ page }) => {
  await page.goto('/notifications')
  await page.getByLabel('N — число дней',{exact:true}).fill('0')
  await page.getByRole('button',{name:'Рассчитать и показать'}).click()
  await expect(page.getByRole('alert')).toContainText('N должно быть целым')
})

test('Enter in a parameter recalculates without silently saving a rule',async ({ page }) => {
  let saves=0
  page.on('request',r=>{if(r.method()==='PATCH'&&r.url().includes('/notifications/settings'))saves++})
  await page.goto('/notifications')
  await page.getByLabel('N — число дней',{exact:true}).fill('2')
  const calculated=page.waitForRequest(r=>r.url().includes('/notifications/preview')&&r.method()==='POST')
  await page.getByLabel('N — число дней',{exact:true}).press('Enter')
  await calculated
  expect(saves).toBe(0)
})
