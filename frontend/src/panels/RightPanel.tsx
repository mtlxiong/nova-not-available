import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { withBackend } from '../lib/backend'
import type { Alignment, ReplayEvent, WorkflowAdvice } from '../lib/types'
import { entityColor, visualState } from '../scene/EntitiesLayer'
import { useTwin } from '../state/twin'
import { useReplay, useScene } from '../state/ui'
import { WorkspacePanel } from './WorkspacePanel'
import { useZoneName } from '../lib/workspace'

type Tab = 'workspace' | 'entities' | 'events' | 'metrics' | 'scene'

const fmtT = (ms: number) => `${Math.floor(ms / 60000)}:${((ms % 60000) / 1000).toFixed(1).padStart(4, '0')}`

function EntitiesTab() {
  const zoneName = useZoneName()
  const ids = useTwin((s) => s.entityIds)
  const entities = useTwin((s) => s.entities)
  const selectedId = useTwin((s) => s.selectedId)
  const select = useTwin((s) => s.select)
  const now = Date.now()
  return (
    <div className="space-y-0.5">
      {ids.length === 0 && <div className="text-zinc-500">No tracked entities.</div>}
      {ids.map((id) => {
        const e = entities[id]
        if (!e) return null
        const vs = visualState(e, now)
        return (
          <button
            key={id}
            onClick={() => select(selectedId === id ? null : id)}
            className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left ${selectedId === id ? 'bg-white/10' : 'hover:bg-white/[0.04]'}`}
          >
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: vs === 'lost' ? '#71717a' : entityColor(e) }} />
            <span className="w-28 truncate font-mono">{e.label ?? id}</span>
            <span className="w-16 truncate text-zinc-400">{e.class_name}</span>
            <span className={`w-12 ${vs === 'active' ? 'text-emerald-400' : vs === 'lost' ? 'text-zinc-500' : 'text-amber-400'}`}>{vs}</span>
            <span className="w-20 truncate text-zinc-300">{zoneName(e.zone_id) ?? '—'}</span>
            <span className="ml-auto font-mono text-zinc-500">{e.position ? `${e.position.x.toFixed(1)},${e.position.y.toFixed(1)}` : 'n/a'}</span>
          </button>
        )
      })}
      <SelectedDetail />
    </div>
  )
}

function SelectedDetail() {
  const zoneName = useZoneName()
  const selectedId = useTwin((s) => s.selectedId)
  const e = useTwin((s) => (s.selectedId ? s.entities[s.selectedId] : undefined))
  const mode = useTwin((s) => s.mode)
  const sessionId = useTwin((s) => s.sessionId)
  const liveEvents = useTwin((s) => s.events)
  const replay = useReplay((s) => s.data)
  const [history, setHistory] = useState<{ t_ms: number; x: number | null; y: number | null; state: string; zone_id: string | null }[]>([])

  useEffect(() => {
    if (!selectedId || !sessionId || mode !== 'live') return
    let cancelled = false
    const load = () =>
      fetch(withBackend(`/api/sessions/${sessionId}/entities/${encodeURIComponent(selectedId)}/history`))
        .then((r) => r.json())
        .then((h) => !cancelled && setHistory(h))
        .catch(() => undefined)
    load()
    const t = setInterval(load, 2000)
    return () => {
      cancelled = true
      clearInterval(t)
    }
  }, [selectedId, sessionId, mode])

  const transitions: ReplayEvent[] = useMemo(() => {
    if (!selectedId) return []
    const src: ReplayEvent[] =
      mode === 'replay' && replay
        ? replay.events
        : liveEvents.map((ev) => ({ t_ms: ev.timestamp_ms, kind: ev.kind, entity_id: ev.entity_id, zone_id: ev.zone_id, data: ev.data }))
    return src.filter((ev) => ev.entity_id === selectedId && ev.kind !== 'session.started')
  }, [selectedId, mode, replay, liveEvents])

  const pathLength = useMemo(() => {
    const samples =
      mode === 'replay'
        ? replay?.entities.find((x) => x.entity_id === selectedId)?.samples.filter((s) => s[3] === 'active' && s[1] !== null).map((s) => [s[1], s[2]] as [number, number]) ?? []
        : history.filter((h) => h.state === 'active' && h.x !== null).map((h) => [h.x, h.y] as [number, number])
    let d = 0
    for (let i = 1; i < samples.length; i++) d += Math.hypot(samples[i][0] - samples[i - 1][0], samples[i][1] - samples[i - 1][1])
    return { d, n: samples.length }
  }, [mode, replay, history, selectedId])

  if (!selectedId) return null
  return (
    <div className="mt-3 rounded-xl bg-white/[0.03] p-2.5 ring-1 ring-white/[0.06]">
      <div className="flex items-center justify-between">
        <span className="font-mono font-semibold">{e?.label ?? selectedId}</span>
        <span className="text-zinc-400">{e ? `${e.class_name} · ${e.source}` : 'not present at this time'}</span>
      </div>
      {e && (
        <div className="mt-1 grid grid-cols-2 gap-x-2 text-zinc-400">
          <span>state</span>
          <span className="text-zinc-200">{e.state}</span>
          <span>position</span>
          <span className="font-mono text-zinc-200">{e.position ? `${e.position.x.toFixed(2)}, ${e.position.y.toFixed(2)} m` : 'null (uncalibrated)'}</span>
          <span>zone</span>
          <span className="text-zinc-200">{zoneName(e.zone_id) ?? '—'}</span>
          <span>first / last seen</span>
          <span className="font-mono text-zinc-200">
            {fmtT(e.first_seen_ms)} / {fmtT(e.last_seen_ms)}
          </span>
          {e.object_id && (
            <>
              <span>object</span>
              <span className="text-zinc-200">{e.object_id}</span>
            </>
          )}
        </div>
      )}
      <div className="mt-2 text-zinc-400">
        Recorded path: <span className="font-mono text-zinc-200">{pathLength.d.toFixed(1)} m</span> over {pathLength.n} stored samples (raw, unsmoothed)
      </div>
      <div className="mt-2 font-semibold text-zinc-400">Transitions</div>
      <div className="max-h-40 overflow-y-auto">
        {transitions.length === 0 && <div className="text-zinc-500">none yet</div>}
        {transitions.map((ev, i) => (
          <div key={i} className="flex gap-2 font-mono">
            <span className="w-14 text-zinc-500">{fmtT(ev.t_ms)}</span>
            <span className={ev.kind === 'zone.enter' ? 'text-emerald-400' : ev.kind === 'zone.exit' ? 'text-orange-400' : 'text-zinc-400'}>{ev.kind}</span>
            <span>{zoneName(ev.zone_id) ?? ''}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function EventsTab() {
  const zoneName = useZoneName()
  const mode = useTwin((s) => s.mode)
  const live = useTwin((s) => s.events)
  const replay = useReplay((s) => s.data)
  const playhead = useTwin((s) => s.playheadMs)
  const select = useTwin((s) => s.select)
  const events: ReplayEvent[] =
    mode === 'replay' && replay
      ? replay.events.filter((e) => e.t_ms <= playhead)
      : live.map((ev) => ({ t_ms: ev.timestamp_ms, kind: ev.kind, entity_id: ev.entity_id, zone_id: ev.zone_id, data: ev.data }))
  const recent = events.slice(-150).reverse()
  return (
    <div className="space-y-0.5 font-mono">
      {recent.length === 0 && <div className="font-sans text-zinc-500">No events.</div>}
      {recent.map((ev, i) => (
        <div key={i} className="flex cursor-pointer gap-2 rounded px-1 hover:bg-white/[0.04]" onClick={() => ev.entity_id && select(ev.entity_id)}>
          <span className="w-14 text-zinc-500">{fmtT(ev.t_ms)}</span>
          <span
            className={`w-28 ${ev.kind.startsWith('zone.enter') ? 'text-emerald-400' : ev.kind.startsWith('zone.exit') ? 'text-orange-400' : ev.kind.startsWith('track.lost') ? 'text-amber-400' : 'text-zinc-400'}`}
          >
            {ev.kind}
          </span>
          <span className="truncate">{ev.entity_id ?? ''}</span>
          <span className="ml-auto text-zinc-300">{zoneName(ev.zone_id) ?? ''}</span>
        </div>
      ))}
    </div>
  )
}

function MetricsTab() {
  const metrics = useReplay((s) => s.metrics)
  const metricsSessionId = useReplay((s) => s.metricsSessionId)
  const mode = useTwin((s) => s.mode)
  const liveSession = useTwin((s) => s.sessionId)
  const replay = useReplay((s) => s.data)
  const showHeatmap = useScene((s) => s.showHeatmap)
  const [error, setError] = useState<string | null>(null)
  const [advice, setAdvice] = useState<WorkflowAdvice | null>(null)
  const [adviceBusy, setAdviceBusy] = useState(false)
  const sessionId = mode === 'replay' ? replay?.session.session_id : liveSession

  const load = async () => {
    if (!sessionId) return
    setError(null)
    try {
      useReplay.getState().set({ metrics: await api.metrics(sessionId), metricsSessionId: sessionId })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <button className="btn" disabled={!sessionId} onClick={load}>
          Compute metrics
        </button>
        <button
          className="btn"
          disabled={!sessionId || adviceBusy}
          onClick={async () => {
            if (!sessionId) return
            setAdviceBusy(true)
            setError(null)
            try {
              setAdvice(await api.workflowAdvice(sessionId))
            } catch (e) {
              setError(e instanceof Error ? e.message : String(e))
            } finally {
              setAdviceBusy(false)
            }
          }}
        >
          {adviceBusy ? 'Asking…' : 'Workflow advice'}
        </button>
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={showHeatmap} onChange={(e) => useScene.getState().set({ showHeatmap: e.target.checked })} /> heatmap
        </label>
        <span className="truncate font-mono text-zinc-500">{metricsSessionId}</span>
      </div>
      {error && <div className="text-red-400">{error}</div>}
      {metrics && (
        <>
          <div className="rounded-xl bg-white/[0.03] p-2 text-mute ring-1 ring-white/[0.06]">
            {metrics.simulated && <span className="mr-1 rounded bg-sky-900 px-1 text-sky-200">SIMULATED DATA</span>}
            {metrics.data_quality.positioned_observations}/{metrics.data_quality.observations} observations positioned · calibration RMSE{' '}
            {metrics.data_quality.calibration_rmse_m?.toFixed(3) ?? 'n/a'} m · {(metrics.duration_ms / 1000).toFixed(1)} s
            {metrics.data_quality.notes.map((n) => (
              <div key={n} className="text-amber-400">
                {n}
              </div>
            ))}
          </div>
          <table className="w-full text-left">
            <thead className="text-zinc-500">
              <tr>
                <th>Zone</th>
                <th className="text-right">visits</th>
                <th className="text-right">dwell s</th>
                <th className="text-right">mean s</th>
                <th className="text-right">max occ</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {metrics.zones.map((z) => (
                <tr key={z.zone_id}>
                  <td className="font-sans">{z.name}</td>
                  <td className="text-right">{z.visits}</td>
                  <td className="text-right">{z.total_dwell_s.toFixed(1)}</td>
                  <td className="text-right">{z.mean_dwell_s?.toFixed(1) ?? '—'}</td>
                  <td className="text-right">{z.max_occupancy}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <table className="w-full text-left">
            <thead className="text-zinc-500">
              <tr>
                <th>Track</th>
                <th className="text-right">dist m</th>
                <th className="text-right">tracked s</th>
                <th className="text-right">m/s</th>
                <th>zones</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {metrics.entities.map((e) => (
                <tr key={e.entity_id}>
                  <td>{e.label ?? e.entity_id}</td>
                  <td className="text-right">{e.distance_m?.toFixed(1) ?? '—'}</td>
                  <td className="text-right">{e.tracked_time_s.toFixed(0)}</td>
                  <td className="text-right">{e.mean_speed_mps?.toFixed(2) ?? '—'}</td>
                  <td className="truncate text-zinc-400">{Object.keys(e.zone_dwell_s).join(' ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="text-zinc-500">
            Tracks are anonymous, session-scoped IDs listed in ID order; ECHO does not rank individuals. Distances use calibrated floor
            coordinates with 0.6 s smoothing. Orders: {metrics.orders.available ? 'linked' : metrics.orders.reason}.
          </div>
        </>
      )}
      {advice && advice.session_id === sessionId && (
        <div className="space-y-1 rounded border border-zinc-700 p-2">
          <div className="font-semibold text-zinc-200">
            Station process time
            {advice.simulated && <span className="ml-2 rounded bg-sky-900 px-1 font-normal text-sky-200">SIMULATED DATA</span>}
          </div>
          {advice.process_times.length === 0 && <div className="text-zinc-500">No zone visits in this session.</div>}
          {advice.process_times.map((row) => (
            <div key={row.zone_id} className="flex gap-2 font-mono">
              <span className="flex-1 font-sans">{row.name}</span>
              <span>{row.process_time_s?.toFixed(1) ?? '—'} s/visit</span>
              <span className="text-zinc-500">{row.visits} visits</span>
            </div>
          ))}
          {advice.handoffs.slice(0, 4).map((h) => (
            <div key={`${h.from_zone}-${h.to_zone}`} className="text-zinc-400">
              {h.from_name} → {h.to_name}: {h.mean_gap_s.toFixed(1)} s between stations ({h.count})
            </div>
          ))}
          {advice.status === 'ok' && advice.summary && <div className="text-zinc-100">{advice.summary}</div>}
          {advice.status === 'unavailable' && advice.reason && <div className="text-amber-400">{advice.reason}</div>}
          <ul className="list-disc pl-4 text-zinc-200">
            {advice.suggestions.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function SceneTab() {
  const scene = useScene()
  const scenes = scene.scenes
  const current = scenes.find((s) => s.scene_id === scene.sceneId)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const [saved, setSaved] = useState<string | null>(null)

  const update = (patch: Partial<Alignment>) => {
    const alignment = { ...scene.alignment, ...patch }
    scene.set({ alignment })
    clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(async () => {
      if (!current) return
      await api.putSceneConfig({ ...current.config, scene_id: current.scene_id, alignment })
      setSaved(new Date().toLocaleTimeString())
    }, 400)
  }

  const slider = (label: string, value: number, min: number, max: number, step: number, onChange: (v: number) => void) => (
    <label className="flex items-center gap-2">
      <span className="w-16 text-zinc-400">{label}</span>
      <input type="range" className="flex-1" min={min} max={max} step={step} value={value} onChange={(e) => onChange(parseFloat(e.target.value))} />
      <input className="input w-16 font-mono" type="number" step={step} value={Number(value.toFixed(3))} onChange={(e) => onChange(parseFloat(e.target.value) || 0)} />
    </label>
  )

  const t = scene.alignment.translation
  return (
    <div className="space-y-2">
      <div className="seg">
        <button className={scene.view === 'iso' ? 'on' : ''} onClick={() => scene.setView('iso')}>
          Iso
        </button>
        <button className={scene.view === 'top' ? 'on' : ''} onClick={() => scene.setView('top')}>
          Top
        </button>
        <button className={scene.view === 'video' ? 'on' : ''} onClick={() => scene.setView('video')} title="Match the video camera that this workspace was analyzed from">
          Video
        </button>
      </div>
      <div className="grid grid-cols-2 gap-1">
        {(
          [
            ['showZones', 'Zones'],
            ['showTrails', 'Trails'],
            ['showSplat', 'Splat'],
            ['showCollider', 'Collider mesh'],
          ] as const
        ).map(([key, label]) => (
          <label key={key} className="flex items-center gap-1">
            <input type="checkbox" checked={scene[key]} onChange={(e) => scene.set({ [key]: e.target.checked })} /> {label}
          </label>
        ))}
      </div>
      <div className="font-semibold text-zinc-400">World → scene alignment</div>
      <p className="text-zinc-500">
        Places the authoritative spatial model (meters) inside the rendered environment. Measurements never use the splat.
      </p>
      {slider('x', t[0], -20, 20, 0.05, (v) => update({ translation: [v, t[1], t[2]] }))}
      {slider('y (up)', t[1], -5, 5, 0.01, (v) => update({ translation: [t[0], v, t[2]] }))}
      {slider('z', t[2], -20, 20, 0.05, (v) => update({ translation: [t[0], t[1], v] }))}
      {slider('yaw °', scene.alignment.yaw_deg, -180, 180, 0.5, (v) => update({ yaw_deg: v }))}
      {slider('scale', scene.alignment.scale, 0.1, 5, 0.01, (v) => update({ scale: Math.max(0.01, v) }))}
      <div className="flex items-center gap-2">
        <button className="btn" onClick={() => update({ translation: [0, 0, 0], yaw_deg: 0, scale: 1 })}>
          Reset
        </button>
        {saved && <span className="text-zinc-500">saved {saved}</span>}
      </div>
      {current?.notes && <div className="text-zinc-500">{current.notes}</div>}
    </div>
  )
}

export function RightPanel() {
  const [tab, setTab] = useState<Tab>('workspace')
  return (
    <div className="flex h-full flex-col text-xs">
      <div className="flex border-b border-white/10">
        {(['workspace', 'entities', 'events', 'metrics', 'scene'] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 border-b py-2 capitalize ${tab === t ? 'border-paper text-paper' : 'border-transparent text-mute hover:text-paper'}`}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {tab === 'workspace' && <WorkspacePanel />}
        {tab === 'entities' && <EntitiesTab />}
        {tab === 'events' && <EventsTab />}
        {tab === 'metrics' && <MetricsTab />}
        {tab === 'scene' && <SceneTab />}
      </div>
    </div>
  )
}
