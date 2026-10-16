import { useMemo, useState } from 'react'
import type { BoardRow, Rehearsal } from '../api'
import { plural, splitPath } from '../lib/format'

interface Props {
  rows: BoardRow[] | null
  error: string | null
  selected: string | null
  rehearsal: Rehearsal | null
  onSelect: (name: string) => void
}

export function Board({ rows, error, selected, rehearsal, onSelect }: Props) {
  const [filter, setFilter] = useState('')
  const after = useMemo(
    () => new Map((rehearsal?.changes ?? []).map((c) => [c.system, c.after])),
    [rehearsal],
  )
  const shown = (rows ?? []).filter((r) => r.name.toLowerCase().includes(filter.trim().toLowerCase()))
  const atOne = (rows ?? []).filter((r) => (after.get(r.name) ?? r.understudy) <= 1).length

  return (
    <section className="board" aria-labelledby="board-title">
      <header className="board-head">
        <h2 id="board-title">Call board</h2>
        {rows && (
          <p className="board-sum">
            {plural(rows.length, 'system')} · {atOne} at 1 or less
            {rehearsal && <span className="board-rehearsing"> during this rehearsal</span>}
          </p>
        )}
        <label className="visually-hidden" htmlFor="board-filter">
          Find a system
        </label>
        <input
          id="board-filter"
          className="board-filter"
          type="search"
          placeholder="Find a system"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          autoComplete="off"
        />
      </header>

      {error && (
        <p className="panel-error" role="alert">
          {error}
        </p>
      )}
      {!rows && !error && (
        <ul className="board-list" aria-busy="true">
          {Array.from({ length: 8 }, (_, i) => (
            <li key={i} className="skeleton skeleton-row" />
          ))}
        </ul>
      )}
      {rows && shown.length === 0 && (
        <p className="board-empty">No system matches “{filter}”. Try a word from its path, like kubelet or proxy.</p>
      )}
      {rows && shown.length > 0 && (
        <ul className="board-list">
          {shown.map((r) => {
            const { lead, tail } = splitPath(r.name)
            const now = after.get(r.name)
            const n = now ?? r.understudy
            return (
              <li key={r.name}>
                <button
                  type="button"
                  className={`board-row${r.name === selected ? ' is-selected' : ''}`}
                  aria-current={r.name === selected ? 'true' : undefined}
                  onClick={() => onSelect(r.name)}
                >
                  <span className={`badge${n <= 1 ? ' is-alarm' : ''}`} aria-label={`understudy number ${n}`}>
                    {now !== undefined && now !== r.understudy ? (
                      <>
                        <s aria-hidden="true">{r.understudy}</s>
                        {now}
                      </>
                    ) : (
                      r.understudy
                    )}
                  </span>
                  <span className="board-name">
                    <span className="board-lead">{lead}</span>
                    <span className="board-tail">{tail}</span>
                  </span>
                  <span className="board-meta">
                    {r.knowers} know · {r.approvers} approve
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
