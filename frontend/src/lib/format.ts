/** "staging/src/k8s.io/apiserver/pkg/storage" -> { lead: "apiserver/pkg/", tail: "storage" }.
 * The staging prefix is noise to a reader; the last segment is what they look for. */
export function splitPath(name: string): { lead: string; tail: string } {
  const short = name.replace(/^staging\/src\/k8s\.io\//, '')
  const i = short.lastIndexOf('/')
  return i < 0 ? { lead: '', tail: short } : { lead: short.slice(0, i + 1), tail: short.slice(i + 1) }
}

export function shortPath(name: string): string {
  const { lead, tail } = splitPath(name)
  return lead + tail
}

/** contributor-25b5f -> 25b5f, for tight spots where the column already says "people". */
export function shortHandle(handle: string): string {
  return handle.replace(/^contributor-/, '')
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

export function ms(n: number): string {
  if (n < 1000) return `${Math.round(n)} ms`
  return `${(n / 1000).toFixed(1)} s`
}

export function toolLabel(tool: string): string {
  return tool.replace(/_/g, ' ')
}
