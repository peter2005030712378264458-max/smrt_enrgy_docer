import test from 'node:test'
import assert from 'node:assert/strict'
import { mappingVisibility } from './columnMappingVisibility.js'

const fields = { ts: 'Время', sensor_name: 'Счётчик', pt: 'Мощность', p1: 'L1', p2: 'L2', p3: 'L3', t: 'Температура', frequency: 'Частота' }
const inspect = (names, extra = {}) => ({ columns: names.map(name => ({ name })), ...extra })
const names = result => result.visible.map(([name]) => name)

test('before inspection all targets remain available for manual mapping', () => {
  const result = mappingVisibility(fields, {}, null)
  assert.equal(result.hiddenCount, 0)
  assert.equal(result.missing.size, 0)
  assert.deepEqual(names(result), Object.keys(fields))
})

test('only unsupported optional targets are hidden; required targets stay visible', () => {
  const result = mappingVisibility(fields, {}, inspect(['ts', 'sensor_name', 'pt']))
  assert.deepEqual(names(result), ['ts', 'sensor_name', 'pt'])
  assert.equal(result.hiddenCount, 5)
  assert.deepEqual(names(mappingVisibility(fields, {}, inspect([]))), ['ts', 'sensor_name'])
})

test('a real temperature column remains available even for NULL and heterogeneous sensor samples', () => {
  const result = mappingVisibility(fields, {}, inspect(['ts', 'sensor_name', 'pt', 't'], {
    sample: [{ sensor_name: 'meter-a', t: null }, { sensor_name: 'meter-b', t: 24 }],
  }))
  assert.ok(names(result).includes('t'))
  assert.ok(names(mappingVisibility(fields, {}, inspect(['t'], { sample: [{ t: null }] }))).includes('t'))
})

test('recognized aliases and ambiguous candidates remain assignable without automatic assignment', () => {
  const assigned = Object.freeze({})
  const result = mappingVisibility(fields, assigned, inspect(['temperature_a', 'temperature_b'], {
    suggestions: { t: { column: null, candidates: ['temperature_a', 'temperature_b'] } },
  }))
  assert.ok(names(result).includes('t'))
  assert.deepEqual(assigned, {})
})

test('manual mappings and missing saved columns are always visible and never erased', () => {
  const assigned = Object.freeze({ t: 'customer_temp', frequency: 'deleted_hz' })
  const result = mappingVisibility(fields, assigned, inspect(['customer_temp']))
  assert.ok(names(result).includes('t'))
  assert.ok(names(result).includes('frequency'))
  assert.deepEqual([...result.missing], ['frequency'])
  assert.deepEqual(assigned, { t: 'customer_temp', frequency: 'deleted_hz' })
})

test('phase-only sources expose existing phases without requiring absent measurements', () => {
  const result = mappingVisibility(fields, {}, inspect(['ts', 'sensor_name', 'p1', 'p2', 'p3']))
  assert.deepEqual(names(result), ['ts', 'sensor_name', 'p1', 'p2', 'p3'])
  assert.deepEqual([...result.required], ['ts', 'sensor_name'])
  assert.deepEqual(names(mappingVisibility(fields, {}, inspect(['p1']))), ['ts', 'sensor_name', 'p1'])
})
