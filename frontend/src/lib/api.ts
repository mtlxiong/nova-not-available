import { withBackend } from './backend'
import type {
  AnalysisJob,
  CalibrationOverlay,
  CalibrationPoint,
  CalibrationResult,
  ReplayData,
  SceneConfig,
  SceneInfo,
  SessionConfig,
  SessionMetrics,
  SessionStatusMessage,
  WorkflowAdvice,
  SessionSummary,
  SpatialModel,
  SpatialModelListItem,
  WorkspaceAnalysisRequest,
} from './types'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(withBackend(path), init)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export interface CurrentSession extends SessionStatusMessage {
  calibration: CalibrationResult | null
  overlay: CalibrationOverlay | null
  config: SessionConfig | null
}

export interface VideoInfo {
  video_id: string
  name: string
  width: number
  height: number
  fps: number
  frame_count: number
  duration_ms: number | null
}

export interface CalibrationListItem {
  calibration_id: string
  camera_id: string
  image_width: number
  image_height: number
  reprojection_rmse_m: number
  points: number
  warnings: string[]
  simulated: boolean
  label: string | null
  method?: CalibrationResult['method']
}

export const api = {
  health: () => request<{ ok: boolean; yolo_weights: boolean; image_blaster: boolean }>('/api/health'),
  spatialModel: () => request<SpatialModel>('/api/spatial-model'),
  spatialModels: () => request<SpatialModelListItem[]>('/api/spatial-models'),
  spatialModelById: (id: string) => request<SpatialModel>(`/api/spatial-models/${encodeURIComponent(id)}`),
  putSpatialModel: (m: SpatialModel, activate = false) => request<SpatialModel>(`/api/spatial-model?activate=${activate}`, json('PUT', m)),
  activateSpatialModel: (id: string) =>
    request<{ model: SpatialModel; session: SessionStatusMessage | null }>(`/api/spatial-models/${encodeURIComponent(id)}/activate`, { method: 'POST' }),
  deleteSpatialModel: (id: string) => request<{ deleted: string }>(`/api/spatial-models/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  sessionModel: (sessionId: string) => request<SpatialModel>(`/api/sessions/${sessionId}/model`),
  analyzeWorkspace: (body: WorkspaceAnalysisRequest) => request<AnalysisJob>('/api/workspaces/analyze', json('POST', body)),
  analysisStatus: (jobId: string) => request<AnalysisJob>(`/api/workspaces/analyze/${jobId}`),
  generateWorldFromWorkspace: (modelId: string) =>
    request<{ slug: string; status: string; reason?: string | null }>(`/api/workspaces/${encodeURIComponent(modelId)}/generate-world`, { method: 'POST' }),
  scenes: () => request<SceneInfo[]>('/api/scenes'),
  putSceneConfig: (c: SceneConfig) => request<SceneConfig>(`/api/scenes/${encodeURIComponent(c.scene_id)}/config`, json('PUT', c)),
  generateScene: (slug: string, image: File) => {
    const form = new FormData()
    form.append('slug', slug)
    form.append('image', image)
    return request<{ slug: string; status: string; reason?: string | null }>('/api/scenes/generate', { method: 'POST', body: form })
  },
  generationStatus: (slug: string) =>
    request<{ slug: string; status: string; reason?: string | null; detail?: string | null }>(`/api/scenes/generate/${slug}`),
  videos: () => request<VideoInfo[]>('/api/videos'),
  uploadVideo: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<VideoInfo>('/api/videos', { method: 'POST', body: form })
  },
  probeCameras: () => request<{ index: number; available: boolean; width?: number; height?: number }[]>('/api/cameras/probe'),
  startSession: (c: SessionConfig) => request<SessionStatusMessage>('/api/sessions/start', json('POST', c)),
  stopSession: () => request<SessionStatusMessage>('/api/sessions/stop', { method: 'POST' }),
  currentSession: () => request<CurrentSession>('/api/sessions/current'),
  applyCalibration: (calibration_id: string | null) => request<SessionStatusMessage>('/api/sessions/current/calibration', json('POST', { calibration_id })),
  sessions: () => request<SessionSummary[]>('/api/sessions'),
  replay: (id: string) => request<ReplayData>(`/api/sessions/${id}/replay`),
  metrics: (id: string) => request<SessionMetrics>(`/api/sessions/${id}/metrics`),
  workflowAdvice: (id: string, question?: string) =>
    request<WorkflowAdvice>(`/api/sessions/${id}/advice`, json('POST', question ? { question } : {})),
  calibrations: () => request<CalibrationListItem[]>('/api/calibrations'),
  calibration: (id: string) => request<{ calibration: CalibrationResult; overlay: CalibrationOverlay }>(`/api/calibrations/${encodeURIComponent(id)}`),
  previewCalibration: (body: CalibrationBody) =>
    request<{ calibration: CalibrationResult; overlay: CalibrationOverlay }>('/api/calibrations/preview', json('POST', body)),
  saveCalibration: (body: CalibrationBody) =>
    request<{ calibration: CalibrationResult; overlay: CalibrationOverlay }>('/api/calibrations', json('POST', body)),
}

export interface CalibrationBody {
  camera_id: string
  image_width: number
  image_height: number
  points: CalibrationPoint[]
  label?: string
}
