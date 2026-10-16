// Types and calls for the Understudy API. Shapes mirror backend/understudy/api.py.

export type Org = 'k8s' | 'sample'

export interface BoardRow {
  name: string
  understudy: number
  knowers: number
  approvers: number
  critical: number
  team: string
  sigs: string[]
  emeritus_approvers: number
  inactive_owners: string | null
}

export interface StagePerson {
  handle: string
  on_route: boolean
  critical: boolean
  off_stage: boolean
}

export interface Stage {
  name: string
  understudy: number
  team: string
  sigs: string[]
  emeritus_approvers: number
  inactive_owners: string | null
  knowers: (StagePerson & { score: number })[]
  approvers: (StagePerson & { knows: boolean })[]
  routes: string[][]
}

export interface Change {
  system: string
  before: number
  after: number
  routes?: string[][]
}

export interface Rehearsal {
  people: string[]
  changes: Change[]
  rerun: string[]
  total_systems: number
  timings_ms: Record<string, number>
  /** Routes after the departure for every system that was re-run. */
  routes: Record<string, string[][]>
}

export interface Fix {
  kind: 'pair' | 'approve'
  knower: string
  approver: string | null
  team: string
  gain: number
  also: Change[]
  summary: string
  owners_diff: string | null
}

export interface Proposal {
  system: string
  before: number
  fixes: Fix[]
  reason: string | null
  tried: number
  timings_ms: Record<string, number>
}

export interface QueryRun {
  graph: string
  cypher: string
  ms: number
  rows: number
}

export type Status = 'ok' | 'memory' | 'not_in_graph' | 'unknown'

export interface Step {
  tool: string
  args: Record<string, unknown>
  status: Status
  summary: string
  ms: number
  queries: QueryRun[]
}

export interface Cites {
  systems: string[]
  people: string[]
  keps: { number: number; title: string | null; url: string }[]
}

export interface Answer {
  text: string
  kind: 'path' | 'memory' | 'not_in_graph' | 'unknown'
  checked: boolean
  note: string | null
  model: string | null
  session: string | null
  steps: Step[]
  cites: Cites
}

export type AskEvent =
  | ({ type: 'tool' } & Step)
  | { type: 'notice'; text: string }
  | ({ type: 'answer' } & Answer)
  | { type: 'error'; text: string }

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function query(params: Record<string, string | null | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v)
  const s = q.toString()
  return s ? `?${s}` : ''
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      ...init,
      headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
    })
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.")
  }
  if (!res.ok) {
    let detail = `The server answered ${res.status}.`
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') detail = body.detail
      else if (Array.isArray(body?.detail)) detail = 'That request was not valid.'
    } catch {
      /* not JSON; keep the generic message */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

const systemPath = (name: string) => name.split('/').map(encodeURIComponent).join('/')

/** A statistic with its 95% interval from resampling whole departures. */
export interface Estimate {
  value: number | null
  low: number | null
  high: number | null
  resamples: number
}

export interface ProofUnit {
  person: string
  left_at: string
  system: string
  understudy_before: number
  understudy_after: number
  knowers_before: number
  knowers_after: number
  prs_before: number
  prs_after: number
  hours_before: number | null
  hours_after: number | null
  project_ratio: number
  /** Slowdown relative to the whole project; null when the system wasn't measurable. */
  slowdown: number | null
  reason: string | null
}

export interface ProofEvent {
  person: string | null
  left_at: string
  prs_before: number
  reason: string | null
}

/** data/proof.json, written by scripts/backtest.py. */
export interface Proof {
  generated_at: string
  data: { prs: number; first_merge: string; last_merge: string }
  method: Record<string, number | string>
  summary: {
    departures: number
    systems: number
    rho: { understudy: Estimate; knowers: Estimate }
    groups: { low: Estimate & { n: number }; high: Estimate & { n: number } }
  }
  events: ProofEvent[]
  units: ProofUnit[]
}

export const api = {
  proof: () => request<Proof>('/api/proof'),
  board: (org: Org, session?: string | null) =>
    request<BoardRow[]>(`/api/systems${query({ org, session })}`),
  stage: (org: Org, name: string, session?: string | null) =>
    request<Stage>(`/api/systems/${systemPath(name)}${query({ org, session })}`),
  rehearse: (org: Org, people: string[], session?: string | null) =>
    request<Rehearsal>(`/api/rehearse${query({ org, session })}`, {
      method: 'POST',
      body: JSON.stringify({ people }),
    }),
  fixes: (name: string, session?: string | null) =>
    request<Proposal>(`/api/fixes/${systemPath(name)}${query({ session })}`),
  startSession: () => request<{ id: string }>('/api/sessions', { method: 'POST' }),
  applyFix: (session: string, system: string, fix: Fix) =>
    request<{ changes: Change[] }>(`/api/sessions/${session}/fix`, {
      method: 'POST',
      body: JSON.stringify({
        system,
        kind: fix.kind,
        knower: fix.knower,
        approver: fix.approver,
      }),
    }),
  depart: (session: string, people: string[]) =>
    request<Rehearsal>(`/api/sessions/${session}/depart`, {
      method: 'POST',
      body: JSON.stringify({ people }),
    }),
  resetSession: (session: string) =>
    request<{ id: string }>(`/api/sessions/${session}/reset`, { method: 'POST' }),
}

/** Server-sent events arrive in chunks that can split anywhere. Returns the complete
 * events in `buffer` and whatever is left over for the next chunk. */
export function parseEvents(buffer: string): { events: AskEvent[]; rest: string } {
  const events: AskEvent[] = []
  const blocks = buffer.replace(/\r\n/g, '\n').split('\n\n')
  const rest = blocks.pop() ?? ''
  for (const block of blocks) {
    const data = block
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).replace(/^ /, ''))
      .join('\n')
    if (!data) continue
    try {
      events.push(JSON.parse(data) as AskEvent)
    } catch {
      /* a malformed event is skipped rather than ending the stream */
    }
  }
  return { events, rest }
}

export async function ask(
  question: string,
  history: { role: 'user' | 'assistant'; content: string }[],
  opts: { session: string | null; visitor: string; signal?: AbortSignal },
  onEvent: (e: AskEvent) => void,
): Promise<void> {
  let res: Response
  try {
    res = await fetch(`/api/ask/stream${query({ session: opts.session, visitor: opts.visitor })}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ question, history }),
      signal: opts.signal,
    })
  } catch (e) {
    if ((e as Error).name === 'AbortError') return
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.")
  }
  if (!res.ok || !res.body) {
    let detail = `The server answered ${res.status}.`
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(res.status, detail)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const { events, rest } = parseEvents(buffer)
    buffer = rest
    events.forEach(onEvent)
  }
  parseEvents(buffer + '\n\n').events.forEach(onEvent)
}
