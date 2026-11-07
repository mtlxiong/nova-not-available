import { create } from 'zustand'
import { IDENTITY_ALIGNMENT } from '../lib/alignment'
import type {
  Alignment,
  CalibrationOverlay,
  CalibrationResult,
  Point2,
  ReplayData,
  SceneInfo,
  SessionMetrics,
  SessionSummary,
  SpatialModel,
} from '../lib/types'

export type ViewPreset = 'iso' | 'top' | 'free' | 'video'

interface SceneStore {
  model: SpatialModel | null
  scenes: SceneInfo[]
  sceneId: string
  alignment: Alignment
  showZones: boolean
  /** Labels on detected objects in 3D. */
  showLabels: boolean
  /** Detected-object boxes over the video. */
  showObjects: boolean
  showTrails: boolean
  showHeatmap: boolean
  showCollider: boolean
  showSplat: boolean
  /** Set while World Labs is still generating the 3D environment for this workspace. */
  worldBuild: string | null
  view: ViewPreset
  viewNonce: number
  set: (patch: Partial<Omit<SceneStore, 'set' | 'setView'>>) => void
  setView: (view: ViewPreset) => void
}

export const useScene = create<SceneStore>((set) => ({
  model: null,
  scenes: [],
  sceneId: 'procedural',
  alignment: IDENTITY_ALIGNMENT,
  showZones: true,
  showLabels: true,
  showObjects: true,
  showTrails: true,
  showHeatmap: false,
  showCollider: false,
  showSplat: true,
  worldBuild: null,
  view: 'iso',
  viewNonce: 0,
  set: (patch) => set(patch),
  setView: (view) => set((s) => ({ view, viewNonce: s.viewNonce + 1 })),
}))

export interface PendingCalibrationPoint {
  image: Point2
  world: Point2 | null
  referenceId: string | null
}

interface CalibrationStore {
  editing: boolean
  points: PendingCalibrationPoint[]
  active: CalibrationResult | null
  overlay: CalibrationOverlay | null
  preview: { calibration: CalibrationResult; overlay: CalibrationOverlay } | null
  /** World point under the cursor on the video, for the 3D calibration preview. */
  hoverWorld: Point2 | null
  error: string | null
  set: (patch: Partial<Omit<CalibrationStore, 'set'>>) => void
}

export const useCalibration = create<CalibrationStore>((set) => ({
  editing: false,
  points: [],
  active: null,
  overlay: null,
  preview: null,
  hoverWorld: null,
  error: null,
  set: (patch) => set(patch),
}))

interface WorkspaceStore {
  /** Unsaved edits to the displayed workspace (labels, zones). */
  draft: SpatialModel | null
  /** Zone being drawn on the video, in world meters. null = not drawing. */
  zoneDraft: Point2[] | null
  dirty: boolean
  set: (patch: Partial<Omit<WorkspaceStore, 'set'>>) => void
}

export const useWorkspace = create<WorkspaceStore>((set) => ({
  draft: null,
  zoneDraft: null,
  dirty: false,
  set: (patch) => set(patch),
}))

interface ReplayStore {
  sessions: SessionSummary[]
  data: ReplayData | null
  metrics: SessionMetrics | null
  metricsSessionId: string | null
  playing: boolean
  speed: number
  set: (patch: Partial<Omit<ReplayStore, 'set'>>) => void
}

export const useReplay = create<ReplayStore>((set) => ({
  sessions: [],
  data: null,
  metrics: null,
  metricsSessionId: null,
  playing: false,
  speed: 1,
  set: (patch) => set(patch),
}))
