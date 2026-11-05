import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { notificationsRequest, previewNotifications } from './notificationsApi.js'
import { refreshAccessToken } from '../../auth/api/authClient.js'
vi.mock('../../auth/api/authClient.js',() => ({refreshAccessToken:vi.fn()}))
vi.mock('../../auth/model/authStore.js',() => ({getAccessToken:() => 'test-only'}))
beforeEach(() => vi.stubGlobal('fetch',vi.fn()))
afterEach(() => vi.unstubAllGlobals())
it('GETs settings and POSTs preview without using PATCH',async () => {
  fetch.mockResolvedValue({ok:true,status:200,json:async()=>({ok:true})})
  await notificationsRequest('settings/')
  expect(fetch.mock.lastCall[1].method).toBe('GET')
  await previewNotifications({day:'2026-10-30'})
  expect(fetch.mock.lastCall[1].method).toBe('POST')
  expect(JSON.parse(fetch.mock.lastCall[1].body)).toEqual({day:'2026-10-30'})
})
it('refreshes once and retains the POST method',async () => {
  fetch.mockResolvedValueOnce({status:401}).mockResolvedValueOnce({ok:true,status:200,json:async()=>({})})
  await previewNotifications({})
  expect(refreshAccessToken).toHaveBeenCalled()
  expect(fetch.mock.lastCall[1].method).toBe('POST')
})
it('surfaces server validation and malformed responses',async () => {
  fetch.mockResolvedValueOnce({ok:false,status:400,json:async()=>({baseline:['bad rule']})})
  await expect(notificationsRequest('settings/',{})).rejects.toThrow('bad rule')
  fetch.mockResolvedValueOnce({ok:false,status:500,json:async()=>{throw Error()}})
  await expect(notificationsRequest('reports/')).rejects.toThrow('Не удалось загрузить уведомления')
})
