import { describe, expect, it } from 'vitest'
import type { Estimate, Proof, ProofUnit } from '../api'
import { againstProject, facts, month, pace, range, signed, verdict } from './proof'
import { BINS, bin, dodge, logDomain, logScale, logTicks, ratioTick } from './proofChart'

const est = (value: number | null, low: number | null, high: number | null): Estimate => ({
  value,
  low,
  high,
  resamples: 2000,
})

describe('verdict', () => {
  it('only counts an interval that stays on one side of zero', () => {
    expect(verdict(est(-0.24, -0.6, 0.8))).toBe('unproven')
    expect(verdict(est(-0.5, -0.8, -0.1))).toBe('supported') // lower number, slower reviews
    expect(verdict(est(0.5, 0.1, 0.8))).toBe('contradicted')
    expect(verdict(est(null, null, null))).toBe('unproven')
    expect(verdict(est(-0.3, -0.6, 0))).toBe('unproven') // touching zero isn't clear of it
  })
})

const unit = (person: string, before: number, after: number, slowdown: number | null): ProofUnit => ({
  person,
  left_at: '2025-07-19',
  system: `s-${person}-${before}-${after}-${slowdown}`,
  understudy_before: before,
  understudy_after: after,
  knowers_before: 5,
  knowers_after: 4,
  prs_before: 9,
  prs_after: 9,
  hours_before: 2,
  hours_after: 3,
  project_ratio: 1,
  slowdown,
  reason: slowdown === null ? 'fewer than 5 PRs in a window' : null,
})

const proof = {
  events: [
    { person: 'a', left_at: '2025-01-01', prs_before: 9, reason: null },
    { person: 'b', left_at: '2025-02-01', prs_before: 9, reason: null },
    { person: 'c', left_at: '2025-03-01', prs_before: 9, reason: null }, // nothing measurable
    { person: null, left_at: '2024-02-01', prs_before: 0, reason: 'fewer than 3 PRs in the 18 months before' },
    { person: 'e', left_at: '2024-03-01', prs_before: 1, reason: 'fewer than 3 PRs in the 18 months before' },
    { person: 'f', left_at: '2026-10-01', prs_before: 9, reason: 'too recent to see 90 days after' },
  ],
  units: [unit('a', 3, 3, 1.2), unit('a', 4, 4, 0.8), unit('a', 2, 1, null), unit('b', 2, 1, 2), unit('c', 3, 3, null)],
} as unknown as Proof

describe('facts', () => {
  it('counts the funnel from every departure to the systems measured', () => {
    const f = facts(proof)
    expect(f.departures).toBe(6)
    expect(f.kept).toBe(3)
    expect(f.measured).toBe(2) // c passed the rules but had no system busy enough
    expect(f.systems).toEqual({ touched: 5, measured: 3 })
    expect(f.dropped).toEqual([
      { reason: 'fewer than 3 PRs in the 18 months before', count: 2 },
      { reason: 'too recent to see 90 days after', count: 1 },
    ])
    expect(f.lowered).toEqual({ touched: 2, measured: 1 })
    expect(f.largest).toEqual({ person: 'a', systems: 2 })
  })
})

describe('words', () => {
  it('formats signs, ranges, pace and months', () => {
    expect(signed(-0.236)).toBe('−0.24')
    expect(signed(0.797)).toBe('0.80')
    expect(signed(-0.001)).toBe('0.00') // no "−0.00"
    expect(signed(null)).toBe('n/a')
    expect(range(est(-0.236, -0.603, 0.797))).toBe('−0.60 to 0.80')
    expect(range(est(null, null, null))).toBe('no interval')
    expect(pace(2.16)).toBe('2.2× slower')
    expect(pace(0.771)).toBe('1.3× faster')
    expect(pace(1.02)).toBe('about the same')
    expect(month('2025-07-19')).toBe('July 2025')
    expect(againstProject(0.771)).toBe('1.3× faster than the project')
    expect(againstProject(1.01)).toBe('About the same as the project')
  })
})

describe('chart geometry', () => {
  it('fits a power-of-two domain around the data and 1', () => {
    expect(logDomain([0.1397, 3.82])).toEqual([0.125, 4])
    expect(logDomain([1.5, 2.5])).toEqual([1, 4]) // 1 is on the chart by default
    expect(logDomain([6, 93], false)).toEqual([4, 128]) // a headcount axis needn't start at 1
    expect(logTicks([0.125, 4])).toEqual([0.125, 0.25, 0.5, 1, 2, 4])
    expect(ratioTick(0.25)).toBe('1/4×')
    expect(ratioTick(0.125)).toBe('1/8×')
    expect(ratioTick(4)).toBe('4×')
  })

  it('maps a log domain onto pixels, upside down for a y axis', () => {
    const y = logScale([0.25, 4], [200, 0])
    expect(y(0.25)).toBe(200)
    expect(y(4)).toBe(0)
    expect(y(1)).toBe(100) // 1 sits halfway between ¼ and 4 on a log scale
  })

  it('puts understudy numbers in columns, 7 and up together', () => {
    expect([0, 1, 6, 7, 19].map(bin)).toEqual([0, 1, 6, 7, 7])
    expect(BINS[bin(19)]).toBe('7+')
  })

  it('moves overlapping dots sideways and leaves the rest alone', () => {
    const ys = [100, 104, 108, 160]
    const dx = dodge(ys, 10)
    expect(dx[3]).toBe(0) // far from the others
    expect(dx[0]).toBe(0) // the first one placed stays in the middle
    expect(new Set(dx.slice(0, 3)).size).toBe(3) // three close dots, three positions
    // No two dots in the same position overlap.
    for (let i = 0; i < ys.length; i++)
      for (let j = i + 1; j < ys.length; j++)
        if (dx[i] === dx[j]) expect(Math.abs(ys[i] - ys[j])).toBeGreaterThanOrEqual(10)
    expect(dodge(ys, 10)).toEqual(dx) // the same every time
  })
})
