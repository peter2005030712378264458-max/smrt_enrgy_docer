import test from 'node:test'
import assert from 'node:assert/strict'
import { mappingAvailability } from './mappingAvailability.js'

const inspect = fields => ({ columns: Object.entries(fields).map(([name, type]) => ({ name, type })) })
const keys = { ts: 'measured_at', sensor_name: 'meter_id' }
const schema = { measured_at: 'timestamptz', meter_id: 'text' }

test('time and meter are mandatory, but voltage-only and points-only mappings can be saved', () => {
  assert.equal(mappingAvailability(keys, inspect(schema)).errors.length, 0)
  const voltage = mappingAvailability({ ...keys, u1: 'voltage' }, inspect({ ...schema, voltage: 'numeric' }))
  assert.deepEqual(voltage.metrics, ['voltage_avg_v'])
  assert.equal(voltage.errors.length, 0)
  for (const field of ['ts', 'sensor_name']) {
    const assigned = { ...keys }
    delete assigned[field]
    assert.equal(mappingAvailability(assigned, inspect(schema)).errors.length, 1)
  }
})

test('two phases remain available individually but cannot become total power or energy', () => {
  const assigned = { ...keys, p1: 'phase_a', p2: 'phase_b' }
  const result = mappingAvailability(assigned, inspect({ ...schema, phase_a: 'numeric', phase_b: 'numeric' }))
  assert.deepEqual(result.metrics, ['phase1_power_w_avg', 'phase2_power_w_avg'])
  assert.equal(result.errors.length, 0)
  assert.ok(result.unavailable.includes('active_power_w_avg'))
})

test('a deleted optional column changes availability without erasing manual mappings', () => {
  const assigned = Object.freeze({ ...keys, pt: 'deleted_power', u1: 'voltage' })
  const result = mappingAvailability(assigned, inspect({ ...schema, voltage: 'numeric' }))
  assert.deepEqual(result.metrics, ['voltage_avg_v'])
  assert.equal(result.errors.length, 0)
  assert.equal(assigned.pt, 'deleted_power')
})

test('schema types and permissions are checked; NULL samples do not remove real columns', () => {
  const inspection = inspect({ ...schema, voltage: 'numeric' })
  inspection.sample = [{ voltage: null }]
  assert.ok(mappingAvailability({ ...keys, u1: 'voltage' }, inspection).metrics.includes('voltage_avg_v'))
  inspection.columns.find(column => column.name === 'voltage').readable = false
  assert.equal(mappingAvailability({ ...keys, u1: 'voltage' }, inspection).metrics.length, 0)
  assert.ok(mappingAvailability(keys, inspect({ ...schema, measured_at: 'text' })).invalidTypes.has('ts'))
  assert.ok(mappingAvailability({ ...keys, pt: 'power' }, inspect({ ...schema, power: 'text' })).invalidTypes.has('pt'))
})
