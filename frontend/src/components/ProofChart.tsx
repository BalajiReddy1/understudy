import { useState } from 'react'
import type { Estimate, ProofUnit } from '../api'
import { againstProject, month, pace, range, signed } from '../lib/proof'
import { BINS, bin, dodge, logDomain, logScale, logTicks, ratioTick } from '../lib/proofChart'

const W = 400
const H = 300
const M = { top: 14, right: 14, bottom: 46, left: 40 }
const R = 4.5
const GAP = 2 * R + 3 // dots in one column closer than this get moved sideways

interface Props {
  units: ProofUnit[] // measured ones only
  rho: { understudy: Estimate; knowers: Estimate }
}

const key = (u: ProofUnit) => `${u.person}|${u.system}`

interface Dot {
  unit: ProofUnit
  x: number
  y: number
}

function Tooltip({ dot }: { dot: Dot }) {
  const u = dot.unit
  const left = (dot.x / W) * 100
  return (
    <div
      className={`proof-tip${left > 60 ? ' is-left' : ''}`}
      style={{ left: `${left}%`, top: `${(dot.y / H) * 100}%` }}
      role="status"
    >
      <p className="proof-tip-value">{againstProject(u.slowdown!)}</p>
      <p className="proof-tip-system">{u.system}</p>
      <p>
        Someone who knew or approved it left in {month(u.left_at)}. Understudy number{' '}
        {u.understudy_before} → {u.understudy_after}; {u.knowers_after} still know it.
      </p>
      <p>
        Median first response {u.hours_before} h → {u.hours_after} h. Across the whole project:{' '}
        {pace(u.project_ratio)}.
      </p>
    </div>
  )
}

function Grid({ y, ticks }: { y: (v: number) => number; ticks: number[] }) {
  return (
    <g className="proof-grid">
      {ticks.map((t) => (
        <g key={t}>
          <line x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} className={t === 1 ? 'is-one' : undefined} />
          <text x={M.left - 6} y={y(t)} dy="0.32em" textAnchor="end">
            {ratioTick(t)}
          </text>
        </g>
      ))}
    </g>
  )
}

export function ProofChart({ units, rho }: Props) {
  const [active, setActive] = useState<{ key: string; panel: 'a' | 'b' } | null>(null)
  const yDomain = logDomain(units.map((u) => u.slowdown!))
  const y = logScale(yDomain, [H - M.bottom, M.top])
  const yTicks = logTicks(yDomain)

  // Panel A: one column per understudy number.
  const band = (W - M.left - M.right) / BINS.length
  const colX = (i: number) => M.left + band * (i + 0.5)
  const offsets = new Map<string, number>()
  BINS.forEach((_, i) => {
    const col = units.filter((u) => bin(u.understudy_after) === i)
    const dx = dodge(
      col.map((u) => y(u.slowdown!)),
      GAP,
    )
    col.forEach((u, j) => offsets.set(key(u), dx[j]))
  })
  const dotsA: Dot[] = units.map((u) => ({
    unit: u,
    x: colX(bin(u.understudy_after)) + (offsets.get(key(u)) ?? 0),
    y: y(u.slowdown!),
  }))
  const emptyLow = !units.some((u) => u.understudy_after <= 1)

  // Panel B: people who still know it, on a log scale.
  const kDomain = logDomain(
    units.map((u) => Math.max(1, u.knowers_after)),
    false,
  )
  const kx = logScale(kDomain, [M.left + 8, W - M.right - 8])
  const dotsB: Dot[] = units.map((u) => ({ unit: u, x: kx(Math.max(1, u.knowers_after)), y: y(u.slowdown!) }))

  const shown = (dots: Dot[], panel: 'a' | 'b') =>
    active?.panel === panel ? dots.find((d) => key(d.unit) === active.key) : undefined

  const dots = (list: Dot[], panel: 'a' | 'b') =>
    list.map((d) => {
      const k = key(d.unit)
      const on = active?.key === k
      return (
        <g
          key={k}
          className={`proof-dot${on ? ' is-active' : ''}`}
          tabIndex={panel === 'a' ? 0 : -1}
          aria-hidden={panel === 'b' ? true : undefined}
          aria-label={
            panel === 'a'
              ? `${d.unit.system}: understudy number ${d.unit.understudy_after}, ${againstProject(d.unit.slowdown!)}`
              : undefined
          }
          onPointerEnter={() => setActive({ key: k, panel })}
          onPointerLeave={() => setActive(null)}
          onFocus={() => setActive({ key: k, panel })}
          onBlur={() => setActive(null)}
        >
          <circle className="hit" cx={d.x} cy={d.y} r={12} />
          <circle className="mark" cx={d.x} cy={d.y} r={on ? R + 1.5 : R} />
        </g>
      )
    })

  const tipA = shown(dotsA, 'a')
  const tipB = shown(dotsB, 'b')

  return (
    <div className="proof-chart">
      <figure className="proof-panel">
        <figcaption>
          <span className="proof-panel-title">By understudy number after they left</span>
          <span className="proof-rho">
            ρ = {signed(rho.understudy.value)} <span>({range(rho.understudy)})</span>
          </span>
        </figcaption>
        <div className="proof-plot">
          <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Slowdown by understudy number after the departure">
            {emptyLow && (
              <g className="proof-empty">
                <rect x={M.left} y={M.top} width={band * 2} height={H - M.top - M.bottom} />
                <text x={M.left + band} y={(M.top + H - M.bottom) / 2} textAnchor="middle">
                  <tspan x={M.left + band}>no system</tspan>
                  <tspan x={M.left + band} dy="1.2em">measured</tspan>
                  <tspan x={M.left + band} dy="1.2em">at 1 or 0</tspan>
                </text>
              </g>
            )}
            <Grid y={y} ticks={yTicks} />
            <g className="proof-axis">
              {BINS.map((b, i) => (
                <text key={b} x={colX(i)} y={H - M.bottom + 18} textAnchor="middle">
                  {b}
                </text>
              ))}
              <text x={(M.left + W - M.right) / 2} y={H - 8} textAnchor="middle" className="proof-axis-title">
                understudy number left
              </text>
            </g>
            {dots(dotsA, 'a')}
          </svg>
          {tipA && <Tooltip dot={tipA} />}
        </div>
      </figure>

      <figure className="proof-panel">
        <figcaption>
          <span className="proof-panel-title">By people who still know it</span>
          <span className="proof-rho">
            ρ = {signed(rho.knowers.value)} <span>({range(rho.knowers)})</span>
          </span>
        </figcaption>
        <div className="proof-plot">
          <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Slowdown by how many people still know the system">
            <Grid y={y} ticks={yTicks} />
            <g className="proof-axis">
              {logTicks(kDomain).map((t) => (
                <text key={t} x={kx(t)} y={H - M.bottom + 18} textAnchor="middle">
                  {t}
                </text>
              ))}
              <text x={(M.left + W - M.right) / 2} y={H - 8} textAnchor="middle" className="proof-axis-title">
                people who know it (log scale)
              </text>
            </g>
            {dots(dotsB, 'b')}
          </svg>
          {tipB && <Tooltip dot={tipB} />}
        </div>
      </figure>
    </div>
  )
}
