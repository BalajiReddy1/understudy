import type { Estimate, Proof } from '../api'

export type Verdict = 'supported' | 'contradicted' | 'unproven'

/** What the understudy number's correlation says, read the only honest way: the claim
 * is that a lower number means a bigger slowdown, so rho should be negative, and only
 * an interval that stays on one side of zero counts as evidence either way. */
export function verdict(rho: Estimate): Verdict {
  if (rho.low === null || rho.high === null) return 'unproven'
  if (rho.high < 0) return 'supported'
  if (rho.low > 0) return 'contradicted'
  return 'unproven'
}

export interface Facts {
  departures: number
  /** Departures that passed every rule. */
  kept: number
  /** Departures with at least one system busy enough to measure. */
  measured: number
  systems: { touched: number; measured: number }
  /** Why departures were left out, most common first. */
  dropped: { reason: string; count: number }[]
  /** Systems whose number the departure actually lowered. */
  lowered: { touched: number; measured: number }
  /** The departure with the most measured systems, and how many. */
  largest: { person: string; systems: number }
}

export function facts(p: Proof): Facts {
  const used = p.units.filter((u) => u.reason === null)
  const reasons = new Map<string, number>()
  for (const e of p.events) {
    if (e.reason) reasons.set(e.reason, (reasons.get(e.reason) ?? 0) + 1)
  }
  const perPerson = new Map<string, number>()
  for (const u of used) perPerson.set(u.person, (perPerson.get(u.person) ?? 0) + 1)
  const [person, systems] = [...perPerson].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0] ?? ['', 0]
  const lowered = (u: (typeof p.units)[number]) => u.understudy_after < u.understudy_before
  return {
    departures: p.events.length,
    kept: p.events.filter((e) => e.reason === null).length,
    measured: perPerson.size,
    systems: { touched: p.units.length, measured: used.length },
    dropped: [...reasons].map(([reason, count]) => ({ reason, count })).sort((a, b) => b.count - a.count),
    lowered: { touched: p.units.filter(lowered).length, measured: used.filter(lowered).length },
    largest: { person, systems },
  }
}

/** -0.236 as "−0.24", with a real minus sign. */
export function signed(x: number | null, digits = 2): string {
  if (x === null) return 'n/a'
  const s = Math.abs(x).toFixed(digits)
  return x < 0 && Number(s) !== 0 ? `−${s}` : s
}

/** "−0.60 to 0.80" */
export function range(e: Estimate): string {
  return e.low === null || e.high === null ? 'no interval' : `${signed(e.low)} to ${signed(e.high)}`
}

/** A slowdown ratio in words: 2.16 -> "2.2× slower", 0.77 -> "1.3× faster". */
export function pace(ratio: number): string {
  if (Math.abs(ratio - 1) < 0.05) return 'about the same'
  return ratio > 1 ? `${ratio.toFixed(1)}× slower` : `${(1 / ratio).toFixed(1)}× faster`
}

/** A system's slowdown, which is already relative to the project, said so. */
export function againstProject(ratio: number): string {
  const p = pace(ratio)
  return p === 'about the same' ? 'About the same as the project' : `${p} than the project`
}

/** "2025-07-19" -> "July 2025" */
export function month(day: string): string {
  const d = new Date(`${day}T00:00:00Z`)
  return d.toLocaleDateString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' })
}
