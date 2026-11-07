import { beforeEach, describe, expect, it } from 'vitest'
import type { EntityUpdate, TrackingUpdate } from '../lib/types'
import { TRAIL_MAX_POINTS, useTwin } from './twin'

const entity = (id: string, x: number | null, state: EntityUpdate['tracking_state'] = 'active'): EntityUpdate => ({
  entity_id: id,
  class_name: 'person',
  source: 'yolo',
  source_track_id: id,
  label: null,
  object_id: null,
  confidence: 0.9,
  bbox: { x1: 0, y1: 0, x2: 10, y2: 20 },
  image_point: { u: 5, v: 20 },
  position: x === null ? null : { x, y: 1, unit: 'm' },
  zone_id: null,
  tracking_state: state,
  first_seen_ms: 0,
  last_seen_ms: 0,
})

const update = (t: number, entities: EntityUpdate[], session = 'ses_1'): TrackingUpdate => ({
  type: 'tracking.update',
  session_id: session,
  frame_index: t / 100,
  timestamp_ms: t,
  capture_wall_ms: 1000,
  emitted_wall_ms: 1010,
  frame_width: 1280,
  frame_height: 720,
  calibrated: true,
  simulated: false,
  entities,
  perf: null,
})

beforeEach(() => {
  useTwin.setState({ mode: 'live', sessionId: null, entities: {}, entityIds: [], events: [], lastFrame: null, selectedId: null })
})

describe('twin store', () => {
  it('creates, updates and removes entities from tracking updates', () => {
    const s = useTwin.getState()
    s.applyTrackingUpdate(update(0, [entity('trk-1', 1), entity('trk-2', 2)]), 1050)
    expect(useTwin.getState().entityIds).toEqual(['trk-1', 'trk-2'])
    expect(useTwin.getState().lastFrame?.e2e_ms).toBe(50)

    const idsRef = useTwin.getState().entityIds
    s.applyTrackingUpdate(update(100, [entity('trk-1', 1.5), entity('trk-2', 2)]))
    expect(useTwin.getState().entityIds).toBe(idsRef)
    expect(useTwin.getState().entities['trk-1'].position).toEqual({ x: 1.5, y: 1 })
    expect(useTwin.getState().entities['trk-1'].trail).toHaveLength(2)

    s.applyTrackingUpdate(update(200, [entity('trk-1', 1.5, 'lost'), entity('trk-2', 2, 'removed')]))
    expect(useTwin.getState().entityIds).toEqual(['trk-1'])
    expect(useTwin.getState().entities['trk-1'].state).toBe('lost')
  })

  it('drops entities on track.removed events (e.g. session end)', () => {
    useTwin.getState().applyTrackingUpdate(update(0, [entity('trk-1', 1), entity('trk-2', 2)]))
    useTwin.getState().select('trk-1')
    useTwin.getState().pushEvent({
      session_id: 'ses_1',
      timestamp_ms: 500,
      kind: 'track.removed',
      entity_id: 'trk-1',
      zone_id: null,
      data: {},
    })
    expect(useTwin.getState().entityIds).toEqual(['trk-2'])
    expect(useTwin.getState().selectedId).toBeNull()
    expect(useTwin.getState().events).toHaveLength(1)
  })

  it('keeps null positions null when uncalibrated', () => {
    useTwin.getState().applyTrackingUpdate(update(0, [entity('trk-1', null)]))
    const e = useTwin.getState().entities['trk-1']
    expect(e.position).toBeNull()
    expect(e.trail).toEqual([])
  })

  it('bounds trail length', () => {
    for (let i = 0; i < TRAIL_MAX_POINTS + 50; i++) useTwin.getState().applyTrackingUpdate(update(i * 100, [entity('trk-1', i * 0.1)]))
    expect(useTwin.getState().entities['trk-1'].trail.length).toBe(TRAIL_MAX_POINTS)
  })

  it('ignores live updates while in replay mode', () => {
    useTwin.getState().setMode('replay')
    useTwin.getState().applyTrackingUpdate(update(0, [entity('trk-1', 1)]))
    expect(useTwin.getState().entityIds).toEqual([])
  })

  it('resets entities when a new session starts', () => {
    useTwin.getState().applyTrackingUpdate(update(0, [entity('trk-1', 1)]))
    useTwin.getState().setStatus({ type: 'session.status', session_id: 'ses_2', state: 'running', source_kind: 'webcam', simulated: false, calibrated: true, message: null })
    expect(useTwin.getState().entityIds).toEqual([])
    expect(useTwin.getState().sessionId).toBe('ses_2')
  })
})
