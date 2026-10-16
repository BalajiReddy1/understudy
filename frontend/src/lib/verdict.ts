import type { Stage } from '../api'
import { plural } from './format'

/** One sentence on what the number means for this system, from the data alone. */
export function verdict(stage: Stage, routes: string[][], gone: Set<string>): string {
  const n = routes.length
  const critical = [...stage.knowers, ...stage.approvers]
    .filter((p) => p.critical && !gone.has(p.handle) && !p.off_stage)
    .map((p) => p.handle)
  const unique = [...new Set(critical)]
  if (n === 0) return 'No approver can reach anyone who knows it.'
  // Who is critical was worked out before the departure, so it isn't named during one.
  if (gone.size > 0) return `${plural(n, 'route')} would still reach the approvers.`
  const losing = unique.length === 1 ? `Losing ${unique[0]}` : `Losing any one of ${unique.join(', ')}`
  if (n === 1) {
    return unique.length
      ? `Only one route reaches an approver. ${losing} would cut it.`
      : 'Only one route reaches an approver.'
  }
  return unique.length
    ? `${n} separate routes reach the approvers. ${losing} would take one away.`
    : `${n} separate routes reach the approvers, and nobody is critical to them.`
}
