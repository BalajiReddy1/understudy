import type { Stage, StagePerson } from '../api'

// Where everything on the stage goes. Four fixed columns, so the picture never jumps
// between renders: the system, people who know it, its approvers, the team. An
// approver who also knows the system sits with the approvers, marked "knows", and the
// line from the system to them runs over the top of the knowers column ("via"), so it
// can't be mistaken for a route through someone who knows it.

export const WIDTH = 740
export const COLUMN_X = { system: 84, knower: 272, approver: 456, team: 652 }
export const NODE_W = { system: 150, person: 120, team: 150 }
export const ROW_H = 42
export const TOP = 54
export const MAX_ROWS = 9

export interface PersonNode extends StagePerson {
  id: string
  column: 'knower' | 'approver'
  x: number
  y: number
  knows: boolean
  score?: number
}

export interface Edge {
  id: string
  from: { x: number; y: number }
  to: { x: number; y: number }
  kind: 'knows' | 'approves' | 'route'
  people: string[] // the people this edge touches, for dimming when they leave
  via?: number // y of the lane above the knowers column, for system -> approver lines
}

export interface Layout {
  height: number
  system: { x: number; y: number }
  team: { x: number; y: number }
  people: PersonNode[]
  more: { knower: number; approver: number }
  moreY: { knower: number; approver: number }
  edges: Edge[]
}

const routeFirst = (a: StagePerson, b: StagePerson) =>
  Number(b.on_route) - Number(a.on_route) || Number(b.critical) - Number(a.critical)

export function layoutStage(stage: Stage, routes: string[][] = stage.routes): Layout {
  const onRoute = new Set(routes.flat())
  const approverHandles = new Set(stage.approvers.map((a) => a.handle))

  const knowers = stage.knowers
    .filter((k) => !approverHandles.has(k.handle))
    .map((k) => ({ ...k, on_route: onRoute.has(k.handle) }))
    .sort((a, b) => routeFirst(a, b) || b.score - a.score || a.handle.localeCompare(b.handle))
  const approvers = stage.approvers
    .map((a) => ({ ...a, on_route: onRoute.has(a.handle) }))
    .sort(
      (a, b) =>
        routeFirst(a, b) || Number(b.knows) - Number(a.knows) || a.handle.localeCompare(b.handle),
    )

  const shownK = knowers.slice(0, MAX_ROWS)
  const shownA = approvers.slice(0, MAX_ROWS)
  const rowsK = shownK.length + (knowers.length > MAX_ROWS ? 1 : 0)
  const rowsA = shownA.length + (approvers.length > MAX_ROWS ? 1 : 0)
  const rows = Math.max(rowsK, rowsA, 3)
  const height = TOP + rows * ROW_H + 24
  const mid = TOP + (rows * ROW_H) / 2
  const top = (n: number) => mid - (n * ROW_H) / 2 + ROW_H / 2

  const people: PersonNode[] = [
    ...shownK.map((k, i) => ({
      ...k,
      id: `k:${k.handle}`,
      column: 'knower' as const,
      x: COLUMN_X.knower,
      y: top(rowsK) + i * ROW_H,
      knows: true,
    })),
    ...shownA.map((a, i) => ({
      ...a,
      id: `a:${a.handle}`,
      column: 'approver' as const,
      x: COLUMN_X.approver,
      y: top(rowsA) + i * ROW_H,
    })),
  ]
  const at = new Map(people.map((p) => [p.handle, p]))
  const system = { x: COLUMN_X.system, y: mid }
  const team = { x: COLUMN_X.team, y: mid }

  const right = (x: number, w: number) => x + w / 2
  const left = (x: number, w: number) => x - w / 2
  const sysOut = { x: right(system.x, NODE_W.system), y: system.y }
  const teamIn = { x: left(team.x, NODE_W.team), y: team.y }
  const outOf = (p: PersonNode) => ({ x: right(p.x, NODE_W.person), y: p.y })
  const into = (p: PersonNode) => ({ x: left(p.x, NODE_W.person), y: p.y })
  const firstKnower = people.find((p) => p.column === 'knower')
  const lane = firstKnower ? firstKnower.y - ROW_H / 2 - 4 : undefined
  const fromSystem = (p: PersonNode) => (p.column === 'approver' ? { via: lane } : {})

  const edges: Edge[] = []
  for (const p of people) {
    if (p.knows) {
      edges.push({
        id: `knows:${p.handle}`,
        from: sysOut,
        to: into(p),
        kind: 'knows',
        people: [p.handle],
        ...fromSystem(p),
      })
    }
    if (p.column === 'approver') {
      edges.push({ id: `approves:${p.handle}`, from: outOf(p), to: teamIn, kind: 'approves', people: [p.handle] })
    }
  }
  routes.forEach((route, i) => {
    const nodes = route.map((h) => at.get(h)).filter((n): n is PersonNode => !!n)
    if (nodes.length !== route.length || nodes.length === 0) return // someone not drawn
    const first = nodes[0]
    const last = nodes[nodes.length - 1]
    edges.push({
      id: `route:${i}:in`,
      from: sysOut,
      to: into(first),
      kind: 'route',
      people: [first.handle],
      ...fromSystem(first),
    })
    if (nodes.length === 2) {
      edges.push({
        id: `route:${i}:hand`,
        from: outOf(first),
        to: into(last),
        kind: 'route',
        people: route,
      })
    }
    edges.push({ id: `route:${i}:out`, from: outOf(last), to: teamIn, kind: 'route', people: [last.handle] })
  })

  return {
    height,
    system,
    team,
    people,
    more: {
      knower: Math.max(0, knowers.length - MAX_ROWS),
      approver: Math.max(0, approvers.length - MAX_ROWS),
    },
    moreY: { knower: top(rowsK) + shownK.length * ROW_H, approver: top(rowsA) + shownA.length * ROW_H },
    edges,
  }
}

/** A horizontal S-curve between two points, or, with `via`, one that climbs to a lane
 * above the knowers column, runs along it and comes down to the approver. */
export function curve(a: { x: number; y: number }, b: { x: number; y: number }, via?: number): string {
  if (via === undefined) {
    const dx = Math.max(24, (b.x - a.x) * 0.5)
    return `M${a.x},${a.y} C${a.x + dx},${a.y} ${b.x - dx},${b.y} ${b.x},${b.y}`
  }
  const l = COLUMN_X.knower - NODE_W.person / 2
  const r = COLUMN_X.knower + NODE_W.person / 2
  const up = (l - a.x) / 2
  const down = (b.x - r) / 2
  return `M${a.x},${a.y} C${a.x + up},${a.y} ${l - up},${via} ${l},${via} L${r},${via} C${r + down},${via} ${b.x - down},${b.y} ${b.x},${b.y}`
}
