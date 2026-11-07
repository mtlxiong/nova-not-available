import { describe, expect, it } from 'vitest'
import { invert3 } from './alignment'
import { videoCameraPose } from './cameraPose'

/** Image->world homography for the auto-calibration camera (camera foot at ox, oy). */
function autoHomography(width: number, height: number, hfovDeg: number, pitchDeg: number, camHeight: number, ox: number, oy: number) {
  const f = width / 2 / Math.tan(((hfovDeg * Math.PI) / 180) / 2)
  const cx = width / 2
  const cy = height / 2
  const s = Math.sin((pitchDeg * Math.PI) / 180)
  const c = Math.cos((pitchDeg * Math.PI) / 180)
  const hw2i = [
    [f, cx * c, cx * s * camHeight],
    [0, -f * s + cy * c, f * c * camHeight + cy * s * camHeight],
    [0, c, s * camHeight],
  ]
  const inv = invert3(hw2i)!
  const hi2w = [
    [inv[0][0] + ox * inv[2][0], inv[0][1] + ox * inv[2][1], inv[0][2] + ox * inv[2][2]],
    [inv[1][0] + oy * inv[2][0], inv[1][1] + oy * inv[2][1], inv[1][2] + oy * inv[2][2]],
    [...inv[2]],
  ]
  const k = hi2w[2][2]
  return hi2w.map((row) => row.map((v) => v / k))
}

describe('video camera pose', () => {
  it('puts the camera at its foot, at the fitted height, looking along the optical axis', () => {
    const homography = autoHomography(768, 576, 65, 28, 4, 3, -2)
    const pose = videoCameraPose(
      {
        calibration_id: 'cal',
        camera_id: 'cam',
        image_width: 768,
        image_height: 576,
        homography,
        reprojection_rmse_m: 0,
        max_error_m: 0,
        point_errors_m: [],
        points: [],
        warnings: [],
        simulated: false,
        label: null,
        method: 'auto-person-height',
      },
      ['Floor scale is approximate: camera ~4.0 m high, pitched 28° down, assuming 1.70 m people and a 65° FOV'],
    )
    expect(pose).not.toBeNull()
    expect(pose!.position[0]).toBeCloseTo(3, 3)
    expect(pose!.position[1]).toBeCloseTo(-2, 3)
    expect(pose!.position[2]).toBeCloseTo(4, 5)
    expect(pose!.lookAt[1]).toBeGreaterThan(pose!.position[1])
    expect(pose!.hfovDeg).toBe(65)
  })
})
