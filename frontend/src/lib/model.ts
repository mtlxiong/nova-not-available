import type { Fixture, SpatialModel } from './types'

export interface Bounds {
  x0: number
  y0: number
  x1: number
  y1: number
}

/** Floor extent in world meters. Analyzed workspaces need not start at the origin. */
export function modelBounds(model: SpatialModel): Bounds {
  const xs = model.floor_polygon.map((p) => p[0])
  const ys = model.floor_polygon.map((p) => p[1])
  if (!xs.length) return { x0: 0, y0: 0, x1: model.width_m, y1: model.depth_m }
  return { x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) }
}

export function fixtureName(f: Fixture): string {
  return f.label ?? f.kind
}

export const PROVENANCE_COLORS: Record<string, string> = {
  template: '#a1a1aa',
  detected: '#f59e0b',
  proposed: '#f59e0b',
  manual: '#60a5fa',
}

let counter = 0
export function localId(prefix: string): string {
  counter += 1
  return `${prefix}-${Date.now().toString(36)}-${counter}`
}
