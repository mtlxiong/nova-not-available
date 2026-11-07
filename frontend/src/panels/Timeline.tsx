import { useEffect, useMemo, useRef } from 'react'
import { api } from '../lib/api'
import { snapshotAt } from '../lib/replay'
import { useTwin } from '../state/twin'
import { useReplay } from '../state/ui'

import { reloadLiveModel, showSessionModel } from '../lib/workspace'

const fmt = (ms: number) => `${Math.floor(ms / 60000)}:${((ms % 60000) / 1000).toFixed(1).padStart(4, '0')}`

export async function loadSessions() {
  try {
    useReplay.getState().set({ sessions: await api.sessions() })
  } catch {
    /* backend down */
  }
}

/** Drives the shared twin store from stored observations. No computer vision is rerun. */
function useReplayDriver() {
  const data = useReplay((s) => s.data)
  const playing = useReplay((s) => s.playing)
  const speed = useReplay((s) => s.speed)
  const mode = useTwin((s) => s.mode)
  const last = useRef<number | null>(null)

  useEffect(() => {
    if (mode !== 'replay' || !data || !playing) {
      last.current = null
      return
    }
    let raf = 0
    const tick = (now: number) => {
      const dt = last.current === null ? 0 : now - last.current
      last.current = now
      const t = Math.min(data.session.duration_ms, useTwin.getState().playheadMs + dt * speed)
      useTwin.getState().applySnapshot(snapshotAt(data, t), t, data.session.session_id)
      if (t >= data.session.duration_ms) useReplay.getState().set({ playing: false })
      else raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [mode, data, playing, speed])
}

export function seek(t: number) {
  const data = useReplay.getState().data
  if (!data) return
  const clamped = Math.max(0, Math.min(data.session.duration_ms, t))
  useTwin.getState().applySnapshot(snapshotAt(data, clamped), clamped, data.session.session_id)
}

export function Timeline() {
  useReplayDriver()
  const mode = useTwin((s) => s.mode)
  const status = useTwin((s) => s.status)
  const playhead = useTwin((s) => s.playheadMs)
  const selectedId = useTwin((s) => s.selectedId)
  const { sessions, data, playing, speed } = useReplay()

  useEffect(() => {
    loadSessions()
  }, [status.state])

  const openReplay = async (id: string) => {
    const replay = await api.replay(id)
    useReplay.getState().set({ data: replay, playing: false, metrics: null, metricsSessionId: null })
    useTwin.getState().setMode('replay')
    showSessionModel(id).catch(() => undefined)
    seek(0)
  }

  const goLive = () => {
    useReplay.getState().set({ playing: false })
    useTwin.getState().setMode('live')
    reloadLiveModel().catch(() => undefined)
  }

  const ticks = useMemo(() => {
    if (!data) return []
    return data.events.filter((e) => (e.kind === 'zone.enter' || e.kind === 'zone.exit') && (!selectedId || e.entity_id === selectedId))
  }, [data, selectedId])

  const duration = data?.session.duration_ms ?? 0
  return (
    <div className="flex items-center gap-3 border-t border-zinc-800 bg-zinc-900/80 px-3 py-2 text-xs">
      <div className="flex items-center gap-1">
        <button className={`btn ${mode === 'live' ? 'btn-primary' : ''}`} onClick={goLive}>
          ● Live
        </button>
        <select
          className="input max-w-64"
          value={mode === 'replay' ? data?.session.session_id ?? '' : ''}
          onFocus={loadSessions}
          onChange={(e) => e.target.value && openReplay(e.target.value)}
        >
          <option value="">Replay session…</option>
          {sessions.map((s) => (
            <option key={s.session_id} value={s.session_id} disabled={s.state === 'running'}>
              {new Date(s.created_wall_ms).toLocaleTimeString()} · {s.source_kind}
              {s.simulated ? ' (sim)' : ''} · {(s.duration_ms / 1000).toFixed(0)}s · {s.state}
            </option>
          ))}
        </select>
      </div>
      {mode === 'replay' && data ? (
        <>
          <button className="btn" onClick={() => seek(0)} title="Rewind">
            ⏮
          </button>
          <button className="btn" onClick={() => seek(playhead - 5000)} title="Back 5 s">
            −5s
          </button>
          <button className="btn btn-primary w-14" onClick={() => useReplay.getState().set({ playing: !playing })}>
            {playing ? '❚❚' : '▶'}
          </button>
          <button className="btn" onClick={() => seek(playhead + 5000)} title="Forward 5 s">
            +5s
          </button>
          <select className="input" value={speed} onChange={(e) => useReplay.getState().set({ speed: parseFloat(e.target.value) })}>
            {[0.25, 0.5, 1, 2, 4, 8].map((s) => (
              <option key={s} value={s}>
                {s}×
              </option>
            ))}
          </select>
          <span className="w-24 font-mono text-zinc-300">
            {fmt(playhead)} / {fmt(duration)}
          </span>
          <div className="relative flex-1">
            <input
              type="range"
              className="w-full"
              min={0}
              max={duration}
              step={50}
              value={playhead}
              onChange={(e) => seek(parseFloat(e.target.value))}
            />
            <div className="pointer-events-none absolute inset-x-0 -bottom-1 h-1.5">
              {ticks.map((e, i) => (
                <span
                  key={i}
                  className={`absolute h-1.5 w-px ${e.kind === 'zone.enter' ? 'bg-emerald-400' : 'bg-orange-400'}`}
                  style={{ left: `${(e.t_ms / Math.max(1, duration)) * 100}%` }}
                />
              ))}
            </div>
          </div>
          {data.session.simulated && <span className="rounded bg-sky-900 px-1 text-sky-200">SIMULATED</span>}
        </>
      ) : (
        <span className="font-mono text-zinc-400">
          {status.state === 'running' ? `live · t=${fmt(playhead)}` : 'idle — start a session or open a stored session for replay'}
        </span>
      )}
    </div>
  )
}
