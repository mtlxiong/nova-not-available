import { useEffect, useState } from 'react'
import { api } from './lib/api'
import { connect } from './lib/ws'
import { reloadLiveModel } from './lib/workspace'
import { resumeWorldGeneration } from './lib/worldgen'
import type { ServerMessage } from './lib/types'
import { refreshActiveCalibration } from './panels/CalibrationPanel'
import { Landing } from './panels/Landing'
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
  const [ready, setReady] = useState(false)
  const [studio, setStudio] = useState(false)
  const [leaving, setLeaving] = useState(false)
  const [entered, setEntered] = useState(false)
  const sceneId = useScene((s) => s.sceneId)
  const scenes = useScene((s) => s.scenes)

  useEffect(() => {
    reloadLiveModel()
      .then(() => {
        const id = useScene.getState().model?.model_id
        if (id) resumeWorldGeneration(id)
      })
      .catch((e) => setLoadError(String(e)))
    loadScenes().catch(() => undefined)
    refreshActiveCalibration()
    const conn = connect(dispatch, setConnected)
    api
      .currentSession()
      .catch(() => undefined)
      .finally(() => setReady(true))
    return () => conn.close()
  }, [])

  useEffect(() => {
    const scene = scenes.find((s) => s.scene_id === sceneId)
    if (scene) useScene.getState().set({ alignment: scene.config.alignment })
  }, [sceneId, scenes])

  const openStudio = () => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduced) {
      setStudio(true)
      return
    }
    setLeaving(true)
    window.setTimeout(() => {
      setLeaving(false)
      setEntered(true)
      setStudio(true)
    }, 520)
  }

  if (!ready) return <div className="h-screen bg-ink" />
  if (!studio) {
    return (
      <div className="flex h-screen flex-col bg-ink">
        {loadError && <p className="px-8 pt-4 text-sm text-rose-200">Backend not reachable. {loadError}</p>}
        <Landing onOpen={openStudio} leaving={leaving} />
      </div>
    )
  }

  return (
    <div className={`${entered ? 'studio-in' : ''} flex h-screen flex-col overflow-hidden`}>
      <TopBar connected={connected} onNew={() => { setStudio(false); setEntered(false) }} />
      {loadError && (
        <div className="border-b border-rose-400/30 bg-rose-950/50 px-4 py-1.5 text-xs text-rose-100">
          Backend not reachable: {loadError}
        </div>
      )}
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto p-3 lg:grid-cols-[minmax(280px,30%)_minmax(0,1fr)_minmax(320px,32%)] lg:overflow-hidden">
        <section className="min-h-[42vh] overflow-hidden rounded-xl border border-white/10 bg-panel lg:min-h-0">
          <VideoPanel />
        </section>
        <section className="min-h-[42vh] overflow-hidden rounded-xl border border-white/10 lg:min-h-0">
          <TwinViewer />
        </section>
        <section className="min-h-[42vh] overflow-hidden rounded-xl border border-white/10 bg-[#161f33] lg:min-h-0">
          <RightPanel />
        </section>
      </div>
      <Timeline />
    </div>
  )
}
