// Geometry for the /proof chart: two panels on the same log scale, hand-drawn SVG
// like the stage. Kept apart from the component so it can be tested.

/** The smallest power-of-two range that holds every value, and 1 (the "no change"
 * line) unless told otherwise. */
export function logDomain(values: number[], withOne = true): [number, number] {
  const extra = withOne ? [1] : []
  const lo = Math.min(...extra, ...values)
  const hi = Math.max(...extra, ...values)
  return [2 ** Math.floor(Math.log2(lo)), 2 ** Math.ceil(Math.log2(hi))]
}

/** Every power of two in the domain. */
export function logTicks([lo, hi]: [number, number]): number[] {
  const out = []
  for (let e = Math.round(Math.log2(lo)); e <= Math.round(Math.log2(hi)); e++) out.push(2 ** e)
  return out
}

/** Maps a value in a log domain onto [a, b] (b may be less than a, for a y axis). */
export function logScale([lo, hi]: [number, number], [a, b]: [number, number]) {
  const l0 = Math.log2(lo)
  const span = Math.log2(hi) - l0
  return (v: number) => a + ((Math.log2(v) - l0) / span) * (b - a)
}

/** Axis label for a power-of-two ratio: 4 -> "4×", 0.25 -> "1/4×". (The single-glyph
 * fractions are too small to read at axis size in the mono face.) */
export function ratioTick(v: number): string {
  return v >= 1 ? `${v}×` : `1/${Math.round(1 / v)}×`
}

/** Understudy numbers on the first panel: 0 to 6 each get a column, 7 and up share one. */
export const BINS = ['0', '1', '2', '3', '4', '5', '6', '7+']
export function bin(n: number): number {
  return Math.min(Math.max(n, 0), BINS.length - 1)
}

/** Sideways offsets that keep dots in one column from covering each other. Dots are
 * placed in order of y; each takes the first offset (0, +step, −step, +2·step, ...) where
 * no dot already placed is within `gap` vertically. Same input, same layout. */
export function dodge(ys: number[], gap: number, step = gap): number[] {
  const order = ys.map((y, i) => [y, i] as const).sort((a, b) => a[0] - b[0] || a[1] - b[1])
  const placed: { y: number; dx: number }[] = []
  const out = new Array<number>(ys.length).fill(0)
  for (const [y, i] of order) {
    for (let k = 0; ; k++) {
      const dx = k === 0 ? 0 : Math.ceil(k / 2) * step * (k % 2 ? 1 : -1)
      if (!placed.some((p) => p.dx === dx && Math.abs(p.y - y) < gap)) {
        placed.push({ y, dx })
        out[i] = dx
        break
      }
    }
  }
  return out
}
