import { describe, expect, it } from 'vitest'
import type { Stage } from '../api'
import { MAX_ROWS, layoutStage } from './stageLayout'

const person = (handle: string, extra = {}) => ({
  handle,
  on_route: false,
  critical: false,
  off_stage: false,
  ...extra,
})

// billing from the sample org: ana, ben and cara know it; dev approves; one route ana -> dev.
const billing: Stage = {
  name: 'billing',
  understudy: 1,
  team: 'payments',
  sigs: [],
  emeritus_approvers: 0,
  inactive_owners: null,
  knowers: ['ana', 'ben', 'cara'].map((h, i) => ({ ...person(h), score: 3 - i })),
  approvers: [{ ...person('dev', { on_route: true, critical: true }), knows: false }],
  routes: [['ana', 'dev']],
}

describe('layoutStage', () => {
  it('puts each person in one column and draws the route through both', () => {
    const l = layoutStage(billing)
    expect(l.people.map((p) => `${p.column}:${p.handle}`)).toEqual([
      'knower:ana',
      'knower:ben',
      'knower:cara',
      'approver:dev',
    ])
    const route = l.edges.filter((e) => e.kind === 'route').map((e) => e.people)
    expect(route).toEqual([['ana'], ['ana', 'dev'], ['dev']])
  })

  it('shows an approver who knows the system once, with the approvers', () => {
    const both: Stage = {
      ...billing,
      knowers: [...billing.knowers, { ...person('dev'), score: 2 }],
      approvers: [{ ...person('dev', { on_route: true }), knows: true }],
      routes: [['dev']],
    }
    const l = layoutStage(both)
    expect(l.people.filter((p) => p.handle === 'dev').map((p) => p.column)).toEqual(['approver'])
    const knows = l.edges.filter((e) => e.kind === 'knows').map((e) => e.people[0])
    expect(knows).toContain('dev')
  })

  it('caps long columns, keeping people on a route', () => {
    const many: Stage = {
      ...billing,
      knowers: Array.from({ length: 20 }, (_, i) => ({ ...person(`k${i}`), score: 20 - i })),
      routes: [['k19', 'dev']], // the weakest knower is the one on the route
    }
    const l = layoutStage(many)
    const shown = l.people.filter((p) => p.column === 'knower').map((p) => p.handle)
    expect(shown).toHaveLength(MAX_ROWS)
    expect(shown[0]).toBe('k19')
    expect(l.more.knower).toBe(20 - MAX_ROWS)
  })

  it('draws routes passed in, such as the ones left after a rehearsal', () => {
    const l = layoutStage(billing, [])
    expect(l.edges.some((e) => e.kind === 'route')).toBe(false)
    expect(l.people.every((p) => !p.on_route)).toBe(true)
  })
})

describe('lines to an approver who knows the system', () => {
  it('run over the top of the knowers column, not through it', () => {
    const both: Stage = {
      ...billing,
      knowers: [...billing.knowers, { ...person('dev'), score: 2 }],
      approvers: [{ ...person('dev', { on_route: true }), knows: true }],
      routes: [['dev']],
    }
    const l = layoutStage(both)
    const topKnower = Math.min(...l.people.filter((p) => p.column === 'knower').map((p) => p.y))
    const direct = l.edges.find((e) => e.id === 'route:0:in')!
    expect(direct.via).toBeDefined()
    expect(direct.via!).toBeLessThan(topKnower)
    // Lines into knowers stay ordinary curves.
    expect(l.edges.find((e) => e.id === 'knows:ana')!.via).toBeUndefined()
  })
})
