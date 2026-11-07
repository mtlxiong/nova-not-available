import { useTwin } from '../state/twin'
import { useScene, useWorkspace } from '../state/ui'
import { api } from './api'
import type { SpatialModel } from './types'

function show(model: SpatialModel) {
  const prev = useScene.getState().model
  useScene.getState().set({ model })
  if (!prev || prev.model_id !== model.model_id) useWorkspace.getState().set({ draft: null, dirty: false, zoneDraft: null })
}

/** Live mode: the running session's workspace, else the active one. */
export async function reloadLiveModel() {
  if (useTwin.getState().mode !== 'live') return
  const id = useTwin.getState().status.spatial_model_id
  show(id ? await api.spatialModelById(id) : await api.spatialModel())
}

/** Display name for a zone id in the shown workspace (ids are opaque in analyzed workspaces). */
export function useZoneName(): (id: string | null | undefined) => string | null {
  const zones = useScene((s) => s.model?.zones)
  const draftZones = useWorkspace((s) => s.draft?.zones)
  return (id) => {
    if (!id) return null
    return (draftZones ?? zones)?.find((z) => z.zone_id === id)?.name ?? id
  }
}

/** Replay mode: the workspace the session was recorded against. */
export async function showSessionModel(sessionId: string) {
  show(await api.sessionModel(sessionId))
}
