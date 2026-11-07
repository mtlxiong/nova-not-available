import { useEffect, useState } from 'react'
import { api } from './lib/api'
import { connect } from './lib/ws'
import { reloadLiveModel } from './lib/workspace'
import type { ServerMessage } from './lib/types'
import { refreshActiveCalibration } from './panels/CalibrationPanel'
import { RightPanel } from './panels/RightPanel'
import { Timeline } from './panels/Timeline'
import { TopBar } from './panels/TopBar'
import { VideoPanel } from './panels/VideoPanel'
import { TwinViewer } from './scene/TwinViewer'
import { useTwin } from './state/twin'
import { useScene } from './state/ui'

export async function loadScenes() {
  const scenes = await api.scenes()
  const { sceneId } = useScene.getState()
  const current = scenes.find((s) => s.scene_id === sceneId) ?? scenes[0]
  useScene.getState().set({ scenes, sceneId: current.scene_id, alignment: current.config.alignment })
}

export function dispatch(msg: ServerMessage) {
  const twin = useTwin.getState()
  switch (msg.type) {
    case 'tracking.update':
      twin.applyTrackingUpdate(msg)
      break
    case 'event':
      twin.pushEvent(msg.event)
      break
    case 'session.status': {
      const prev = twin.status
      twin.setStatus(msg)
      if (msg.session_id !== prev.session_id || msg.calibrated !== prev.calibrated || msg.message !== prev.message) refreshActiveCalibration()
      if (msg.spatial_model_id !== prev.spatial_model_id || msg.state !== prev.state) reloadLiveModel().catch(() => undefined)
      break
    }
  }
}

export default function App() {
  const [connected, setConnected] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const sceneId = useScene((s) => s.sceneId)
  const scenes = useScene((s) => s.scenes)

  useEffect(() => {
    reloadLiveModel().catch((e) => setLoadError(String(e)))
    loadScenes().catch(() => undefined)
    refreshActiveCalibration()
    const conn = connect(dispatch, setConnected)
    return () => conn.close()
  }, [])

  useEffect(() => {
    const scene = scenes.find((s) => s.scene_id === sceneId)
    if (scene) useScene.getState().set({ alignment: scene.config.alignment })
  }, [sceneId, scenes])

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <TopBar connected={connected} />
      {loadError && <div className="bg-red-900 px-3 py-1 text-xs">Backend not reachable: {loadError}</div>}
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(320px,30%)_1fr_minmax(300px,24%)]">
        <div className="min-h-0 border-r border-zinc-800">
          <VideoPanel />
        </div>
        <div className="min-h-0">
          <TwinViewer />
        </div>
        <div className="min-h-0 border-l border-zinc-800">
          <RightPanel />
        </div>
      </div>
      <Timeline />
    </div>
  )
}
