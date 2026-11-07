import { describe, expect, it } from 'vitest'
import vectors from '../../../shared/alignment_vectors.json'
import { invert3, worldToImage, applyHomography, renderToWorld, worldToRender } from './alignment'
import { positionAt, snapshotAt } from './replay'
import type { Alignment, ReplayData, ReplaySample } from './types'
import { parseFrameMessage } from './ws'

describe('alignment', () => {
  it('matches the backend on shared vectors', () => {
    expect(vectors.cases.length).toBeGreaterThan(0)
    for (const c of vectors.cases) {
      const a = c.alignment as Alignment
      const [x, y, z] = c.world
      const got = worldToRender(a, x, y, z)
      got.forEach((v, i) => expect(v).toBeCloseTo(c.render[i], 7))
      renderToWorld(a, ...got).forEach((v, i) => expect(v).toBeCloseTo(c.world[i], 7))
    }
  })

  it('applies an image->world homography and refuses points beyond the horizon', () => {
    const h = [
      [1, 0, 0],
      [0, 1, 0],
      [0, -0.01, 1],
    ]
    expect(applyHomography(h, 10, 20)).toEqual([10 / 0.8, 20 / 0.8])
    expect(applyHomography(h, 10, 150)).toBeNull()
  })

  it('round-trips image -> world -> image through the inverse homography', () => {
    const h = [
      [0.02, 0.001, -3],
      [0.0005, -0.03, 12],
      [0.00001, 0.002, 1],
    ]
    const inv = invert3(h)!
    const w = applyHomography(h, 320, 400)!
    const [u, v] = worldToImage(inv, w[0], w[1])!
    expect(u).toBeCloseTo(320, 6)
    expect(v).toBeCloseTo(400, 6)
    expect(invert3([[1, 2, 3], [2, 4, 6], [0, 0, 1]])).toBeNull()
  })
})

const samples: ReplaySample[] = [
  [0, 0, 0, 'active', null],
  [1000, 1, 0, 'active', 'prep'],
  [2000, null, null, 'lost', 'prep'],
  [4000, 3, 0, 'active', 'grill'],
  [5000, 4, 0, 'active', 'grill'],
]

describe('replay', () => {
  it('interpolates only between active positioned samples', () => {
    expect(positionAt(samples, 500)).toEqual([0.5, 0])
    expect(positionAt(samples, 1000)).toEqual([1, 0])
    expect(positionAt(samples, 1500)).toBeNull()
    expect(positionAt(samples, 3000)).toBeNull()
    expect(positionAt(samples, 4500)).toEqual([3.5, 0])
    expect(positionAt(samples, -1)).toBeNull()
  })

  const replay: ReplayData = {
    session: {
      session_id: 's',
      created_wall_ms: 0,
      ended_wall_ms: 1,
      state: 'stopped',
      source_kind: 'simulation',
      calibration_id: null,
      scene_id: null,
      simulated: true,
      duration_ms: 5000,
      processed_frames: 50,
      error: null,
    },
    calibration: null,
    entities: [
      { entity_id: 'trk-1', class_name: 'person', source: 'simulation', label: null, object_id: null, first_seen_ms: 0, last_seen_ms: 5000, samples },
      { entity_id: 'trk-2', class_name: 'person', source: 'simulation', label: null, object_id: null, first_seen_ms: 4500, last_seen_ms: 5000, samples: [[4500, 2, 2, 'active', null]] },
    ],
    events: [],
  }

  it('snapshots show lost entities at their last observed position, never extrapolated', () => {
    const [e] = snapshotAt(replay, 3000)
    expect(e.state).toBe('lost')
    expect(e.position).toEqual({ x: 1, y: 0 })
    expect(e.zone_id).toBe('prep')
  })

  it('snapshots include only entities alive at the playhead and build trails from samples', () => {
    expect(snapshotAt(replay, 1000).map((e) => e.entity_id)).toEqual(['trk-1'])
    const at = snapshotAt(replay, 4600)
    expect(at.map((e) => e.entity_id)).toEqual(['trk-1', 'trk-2'])
    expect(at[0].trail).toEqual([
      [0, 0],
      [1, 0],
      [3, 0],
      [3.6, 0],
    ])
  })

  it('is deterministic', () => {
    expect(snapshotAt(replay, 4321)).toEqual(snapshotAt(replay, 4321))
  })
})

describe('websocket frames', () => {
  it('parses the binary frame header', () => {
    const buf = new ArrayBuffer(8 + 3)
    const view = new DataView(buf)
    view.setUint32(0, 42, true)
    view.setUint32(4, 12500, true)
    new Uint8Array(buf, 8).set([0xff, 0xd8, 0xff])
    const msg = parseFrameMessage(buf)
    expect(msg.frameIndex).toBe(42)
    expect(msg.timestampMs).toBe(12500)
    expect(msg.jpeg.size).toBe(3)
  })
})
