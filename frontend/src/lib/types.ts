// Mirrors backend/echo/schemas.py. Keep in sync.

export type TrackingState = 'active' | 'lost' | 'removed'
export type EntitySource = 'yolo' | 'aruco' | 'simulation'
export type Point2 = [number, number]

export interface WorldPosition {
  x: number
  y: number
  unit: 'm'
}

export interface BBox {
  x1: number
  y1: number
  x2: number
  y2: number
}

export interface EntityUpdate {
  entity_id: string
  class_name: string
  source: EntitySource
  source_track_id: string
  label: string | null
  object_id: string | null
  confidence: number | null
  bbox: BBox | null
  image_point: { u: number; v: number } | null
  position: WorldPosition | null
  zone_id: string | null
  tracking_state: TrackingState
  first_seen_ms: number
  last_seen_ms: number
}

export interface PerfStats {
  capture_fps: number
  process_fps: number
  inference_ms: number
  tracking_ms: number
  pipeline_ms: number
  dropped_frames: number
  queue_depth: number
}

export interface TrackingUpdate {
  type: 'tracking.update'
  session_id: string
  frame_index: number
  timestamp_ms: number
  capture_wall_ms: number
  emitted_wall_ms: number
  frame_width: number
  frame_height: number
  calibrated: boolean
  simulated: boolean
  entities: EntityUpdate[]
  perf: PerfStats | null
}

export type EventKind =
  | 'session.started'
  | 'session.stopped'
  | 'track.created'
  | 'track.lost'
  | 'track.reacquired'
  | 'track.removed'
  | 'zone.enter'
  | 'zone.exit'

export interface SpatialEvent {
  session_id: string
  timestamp_ms: number
  kind: EventKind
  entity_id: string | null
  zone_id: string | null
  data: Record<string, unknown>
}

export interface EventMessage {
  type: 'event'
  event: SpatialEvent
}

export type SessionState = 'idle' | 'starting' | 'running' | 'stopping' | 'stopped' | 'error'

export interface SessionStatusMessage {
  type: 'session.status'
  session_id: string | null
  state: SessionState
  source_kind: string | null
  simulated: boolean
  calibrated: boolean
  message: string | null
  spatial_model_id?: string | null
}

export type ServerMessage = TrackingUpdate | EventMessage | SessionStatusMessage

export type Provenance = 'template' | 'detected' | 'proposed' | 'manual'

export const ZONE_KINDS = ['workstation', 'queue', 'pickup', 'storage', 'aisle', 'activity', 'other'] as const
export type ZoneKind = (typeof ZONE_KINDS)[number]

export interface Zone {
  zone_id: string
  name: string
  kind: ZoneKind
  polygon: Point2[]
  color: string
  provenance: Provenance
}

export interface Fixture {
  fixture_id: string
  /** Free-form category; detected fixtures use the detector's label. */
  kind: string
  label: string | null
  center: Point2
  /** width (x), depth (y), height (z) in meters */
  size: [number, number, number]
  yaw_deg: number
  zone_id: string | null
  provenance: Provenance
  confidence: number | null
  estimated_dims: ('width' | 'depth' | 'height')[]
  image_bbox: [number, number, number, number] | null
}

export interface FloorTexture {
  url: string
  /** World position of the texture's south-west corner. */
  origin: Point2
  size_m: Point2
}

export interface ReferencePoint {
  point_id: string
  label: string
  x: number
  y: number
}

export interface SpatialModel {
  model_id: string
  name: string
  width_m: number
  depth_m: number
  floor_polygon: Point2[]
  navigable_regions: Point2[][]
  zones: Zone[]
  fixtures: Fixture[]
  reference_points: ReferencePoint[]
  source: 'template' | 'analyzed' | 'manual' | 'blank'
  notes: string[]
  camera_id: string | null
  calibration_id: string | null
  floor_texture: FloorTexture | null
  background_url: string | null
}

export interface SpatialModelListItem {
  model_id: string
  name: string
  source: SpatialModel['source']
  active: boolean
  updated_wall_ms: number
  zones: number
  fixtures: number
}

export interface WorkspaceAnalysisRequest {
  video_id?: string | null
  calibration_id?: string | null
  model_id?: string | null
  name?: string | null
  vocabulary?: string[] | null
  person_height_m?: number
  hfov_deg?: number
  object_confidence?: number
  max_frames?: number
}

export interface AnalysisResult {
  model: SpatialModel
  calibration: CalibrationResult
  stats: Record<string, number | string | null>
  objects: { label: string; confidence: number; bbox: [number, number, number, number] }[]
  notes: string[]
}

export interface AnalysisJob {
  job_id: string
  state: 'running' | 'done' | 'error'
  stage: string
  progress: number
  error: string | null
  result: AnalysisResult | null
}

export interface Alignment {
  translation: [number, number, number]
  yaw_deg: number
  scale: number
}

export interface SceneConfig {
  scene_id: string
  alignment: Alignment
  show_collider: boolean
  splat_visible: boolean
}

export interface SceneInfo {
  scene_id: string
  kind: 'procedural' | 'image-blaster'
  display_name: string
  splat_url: string | null
  collider_url: string | null
  pano_url: string | null
  semantics: { metric_scale_factor: number; ground_plane_offset: number; flip_y: boolean } | null
  synthetic: boolean
  notes: string | null
  config: SceneConfig
}

export interface CalibrationPoint {
  image: Point2
  world: Point2
}

export interface CalibrationResult {
  calibration_id: string | null
  camera_id: string
  image_width: number
  image_height: number
  homography: number[][]
  reprojection_rmse_m: number
  max_error_m: number
  point_errors_m: number[]
  points: CalibrationPoint[]
  warnings: string[]
  simulated: boolean
  label: string | null
  /** 'auto-person-height' is an approximate single-view estimate, not a surveyed calibration. */
  method: 'points' | 'simulated' | 'auto-person-height'
}

export interface CalibrationOverlay {
  zones: { zone_id: string; color: string; image_polygon: (Point2 | null)[] }[]
  floor: (Point2 | null)[]
  reference_points: { point_id: string; image: Point2 | null }[]
}

export type SourceKind = 'webcam' | 'file' | 'simulation'

export interface SessionConfig {
  source: SourceKind
  camera_index?: number
  file_id?: string | null
  width?: number | null
  height?: number | null
  inference_fps?: number
  realtime?: boolean
  detector?: 'yolo' | 'none'
  tracker?: 'bytetrack' | 'iou'
  classes?: string[]
  confidence?: number
  enable_aruco?: boolean
  calibration_id?: string | null
  spatial_model_id?: string | null
  scene_id?: string
  simulation_entities?: number
  simulation_duration_s?: number | null
}

export interface SessionSummary {
  session_id: string
  created_wall_ms: number
  ended_wall_ms: number | null
  state: string
  source_kind: string
  calibration_id: string | null
  scene_id: string | null
  simulated: boolean
  duration_ms: number
  processed_frames: number
  error: string | null
}

/** [t_ms, x, y, state, zone_id] */
export type ReplaySample = [number, number | null, number | null, TrackingState, string | null]

export interface ReplayEntity {
  entity_id: string
  class_name: string
  source: EntitySource
  label: string | null
  object_id: string | null
  first_seen_ms: number
  last_seen_ms: number
  samples: ReplaySample[]
}

export interface ReplayEvent {
  t_ms: number
  kind: EventKind
  entity_id: string | null
  zone_id: string | null
  data: Record<string, unknown>
}

export interface ReplayData {
  session: SessionSummary
  calibration: { calibration_id: string; reprojection_rmse_m: number; simulated: boolean } | null
  entities: ReplayEntity[]
  events: ReplayEvent[]
}

export interface ZoneMetrics {
  zone_id: string
  name: string
  kind: string
  visits: number
  unique_entities: number
  total_dwell_s: number
  mean_dwell_s: number | null
  max_occupancy: number
  mean_occupancy: number
  occupancy_series: number[]
}

export interface EntityMetrics {
  entity_id: string
  class_name: string
  label: string | null
  first_seen_ms: number
  last_seen_ms: number
  distance_m: number | null
  tracked_time_s: number
  mean_speed_mps: number | null
  excluded_segments: number
  positioned_samples: number
  zone_dwell_s: Record<string, number>
}

export interface SessionMetrics {
  session_id: string
  simulated: boolean
  duration_ms: number
  data_quality: {
    calibrated: boolean
    calibration_id: string | null
    calibration_rmse_m: number | null
    observations: number
    positioned_observations: number
    notes: string[]
  }
  entities: EntityMetrics[]
  zones: ZoneMetrics[]
  heatmap: { origin: Point2; cell_m: number; cols: number; rows: number; seconds: number[][] }
  orders: { available: boolean; reason?: string }
  queues: { available: boolean; reason?: string }
}
