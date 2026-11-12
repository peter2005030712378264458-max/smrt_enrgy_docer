import test from 'node:test'
import assert from 'node:assert/strict'
import { filterAvailability, availabilityDate } from './availabilityUtils.js'

const rows = [
  { id: 1, sensor_name: 'Счётчик один', status: 'no_data', kind: 'electricity' },
  { id: 2, sensor_name: 'Климат', status: 'low_data', kind: 'climate' },
  { id: 3, sensor_name: 'Метеостанция', status: 'unknown', kind: 'meteo' },
  { id: 4, sensor_name: 'Счётчик два', status: 'ok', kind: 'electricity' },
]

test('problem filter distinguishes unknown assessment from no measurements', () => {
  assert.deepEqual(filterAvailability(rows, {}).map(row => row.id), [1, 2])
  assert.deepEqual(filterAvailability(rows, { status: 'low_data' }).map(row => row.id), [2])
})
test('search and type work on the whole result and use stable IDs', () => {
  assert.deepEqual(filterAvailability(rows, { kind: 'climate', search: ' КЛИМАТ ' }).map(row => row.id), [2])
  assert.deepEqual(filterAvailability(rows, { search: '1' }).map(row => row.id), [1])
  assert.deepEqual(filterAvailability(rows, { search: 'nonexistent' }), [])
})
test('dates use Moscow independent of browser timezone', () => {
  assert.equal(availabilityDate('2026-11-04T22:00:00Z', true), '05.11.2026')
  assert.equal(availabilityDate(null), '—')
})
