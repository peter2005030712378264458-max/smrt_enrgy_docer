import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import NotificationsPage from './NotificationsPage.jsx'
import { DEFAULT_RULE } from './curveUtils.js'
import { notificationsRequest, previewNotifications } from '../../features/notifications/api/notificationsApi.js'
vi.mock('../../features/notifications/api/notificationsApi.js', () => ({ notificationsRequest:vi.fn(),previewNotifications:vi.fn() }))
vi.mock('../../shared/theme/ThemeToggle.jsx', () => ({ default:() => <span>Тема</span> }))
vi.mock('./ConsumptionCurve.jsx', () => ({ default:({ points }) => <div role="img" aria-label="Почасовая кривая">{points.length} точек</div> }))
const settings = { enabled:false, threshold_percent:'30', baseline:DEFAULT_RULE, mail_configured:false,email:'test@example.invalid',last_check_at:null }
const result = { day:'2026-10-30', status:'normal', energy_kwh:0, average_kwh:10, closed_hours:24, checked_hours:24,
  baseline_dates:['2026-10-29'], explanation:'Проверка завершена',as_of:'2026-10-31T00:00:00Z',
  curve:[{hour:0,time:'00:00',actual_kwh:0,expected_kwh:10,deviation_percent:-100,sample_count:1,reason:'',anomaly:true}],
  reference_days:[{day:'2026-10-29',hours:Array(24).fill(10)}] }
function mount() { return render(<MemoryRouter><NotificationsPage /></MemoryRouter>) }
beforeEach(() => {
  vi.clearAllMocks()
  notificationsRequest.mockImplementation(async (path,payload) => path==='settings/' ? { ...settings,...payload } :
    path==='preview/' ? { last_day:'2026-10-30' } : { results:[],count:0,page_size:20 })
  previewNotifications.mockResolvedValue(result)
})
describe('notification interactions', () => {
  it('loads real-data preview while notifications stay disabled', async () => {
    mount()
    expect(await screen.findByRole('img',{name:'Почасовая кривая'})).toBeInTheDocument()
    expect(screen.getByLabelText('Включить фоновую проверку и письма')).not.toBeChecked()
    expect(notificationsRequest.mock.calls.filter(([,payload]) => payload)).toHaveLength(0)
    expect(screen.getByText(/^0,000 кВт·ч$/)).toBeInTheDocument()
  })
  it('changes N, marks old preview stale, and calculates without saving', async () => {
    const user=userEvent.setup();mount()
    const input=await screen.findByLabelText('N — число дней')
    await user.clear(input);await user.type(input,'2')
    expect(screen.getByText(/Настройки изменены/)).toBeInTheDocument()
    await user.click(screen.getByRole('button',{name:'Рассчитать и показать'}))
    await waitFor(() => expect(previewNotifications.mock.lastCall[0].baseline.history_days).toBe(2))
    expect(notificationsRequest.mock.calls.filter(([,payload]) => payload)).toHaveLength(0)
  })
  it('switches modes, functions and fixed range, saves only explicitly', async () => {
    const user=userEvent.setup();mount()
    await screen.findByLabelText('Режим проверки')
    await user.selectOptions(screen.getByLabelText('Режим проверки'),'live')
    expect(screen.getByLabelText('Анализируемый день')).toBeDisabled()
    await user.selectOptions(screen.getByLabelText('Функция сравнения'),'linear')
    expect(screen.getByText(/Для тренда нужно минимум два/)).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Период для функции'),'range')
    await user.type(screen.getByLabelText('Начало диапазона'),'2026-10-01')
    await user.type(screen.getByLabelText('Конец диапазона включительно'),'2026-10-20')
    await user.click(screen.getByLabelText('Включить фоновую проверку и письма'))
    await user.click(screen.getByRole('button',{name:'Сохранить правило'}))
    await waitFor(() => expect(notificationsRequest).toHaveBeenCalledWith('settings/',expect.objectContaining({enabled:true,baseline:expect.objectContaining({mode:'live',method:'linear',window_mode:'range'})})))
  })
  it('shows preview errors and can retry', async () => {
    previewNotifications.mockRejectedValueOnce(new Error('Источник недоступен'))
    const user=userEvent.setup();mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('Источник недоступен')
    await user.click(screen.getByRole('button',{name:'Рассчитать и показать'}))
    expect(await screen.findByRole('img')).toBeInTheDocument()
  })
  it('ignores an outdated response when settings changed during loading', async () => {
    let resolve
    previewNotifications.mockImplementationOnce(() => new Promise(done => {resolve=done}))
    const user=userEvent.setup();mount()
    await screen.findByLabelText('N — число дней')
    await user.selectOptions(screen.getByLabelText('Функция сравнения'),'median')
    resolve(result)
    await waitFor(() => expect(screen.queryByRole('img')).not.toBeInTheDocument())
    await user.click(screen.getByRole('button',{name:'Рассчитать и показать'}))
    expect(await screen.findByRole('img')).toBeInTheDocument()
  })
  it('shows settings failures without creating an empty rule', async () => {
    notificationsRequest.mockRejectedValue(new Error('Ошибка настроек'))
    mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('Ошибка настроек')
    expect(screen.queryByRole('button',{name:'Сохранить правило'})).not.toBeInTheDocument()
  })
})

describe('history and failure UX', () => {
  it('shows versioned and legacy reports, mail state and pagination', async () => {
    const user=userEvent.setup()
    notificationsRequest.mockImplementation(async (path,payload) => {
      if(path==='settings/') return { ...settings,...payload,mail_configured:true }
      if(path==='preview/') return {last_day:'2026-10-30'}
      return {count:21,page_size:20,results:path.includes('page=2') ? [] : [
        {id:1,day:'2026-10-30',status:'critical',energy_kwh:0,average_kwh:null,threshold_percent:30,
         baseline_dates:[],explanation:'Проверка',email_status:'sent',baseline_snapshot:{rule:{method:'median',mode:'live'}},
         delivery_snapshot:{anomaly_hours:[2]}},
        {id:2,day:'2026-10-29',status:'insufficient',energy_kwh:null,average_kwh:null,threshold_percent:30,
         baseline_dates:['2026-10-28'],explanation:'Нет данных',email_status:'custom-state',email_error:'SMTP ошибка'}
      ]}
    })
    mount()
    expect(await screen.findByText(/Прежнее суточное правило/)).toBeInTheDocument()
    expect(screen.getByText('Передано почтовому серверу')).toBeInTheDocument()
    expect(screen.getByText('SMTP ошибка')).toBeInTheDocument()
    await user.click(screen.getAllByText('Подробнее')[0])
    expect(screen.getByText(/В письме отмечены часы: 2:00/)).toBeVisible()
    await user.click(screen.getByRole('button',{name:'Далее'}))
    await waitFor(()=>expect(notificationsRequest).toHaveBeenCalledWith('reports/?page=2'))
    expect(screen.getByRole('button',{name:'Далее'})).toBeDisabled()
    await user.click(screen.getByRole('button',{name:'Назад'}))
    await waitFor(()=>expect(notificationsRequest).toHaveBeenCalledWith('reports/?page=1'))
  })

  it('validates preview, changes other controls and surfaces save errors', async () => {
    const user=userEvent.setup();mount()
    const n=await screen.findByLabelText('N — число дней')
    await user.clear(n);await user.type(n,'0')
    const before=previewNotifications.mock.calls.length
    await user.click(screen.getByRole('button',{name:'Рассчитать и показать'}))
    expect(await screen.findByRole('alert')).toHaveTextContent('N должно')
    expect(previewNotifications).toHaveBeenCalledTimes(before)
    await user.clear(n);await user.type(n,'3')
    await user.selectOptions(screen.getByLabelText('Сопоставимость дней'),'weekday')
    await user.clear(screen.getByLabelText('Минимум дней на каждый час'))
    await user.type(screen.getByLabelText('Минимум дней на каждый час'),'2')
    await user.clear(screen.getByLabelText('Порог отклонения, %'))
    await user.type(screen.getByLabelText('Порог отклонения, %'),'25')
    await user.clear(screen.getByLabelText('Минимальное отклонение, кВт·ч'))
    await user.type(screen.getByLabelText('Минимальное отклонение, кВт·ч'),'1')
    notificationsRequest.mockImplementation(async (path,payload)=> {
      if(payload) throw new Error('Сохранение недоступно')
      return path==='settings/'? settings : {results:[],count:0,page_size:20}
    })
    await user.click(screen.getByRole('button',{name:'Сохранить правило'}))
    expect(await screen.findByRole('alert')).toHaveTextContent('Сохранение недоступно')
  })
})

describe('live refresh lifecycle', () => {
  it('refreshes an unchanged live preview and stops after an edit', async () => {
    notificationsRequest.mockImplementation(async (path,payload) => path==='settings/' ?
      { ...settings,...payload,legacy_rule:true,baseline:{...DEFAULT_RULE,mode:'live'} } :
      path==='preview/'?{}:{results:[],count:0,page_size:20})
    mount()
    await screen.findByRole('img')
    expect(screen.getByText(/Пока сохранено прежнее суточное правило/)).toBeInTheDocument()
    vi.useFakeTimers()
    try {
      const count=previewNotifications.mock.calls.length
      await act(async()=>{await vi.advanceTimersByTimeAsync(60000)})
      expect(previewNotifications.mock.calls.length).toBeGreaterThan(count)
      fireEvent.change(screen.getByLabelText('N — число дней'),{target:{value:'2'}})
      const edited=previewNotifications.mock.calls.length
      await act(async()=>{await vi.advanceTimersByTimeAsync(60000)})
      expect(previewNotifications).toHaveBeenCalledTimes(edited)
    } finally { vi.useRealTimers() }
  })
})
