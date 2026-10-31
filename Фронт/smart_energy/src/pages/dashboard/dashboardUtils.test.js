import test from 'node:test'
import assert from 'node:assert/strict'
import { buildPowerSeries, buildPowerStats, buildSummaryFromTimeseries } from './dashboardUtils.js'

const at = hour => `2026-10-15T${String(hour).padStart(2, '0')}:00:00+03:00`

test('missing measurements stay unknown and real zero is retained', () => {
  const points = [{ timestamp: at(0), value: null }, { timestamp: at(1), value: 0 }]
  assert.deepEqual(buildPowerSeries(points).map(point => point.kw), [null, 0])
  const stats = buildPowerStats(points, null)
  assert.equal(stats.avgKw, 0)
  assert.equal(stats.currentKw, 0)
  assert.equal(stats.maxKw, 0)
})

test('all-NULL series does not become a zero load', () => {
  const points = [{ timestamp: at(0), value: null }]
  const stats = buildPowerStats(points, { avg_power_kw: null, max_power_kw: null })
  for (const field of ['avgKw', 'maxKw', 'minKw', 'currentKw', 'loadFactor']) assert.equal(stats[field], null)
  assert.equal(stats.lastTimestamp, at(0))
  assert.equal(buildSummaryFromTimeseries(points, null).points, 1)
})

test('missing last reading is not replaced by the last known power', () => {
  const points = [{ timestamp: at(0), value: 4 }, { timestamp: at(1), value: null }]
  const stats = buildPowerStats(points, null)
  assert.equal(stats.avgKw, 4)
  assert.equal(stats.currentKw, null)
  assert.equal(stats.lastTimestamp, at(1))
  assert.equal(buildSummaryFromTimeseries(points, null).current_power_kw, null)
})

test('fallback does not fabricate dates or unavailable voltage and frequency', () => {
  const result = buildSummaryFromTimeseries([], { date_range: { date_from: at(0), date_to: at(1) } })
  assert.equal(result.date_from, null)
  assert.equal(result.timestamp_iso, null)
  assert.equal(result.avg_voltage_v, null)
  assert.equal(result.avg_frequency_hz, null)
  assert.deepEqual(buildPowerSeries([{ timestamp: null, value: 3 }, { timestamp: 'invalid', value: 5 }]), [])
})

test('gaps stay in the timeline and do not dilute averages or become energy', () => {
  const points = [{ timestamp: at(0), value: 2, energy_kwh: 2 }, { timestamp: at(1), value: null, energy_kwh: null },
    { timestamp: at(2), value: 4, energy_kwh: 4 }]
  assert.equal(buildPowerStats(points, null).avgKw, 3)
  assert.equal(buildPowerSeries(points).length, 3)
  assert.equal(buildSummaryFromTimeseries(points, null).total_energy_kwh, null)
})
