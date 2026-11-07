import type { ReplayData, ReplayEntity, ReplaySample, TrackingState } from './types'

/** Index of the last sample with t <= tMs, or -1. Samples are sorted by time. */
export function lastSampleIndex(samples: ReplaySample[], tMs: number): number {
  let lo = 0
  let hi = samples.length - 1
  let ans = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (samples[mid][0] <= tMs) {
      ans = mid
      lo = mid + 1
    } else {
      hi = mid - 1
    }
  }
  return ans
}

const positioned = (s: ReplaySample | undefined): s is ReplaySample => !!s && s[3] === 'active' && s[1] !== null && s[2] !== null

/**
 * Interpolates only between consecutive active, positioned samples. Mirrors backend
 * echo/memory/replay.py:position_at — returns null across gaps instead of inventing a path.
 */
export function positionAt(samples: ReplaySample[], tMs: number): [number, number] | null {
  const i = lastSampleIndex(samples, tMs)
  if (i < 0) return null
  const a = samples[i]
  if (!positioned(a)) return null
  if (a[0] === tMs) return [a[1] as number, a[2] as number]
  const b = samples[i + 1]
  if (!positioned(b)) return null
  const f = (tMs - a[0]) / (b[0] - a[0])
  return [(a[1] as number) + ((b[1] as number) - (a[1] as number)) * f, (a[2] as number) + ((b[2] as number) - (a[2] as number)) * f]
}

export interface SnapshotEntity {
  entity_id: string
  class_name: string
  source: ReplayEntity['source']
  label: string | null
  object_id: string | null
  state: TrackingState
  position: { x: number; y: number } | null
  zone_id: string | null
  first_seen_ms: number
  last_seen_ms: number
  trail: [number, number][]
}

/** Entity states at a playhead time, reconstructed purely from stored observations. */
export function snapshotAt(replay: ReplayData, tMs: number, trailMs = 15000): SnapshotEntity[] {
  const out: SnapshotEntity[] = []
  for (const e of replay.entities) {
    if (tMs < e.first_seen_ms || tMs > e.last_seen_ms) continue
    const i = lastSampleIndex(e.samples, tMs)
    if (i < 0) continue
    const last = e.samples[i]
    const interp = positionAt(e.samples, tMs)
    let state: TrackingState = last[3]
    let position: { x: number; y: number } | null = interp ? { x: interp[0], y: interp[1] } : null
    if (!interp) {
      // In a lost gap: show the last observed position, flagged lost. Never extrapolate.
      state = 'lost'
      for (let j = i; j >= 0; j--) {
        const s = e.samples[j]
        if (positioned(s)) {
          position = { x: s[1] as number, y: s[2] as number }
          break
        }
      }
    }
    const trail: [number, number][] = []
    for (let j = lastSampleIndex(e.samples, tMs - trailMs) + 1; j <= i; j++) {
      const s = e.samples[j]
      if (positioned(s)) trail.push([s[1] as number, s[2] as number])
    }
    if (interp) trail.push(interp)
    out.push({
      entity_id: e.entity_id,
      class_name: e.class_name,
      source: e.source,
      label: e.label,
      object_id: e.object_id,
      state,
      position,
      zone_id: last[4],
      first_seen_ms: e.first_seen_ms,
      last_seen_ms: e.last_seen_ms,
      trail,
    })
  }
  return out
}
