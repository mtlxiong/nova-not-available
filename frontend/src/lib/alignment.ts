import type { Alignment } from './types'

/**
 * World (meters, z-up, ground plane x/y) -> Three.js render (y-up).
 * Mirrors backend/echo/spatial/alignment.py; both are tested against shared/alignment_vectors.json.
 *
 * Equivalent scene graph: a group with position=translation, rotation.y=yaw, scale=scale,
 * whose children are placed at worldToLocal(x, y, z).
 */
export function worldToLocal(x: number, y: number, z = 0): [number, number, number] {
  return [x, z, -y]
}

export function worldToRender(a: Alignment, x: number, y: number, z = 0): [number, number, number] {
  const [bx, by, bz] = worldToLocal(x, y, z)
  const t = (a.yaw_deg * Math.PI) / 180
  const c = Math.cos(t)
  const s = Math.sin(t)
  const rx = c * bx + s * bz
  const rz = -s * bx + c * bz
  return [a.scale * rx + a.translation[0], a.scale * by + a.translation[1], a.scale * rz + a.translation[2]]
}

export function renderToWorld(a: Alignment, rx: number, ry: number, rz: number): [number, number, number] {
  const k = a.scale
  const px = (rx - a.translation[0]) / k
  const py = (ry - a.translation[1]) / k
  const pz = (rz - a.translation[2]) / k
  const t = (a.yaw_deg * Math.PI) / 180
  const c = Math.cos(t)
  const s = Math.sin(t)
  const bx = c * px - s * pz
  const bz = s * px + c * pz
  return [bx, -bz, py]
}

export const IDENTITY_ALIGNMENT: Alignment = { translation: [0, 0, 0], yaw_deg: 0, scale: 1 }

/** Applies a 3x3 image->world homography (row-major) to a pixel. Null beyond the plane horizon. */
export function applyHomography(h: number[][], u: number, v: number, wSign = 1): [number, number] | null {
  const x = h[0][0] * u + h[0][1] * v + h[0][2]
  const y = h[1][0] * u + h[1][1] * v + h[1][2]
  const w = h[2][0] * u + h[2][1] * v + h[2][2]
  if (!Number.isFinite(w) || w * wSign <= 1e-9) return null
  return [x / w, y / w]
}

export function invert3(m: number[][]): number[][] | null {
  const [[a, b, c], [d, e, f], [g, h, i]] = m
  const A = e * i - f * h
  const B = -(d * i - f * g)
  const C = d * h - e * g
  const det = a * A + b * B + c * C
  if (!Number.isFinite(det) || Math.abs(det) < 1e-12) return null
  return [
    [A / det, -(b * i - c * h) / det, (b * f - c * e) / det],
    [B / det, (a * i - c * g) / det, -(a * f - c * d) / det],
    [C / det, -(a * h - b * g) / det, (a * e - b * d) / det],
  ]
}

/** World floor point -> image pixel via the inverse of an image->world homography. */
export function worldToImage(hInv: number[][], x: number, y: number): [number, number] | null {
  const u = hInv[0][0] * x + hInv[0][1] * y + hInv[0][2]
  const v = hInv[1][0] * x + hInv[1][1] * y + hInv[1][2]
  const w = hInv[2][0] * x + hInv[2][1] * y + hInv[2][2]
  if (!Number.isFinite(w) || Math.abs(w) < 1e-12) return null
  return [u / w, v / w]
}
