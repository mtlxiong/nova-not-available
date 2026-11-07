import { useState } from 'react'
import { api } from '../lib/api'
import { useTwin } from '../state/twin'
import { useReplay, useScene } from '../state/ui'

export function TopBar({ connected, onNew }: { connected: boolean; onNew: () => void }) {
  const status = useTwin((s) => s.status)
  const mode = useTwin((s) => s.mode)
  const view = useScene((s) => s.view)
  const showHeatmap = useScene((s) => s.showHeatmap)
  const videoReady = useScene((s) => s.videoReady)
  const replaySession = useReplay((s) => s.data?.session.session_id ?? null)
  const heatmapSession = mode === 'replay' ? replaySession : status.session_id
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const running = status.state === 'running' || status.state === 'stopping'

  const stop = async () => {
    setBusy(true)
    setError(null)
    try {
      useTwin.getState().setStatus(await api.stopSession())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const stateClass = status.state === 'running' ? 'text-emerald-300' : status.state === 'error' ? 'text-rose-300' : 'text-mute'

  return (
    <header className="relative shrink-0 border-b border-white/[0.08] bg-[#141c2e]">
      <div className="flex items-center gap-3 px-4 py-2.5">
        <span className="flex items-center gap-2">
          <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden>
            <path d="M8 1.5 14 5v6L8 14.5 2 11V5L8 1.5Z" />
            <path d="M8 8 14 5M8 8 2 5M8 8v6.5" />
          </svg>
          <span className="text-sm font-medium tracking-[0.06em]">ECHO</span>
        </span>
        <span className={`h-1.5 w-1.5 rounded-full ${connected ? 'bg-emerald-400' : 'bg-rose-400'}`} title={connected ? 'Connected' : 'Backend disconnected'} />
        <span className={`text-xs capitalize ${stateClass}`}>{status.state}</span>
        {status.simulated && <span className="text-xs text-mute">Simulated</span>}
        <div className="seg absolute left-1/2 -translate-x-1/2">
          <button className={view === 'iso' ? 'on' : ''} onClick={() => useScene.getState().setView('iso')}>
            Iso
          </button>
          <button className={view === 'top' ? 'on' : ''} onClick={() => useScene.getState().setView('top')}>
            Top
          </button>
          <button
            className={view === 'video' ? 'on' : ''}
            disabled={!videoReady}
            title={videoReady ? 'Look through the camera that filmed this workspace' : 'This workspace has no camera pose'}
            onClick={() => useScene.getState().setView('video')}
          >
            Video
          </button>
          <button
            className={showHeatmap ? 'on' : ''}
            aria-pressed={showHeatmap}
            disabled={!heatmapSession}
            title={heatmapSession ? 'Show where tracked movement has spent time on the floor' : 'Start a session to see movement'}
            onClick={() => useScene.getState().set({ showHeatmap: !showHeatmap })}
          >
            Heatmap
          </button>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {running && (
            <button className="btn btn-danger rounded-full" disabled={busy} onClick={stop}>
              Stop
            </button>
          )}
          <button className="btn rounded-full" onClick={onNew}>
            New session
          </button>
        </div>
      </div>
      {(error || (status.state === 'error' && status.message)) && (
        <p className="px-4 pb-2 text-xs text-rose-300">{error || status.message}</p>
      )}
    </header>
  )
}
