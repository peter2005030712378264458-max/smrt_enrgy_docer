const PHASES = ['p1', 'p2', 'p3']

export function mappingVisibility(fields, assigned, inspection) {
  const entries = Object.entries(fields)
  const sourceNames = new Set((inspection?.columns ?? []).map(column => column.name))
  const lowerNames = new Set([...sourceNames].map(name => name.toLocaleLowerCase()))
  function detected(field) {
    const suggestion = inspection?.suggestions?.[field]
    return lowerNames.has(field.toLocaleLowerCase()) || sourceNames.has(suggestion?.column) ||
      (suggestion?.candidates ?? []).some(name => sourceNames.has(name))
  }
  const needsPhases = !assigned.pt && !detected('pt') && PHASES.some(field => assigned[field] || detected(field))
  const required = new Set(['ts', 'sensor_name', ...(needsPhases ? PHASES : ['pt'])])
  // Samples are deliberately ignored: NULL for one sensor does not mean that a column is absent.
  const visible = entries.filter(([field]) => !inspection || required.has(field) || assigned[field] || detected(field))
  const missing = new Set(inspection ? entries
    .filter(([field]) => assigned[field] && !sourceNames.has(assigned[field]))
    .map(([field]) => field) : [])
  return { visible, hiddenCount: entries.length - visible.length, required, missing }
}
