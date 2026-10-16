import type { KeyboardEvent } from 'react'
import type { Stage as StageData } from '../api'
import { shortHandle, shortPath, splitPath } from '../lib/format'
import { COLUMN_X, NODE_W, ROW_H, WIDTH, curve, layoutStage } from '../lib/stageLayout'

interface Props {
  stage: StageData
  /** Routes to draw instead of the stage's own, e.g. what's left after a rehearsal. */
  routes?: string[][]
  /** People taken off stage in a rehearsal (session departures come in `stage`). */
  off?: Set<string>
  selected?: Set<string>
  highlight?: Set<string>
  onTogglePerson?: (handle: string) => void
  /** Sample org names are short; Kubernetes pseudonyms get trimmed to fit. */
  compactNames?: boolean
}

const H = ROW_H - 10

function teamLabel(team: string): { lead: string; tail: string } {
  const teams = team.split(', ')
  if (teams.length > 1) return { lead: `${teams.length} teams`, tail: teams.join(' + ') }
  if (team.endsWith('/OWNERS')) return { lead: `${shortPath(team.slice(0, -'/OWNERS'.length))}/`, tail: 'OWNERS' }
  return { lead: '', tail: team }
}

export function Stage({
  stage,
  routes,
  off = new Set(),
  selected = new Set(),
  highlight = new Set(),
  onTogglePerson,
  compactNames = false,
}: Props) {
  const layout = layoutStage(stage, routes)
  const gone = (h: string) => off.has(h)
  const sys = splitPath(stage.name)
  const team = teamLabel(stage.team)
  const routeCount = (routes ?? stage.routes).length

  const onKey = (handle: string) => (e: KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onTogglePerson?.(handle)
    }
  }

  return (
    <svg
      className="stage-svg"
      viewBox={`0 0 ${WIDTH} ${layout.height}`}
      role="group"
      aria-label={`${stage.name}: ${stage.knowers.length} people know it, ${stage.approvers.length} approve it, ${routeCount} independent routes`}
    >
      <g className="stage-columns" aria-hidden="true">
        <text x={COLUMN_X.system} y={22}>System</text>
        <text x={COLUMN_X.knower} y={22}>Know it</text>
        <text x={COLUMN_X.approver} y={22}>Approve it</text>
        <text x={COLUMN_X.team} y={22}>Team</text>
      </g>

      <g className="stage-edges" aria-hidden="true">
        {layout.edges
          .filter((e) => e.kind !== 'route')
          .map((e) => (
            <path
              key={e.id}
              d={curve(e.from, e.to, e.via)}
              className={`edge edge-${e.kind}${e.people.some(gone) ? ' is-gone' : ''}`}
            />
          ))}
        {layout.edges
          .filter((e) => e.kind === 'route')
          .map((e) => (
            <path key={e.id} d={curve(e.from, e.to, e.via)} className="edge edge-route" />
          ))}
      </g>

      <g className="stage-node stage-system" aria-hidden="true">
        <rect
          x={layout.system.x - NODE_W.system / 2}
          y={layout.system.y - 24}
          width={NODE_W.system}
          height={48}
          rx={10}
        />
        {sys.lead && (
          <text x={layout.system.x} y={layout.system.y - 6} className="node-lead">
            {sys.lead.length > 24 ? `…${sys.lead.slice(-23)}` : sys.lead}
          </text>
        )}
        <text x={layout.system.x} y={layout.system.y + (sys.lead ? 13 : 5)} className="node-tail">
          {sys.tail}
        </text>
      </g>

      <g className="stage-node stage-team" aria-hidden="true">
        <rect
          x={layout.team.x - NODE_W.team / 2}
          y={layout.team.y - 24}
          width={NODE_W.team}
          height={48}
          rx={10}
        />
        {team.lead && (
          <text x={layout.team.x} y={layout.team.y - 6} className="node-lead">
            {team.lead.length > 24 ? `…${team.lead.slice(-23)}` : team.lead}
          </text>
        )}
        <text x={layout.team.x} y={layout.team.y + (team.lead ? 13 : 5)} className="node-tail">
          {team.tail}
        </text>
      </g>

      {layout.people.map((p) => {
        const isGone = p.off_stage || gone(p.handle)
        const cls = [
          'stage-node',
          'stage-person',
          p.on_route && !isGone ? 'is-route' : '',
          p.critical && !isGone ? 'is-critical' : '',
          isGone ? 'is-gone' : '',
          selected.has(p.handle) && !isGone ? 'is-selected' : '',
          highlight.has(p.handle) ? 'is-highlight' : '',
        ]
          .filter(Boolean)
          .join(' ')
        const label = `${p.handle}${p.column === 'approver' ? ', approves' : ''}${p.knows ? ', knows it' : ''}${p.on_route ? ', on a route' : ''}${p.critical ? ', critical' : ''}${isGone ? ', off stage' : ''}`
        const name = compactNames ? p.handle : shortHandle(p.handle)
        const interactive = !!onTogglePerson && !p.off_stage
        return (
          <g
            key={p.id}
            className={cls}
            transform={`translate(${p.x - NODE_W.person / 2}, ${p.y - H / 2})`}
            role={interactive ? 'button' : 'img'}
            tabIndex={interactive ? 0 : undefined}
            aria-pressed={interactive ? selected.has(p.handle) : undefined}
            aria-label={label}
            onClick={interactive ? () => onTogglePerson?.(p.handle) : undefined}
            onKeyDown={interactive ? onKey(p.handle) : undefined}
          >
            <title>{label}</title>
            <rect className="halo" x={-4} y={-4} width={NODE_W.person + 8} height={H + 8} rx={H / 2 + 4} />
            <rect width={NODE_W.person} height={H} rx={H / 2} />
            <text x={NODE_W.person / 2} y={H / 2 + 4.5}>
              {name}
            </text>
            {p.column === 'approver' && p.knows && (
              <circle className="knows-dot" cx={14} cy={H / 2} r={3.5}>
                <title>Also knows the system</title>
              </circle>
            )}
          </g>
        )
      })}

      {(['knower', 'approver'] as const).map((col) =>
        layout.more[col] ? (
          <text key={col} x={COLUMN_X[col]} y={layout.moreY[col] + 4} className="stage-more">
            +{layout.more[col]} more
          </text>
        ) : null,
      )}
    </svg>
  )
}
