import { create } from 'zustand'
import type { SnapshotEntity } from '../lib/replay'
import type {
  BBox,
  EntitySource,
  PerfStats,
  SessionStatusMessage,
  SpatialEvent,
  TrackingState,
  TrackingUpdate,
} from '../lib/types'

export const TRAIL_MAX_POINTS = 300
const TRAIL_MIN_STEP_M = 0.02
const EVENTS_MAX = 300

export interface EntityView {
  entity_id: string
  class_name: string
  source: EntitySource
  label: string | null
  object_id: string | null
  state: TrackingState
  /** World meters. Null when uncalibrated: the entity is listed but not placed in 3D. */
  position: { x: number; y: number } | null
  zone_id: string | null
  bbox: BBox | null
  confidence: number | null
  first_seen_ms: number
  last_seen_ms: number
  /** Client wall clock (ms) of the last update touching this entity. */
  received_at: number
  trail: [number, number][]
}

export interface FrameInfo {
  frame_index: number
  timestamp_ms: number
  frame_width: number
  frame_height: number
  calibrated: boolean
  simulated: boolean
  perf: PerfStats | null
  /** Receipt time minus server capture time (same-host clocks). */
  e2e_ms: number
  entities: { entity_id: string; bbox: BBox | null; state: TrackingState; class_name: string; source: EntitySource; label: string | null }[]
}

export type Mode = 'live' | 'replay'

export interface TwinState {
  mode: Mode
  status: SessionStatusMessage
  sessionId: string | null
  entities: Record<string, EntityView>
  /** Sorted entity IDs; reference only changes when the set of entities changes. */
  entityIds: string[]
  events: SpatialEvent[]
  lastFrame: FrameInfo | null
  playheadMs: number
  selectedId: string | null
  setMode: (mode: Mode) => void
  setStatus: (status: SessionStatusMessage) => void
  applyTrackingUpdate: (msg: TrackingUpdate, receivedAt?: number) => void
  applySnapshot: (snapshot: SnapshotEntity[], tMs: number, sessionId: string) => void
  pushEvent: (event: SpatialEvent) => void
  setEvents: (events: SpatialEvent[]) => void
  select: (id: string | null) => void
  clearEntities: () => void
}

const sameIds = (a: string[], b: string[]) => a.length === b.length && a.every((v, i) => v === b[i])

function extendTrail(trail: [number, number][], p: { x: number; y: number } | null, state: TrackingState) {
  if (!p || state !== 'active') return trail
  const last = trail[trail.length - 1]
  if (last && Math.hypot(last[0] - p.x, last[1] - p.y) < TRAIL_MIN_STEP_M) return trail
  const next = trail.length >= TRAIL_MAX_POINTS ? trail.slice(trail.length - TRAIL_MAX_POINTS + 1) : trail.slice()
  next.push([p.x, p.y])
  return next
}

export const useTwin = create<TwinState>((set, get) => ({
  mode: 'live',
  status: { type: 'session.status', session_id: null, state: 'idle', source_kind: null, simulated: false, calibrated: false, message: null },
  sessionId: null,
  entities: {},
  entityIds: [],
  events: [],
  lastFrame: null,
  playheadMs: 0,
  selectedId: null,

  setMode: (mode) => set({ mode, entities: {}, entityIds: [], events: mode === get().mode ? get().events : [], lastFrame: null }),

  setStatus: (status) => {
    const prev = get().status
    const newSession = status.session_id && status.session_id !== prev.session_id
    set({
      status,
      ...(newSession && get().mode === 'live' ? { sessionId: status.session_id, entities: {}, entityIds: [], events: [], selectedId: null } : {}),
    })
  },

  applyTrackingUpdate: (msg, receivedAt = Date.now()) => {
    const state = get()
    if (state.mode !== 'live') return
    const prev = state.sessionId === msg.session_id ? state.entities : {}
    const entities: Record<string, EntityView> = {}
    for (const e of msg.entities) {
      if (e.tracking_state === 'removed') continue
      const old = prev[e.entity_id]
      const position = e.position ? { x: e.position.x, y: e.position.y } : null
      entities[e.entity_id] = {
        entity_id: e.entity_id,
        class_name: e.class_name,
        source: e.source,
        label: e.label,
        object_id: e.object_id,
        state: e.tracking_state,
        position,
        zone_id: e.zone_id,
        bbox: e.bbox,
        confidence: e.confidence,
        first_seen_ms: e.first_seen_ms,
        last_seen_ms: e.last_seen_ms,
        received_at: receivedAt,
        trail: extendTrail(old?.trail ?? [], position, e.tracking_state),
      }
    }
    const ids = Object.keys(entities).sort()
    set({
      sessionId: msg.session_id,
      entities,
      entityIds: sameIds(ids, state.entityIds) ? state.entityIds : ids,
      playheadMs: msg.timestamp_ms,
      lastFrame: {
        frame_index: msg.frame_index,
        timestamp_ms: msg.timestamp_ms,
        frame_width: msg.frame_width,
        frame_height: msg.frame_height,
        calibrated: msg.calibrated,
        simulated: msg.simulated,
        perf: msg.perf,
        e2e_ms: receivedAt - msg.capture_wall_ms,
        entities: msg.entities.map((e) => ({
          entity_id: e.entity_id,
          bbox: e.bbox,
          state: e.tracking_state,
          class_name: e.class_name,
          source: e.source,
          label: e.label,
        })),
      },
    })
  },

  applySnapshot: (snapshot, tMs, sessionId) => {
    const state = get()
    const now = Date.now()
    const entities: Record<string, EntityView> = {}
    for (const s of snapshot) {
      entities[s.entity_id] = {
        entity_id: s.entity_id,
        class_name: s.class_name,
        source: s.source,
        label: s.label,
        object_id: s.object_id,
        state: s.state,
        position: s.position,
        zone_id: s.zone_id,
        bbox: null,
        confidence: null,
        first_seen_ms: s.first_seen_ms,
        last_seen_ms: s.last_seen_ms,
        received_at: now,
        trail: s.trail,
      }
    }
    const ids = Object.keys(entities).sort()
    set({ entities, entityIds: sameIds(ids, state.entityIds) ? state.entityIds : ids, playheadMs: tMs, sessionId })
  },

  pushEvent: (event) => {
    if (get().mode !== 'live') return
    const { events, entities, entityIds, selectedId } = get()
    const next = { events: events.length >= EVENTS_MAX ? [...events.slice(1), event] : [...events, event] }
    const removedId = event.kind === 'track.removed' ? event.entity_id : null
    if (removedId && entities[removedId]) {
      const { [removedId]: _, ...rest } = entities
      set({
        ...next,
        entities: rest,
        entityIds: entityIds.filter((id) => id !== removedId),
        selectedId: selectedId === removedId ? null : selectedId,
      })
    } else {
      set(next)
    }
  },

  setEvents: (events) => set({ events }),
  select: (selectedId) => set({ selectedId }),
  clearEntities: () => set({ entities: {}, entityIds: [], lastFrame: null }),
}))
