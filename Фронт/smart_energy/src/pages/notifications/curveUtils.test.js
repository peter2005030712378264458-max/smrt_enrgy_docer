import { describe, it, expect } from 'vitest'
import { DEFAULT_RULE, chartPoints, moscowDay, percentage, ruleErrors } from './curveUtils.js'
const draft = baseline => ({ threshold_percent:'30', baseline:{ ...DEFAULT_RULE,...baseline } })
describe('notification display and rule validation', () => {
  it('uses Moscow calendar across UTC midnight and offsets', () => {
    expect(moscowDay(0,new Date('2026-11-03T22:30:00Z'))).toBe('2026-11-04')
    expect(moscowDay(-1,new Date('2026-01-01T00:00:00Z'))).toBe('2025-12-31')
  })
  it('preserves measured zero and gaps', () => {
    const [zero,gap]=chartPoints([{ actual_kwh:0,expected_kwh:'0',lower:0,upper:1 },{actual_kwh:null,expected_kwh:'NaN'}])
    expect(zero.actual_kwh).toBe(0); expect(zero.band).toEqual([0,1])
    expect(gap.actual_kwh).toBeNull();expect(gap.expected_kwh).toBeNull()
    expect(percentage(null)).toBe('—');expect(percentage('NaN')).toBe('—')
    expect(percentage(0)).toBe('0%');expect(percentage(30)).toBe('+30%');expect(percentage(-30)).toBe('-30%')
  })
  it('validates dates and all numeric bounds', () => {
    expect(ruleErrors(draft({}),'2026-11-03','2026-11-04')).toEqual([])
    for (const rule of [{history_days:0},{history_days:61},{history_days:1.5},{minimum_days:0},
      {minimum_days:8},{absolute_threshold:''},{absolute_threshold:-1},{absolute_threshold:'NaN'},
      {window_mode:'range'},{window_mode:'range',date_from:'2026-11-04',date_to:'2026-11-03'},
      {window_mode:'range',date_from:'2024-01-01',date_to:'2026-01-01'}]) {
      expect(ruleErrors(draft(rule),'2026-11-03','2026-11-04').length).toBeGreaterThan(0)
    }
    expect(ruleErrors(draft({mode:'live'}),'2026-11-03','2026-11-04').length).toBeGreaterThan(0)
    expect(ruleErrors(draft({}),'2026-11-05','2026-11-04').length).toBeGreaterThan(0)
    for (const threshold of ['0','-1','NaN','10001']) expect(ruleErrors({ ...draft({}),threshold_percent:threshold },'2026-11-03','2026-11-04').length).toBeGreaterThan(0)
  })
})
