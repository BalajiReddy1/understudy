import { describe, expect, it } from 'vitest'
import type { Stage } from '../api'
import { verdict } from './verdict'

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
  knowers: [
    { ...person('ana', { on_route: true }), score: 3 },
    { ...person('ben'), score: 2 },
  ],
  approvers: [{ ...person('dev', { on_route: true, critical: true }), knows: false }],
  routes: [['ana', 'dev']],
}
const none = new Set<string>()

describe('verdict', () => {
  it('names the one person a single route depends on', () => {
    expect(verdict(billing, billing.routes, none)).toBe(
      'Only one route reaches an approver. Losing dev would cut it.',
    )
  })

  it('names someone who both knows and approves only once', () => {
    const both: Stage = {
      ...billing,
      knowers: [{ ...person('dev', { critical: true }), score: 2 }],
      approvers: [{ ...person('dev', { critical: true }), knows: true }],
      routes: [['dev']],
    }
    expect(verdict(both, both.routes, none)).toBe('Only one route reaches an approver. Losing dev would cut it.')
  })

  it('lists everyone critical when there are several routes', () => {
    const two: Stage = {
      ...billing,
      knowers: [
        { ...person('ana', { critical: true }), score: 3 },
        { ...person('ben'), score: 2 },
      ],
      approvers: [
        { ...person('dev', { critical: true }), knows: false },
        { ...person('eve'), knows: true },
      ],
      routes: [['ana', 'dev'], ['eve']],
    }
    expect(verdict(two, two.routes, none)).toBe(
      '2 separate routes reach the approvers. Losing any one of ana, dev would take one away.',
    )
  })

  it('says so when nobody is critical', () => {
    const safe: Stage = { ...billing, approvers: [{ ...person('dev'), knows: true }], routes: [['dev'], ['ana']] }
    expect(verdict(safe, safe.routes, none)).toBe(
      '2 separate routes reach the approvers, and nobody is critical to them.',
    )
  })

  it('leaves out people already taken off stage', () => {
    const off: Stage = {
      ...billing,
      approvers: [{ ...person('dev', { critical: true, off_stage: true }), knows: false }],
    }
    expect(verdict(off, off.routes, none)).toBe('Only one route reaches an approver.')
  })

  it('counts what is left during a rehearsal without naming anyone', () => {
    expect(verdict(billing, [['ben', 'dev']], new Set(['ana']))).toBe('1 route would still reach the approvers.')
  })

  it('says when no route is left', () => {
    expect(verdict(billing, [], new Set(['dev']))).toBe('No approver can reach anyone who knows it.')
  })
})
