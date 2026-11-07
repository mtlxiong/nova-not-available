import { applyHomography } from './alignment'
import type { CalibrationResult } from './types'

export interface VideoPose {
  /** World meters: camera foot x/y, then height above the floor. */
  position: [number, number, number]
  /** World meters on the floor, where the optical axis lands. */
  lookAt: [number, number]
  hfovDeg: number
}

/** Vertical fov (degrees) that preserves a horizontal field of view at this canvas aspect. */
export function verticalFovDeg(hfovDeg: number, aspect: number): number {
  const h = (hfovDeg * Math.PI) / 180
  return (2 * Math.atan(Math.tan(h / 2) / Math.max(aspect, 1e-3)) * 180) / Math.PI
}

/**
 * Camera that produced this ground-plane calibration.
 * Auto-calibration notes carry height, pitch, and FOV. The foot is the image of the nadir,
 * and the look target is the image of the principal point, both mapped through the homography.
 */
export function videoCameraPose(cal: CalibrationResult, notes: string[]): VideoPose | null {
  const text = notes.join('\n')
  const placed = text.match(/camera ~([\d.]+) m high, pitched ([\d.]+)° down/)
  if (!placed) return null
  const height = Number(placed[1])
  const pitchDeg = Number(placed[2])
  const fov = text.match(/([\d.]+)° FOV/)
  const hfovDeg = fov ? Number(fov[1]) : 65
  if (!(height > 0) || !(pitchDeg > 1) || !(hfovDeg > 1)) return null
  const focal = cal.image_width / 2 / Math.tan(((hfovDeg * Math.PI) / 180) / 2)
  const u = cal.image_width / 2
  const nadirV = cal.image_height / 2 + focal / Math.tan((pitchDeg * Math.PI) / 180)
  const foot = applyHomography(cal.homography, u, nadirV)
  const look = applyHomography(cal.homography, u, cal.image_height / 2)
  if (!foot || !look) return null
  return { position: [foot[0], foot[1], height], lookAt: look, hfovDeg }
}
