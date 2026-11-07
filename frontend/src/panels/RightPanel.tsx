import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { withBackend } from '../lib/backend'
import type { Alignment, AnalysisJob, ReplayEvent, SessionMetrics, WorkflowAdvice } from '../lib/types'
import { entityColor, visualState } from '../scene/EntitiesLayer'
import { useTwin } from '../state/twin'
import { useReplay, useScene } from '../state/ui'
import { applyWorkspace, WorkspacePanel } from './WorkspacePanel'
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
    <div className="mt-3 border-t border-white/10 pt-3">
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
          <div className="border-t border-white/10 pt-2 text-mute">
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

function splitAdvice(text: string) {
  const clean = text.trim()
  const cut = clean.search(/[.!?]/)
  if (cut >= 18 && cut <= 96) return { title: clean.slice(0, cut), rest: clean.slice(cut + 1).trim() }
  const word = clean.lastIndexOf(' ', 68)
  const at = word > 24 ? word : Math.min(clean.length, 68)
  return { title: clean.slice(0, at), rest: clean.slice(at).trim() }
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg viewBox="0 0 16 16" className={`h-4 w-4 shrink-0 text-mute transition ${open ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden>
      <path d="M6 3.5 10.5 8 6 12.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function AnalysisCard({
  text,
  open,
  detail,
  busy,
  onToggle,
  onDeeper,
}: {
  text: string
  open: boolean
  detail: string | null
  busy: boolean
  onToggle: () => void
  onDeeper: () => void
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.04]">
      <button type="button" className="flex w-full items-start gap-3 px-3 py-3 text-left" aria-expanded={open} onClick={onToggle}>
        <span className="min-w-0 flex-1">
          <span className="block text-sm text-paper">{splitAdvice(text).title}</span>
          {!open && splitAdvice(text).rest && <span className="mt-1 line-clamp-2 text-xs leading-relaxed text-mute">{splitAdvice(text).rest}</span>}
        </span>
        <Chevron open={open} />
      </button>
      {open && (
        <div className="px-3 pb-3">
          <p className="text-sm leading-relaxed text-mute">{text}</p>
          {detail && <p className="mt-3 text-sm leading-relaxed text-paper">{detail}</p>}
          {!detail && (
            <button type="button" className="btn mt-3" disabled={busy} onClick={onDeeper}>
              {busy ? 'Looking' : 'Look further'}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function AdviceAgent({ notice, view }: { notice: string | null; view: 'overview' | 'insights' | 'notes' }) {
  const mode = useTwin((s) => s.mode)
  const liveSession = useTwin((s) => s.status.session_id)
  const replay = useReplay((s) => s.data)
  const model = useScene((s) => s.model)
  const sessionId = mode === 'replay' ? replay?.session.session_id ?? null : liveSession
  const [metrics, setMetrics] = useState<SessionMetrics | null>(null)
  const [base, setBase] = useState<WorkflowAdvice | null>(null)
  const [turns, setTurns] = useState<{ question: string; advice: WorkflowAdvice }[]>([])
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const [deeper, setDeeper] = useState<Record<string, string>>({})
  const [digging, setDigging] = useState<string | null>(null)
  const narrated = useRef<string | null>(null)
  const threadRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setMetrics(null)
    setBase(null)
    setTurns([])
    setError(null)
    narrated.current = null
    if (!sessionId) return
    let stop = false
    const load = () =>
      api
        .metrics(sessionId)
        .then((m) => {
          if (!stop) setMetrics(m)
        })
        .catch(() => undefined)
    load()
    const timer = setInterval(load, 4000)
    return () => {
      stop = true
      clearInterval(timer)
    }
  }, [sessionId])

  const hasVisits = !!metrics?.zones.some((z) => z.visits > 0)
  useEffect(() => {
    if (!sessionId || !hasVisits || narrated.current === sessionId) return
    let stop = false
    narrated.current = sessionId
    const take = (advice: WorkflowAdvice) => {
      if (!stop && advice.session_id === sessionId) setBase(advice)
    }
    api
      .workflowAdvice(sessionId)
      .then(take)
      .catch(() => api.workflowAdvice(sessionId).then(take))
      .catch((e) => {
        narrated.current = null
        if (!stop) setError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      stop = true
    }
  }, [sessionId, hasVisits])

  useEffect(() => {
    if (!turns.length) return
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight })
  }, [turns.length])

  const shown = base && base.session_id === sessionId ? base : null
  const rows = (shown?.process_times?.length ? shown.process_times : (metrics?.zones ?? []).filter((z) => z.visits > 0))
    .map((z) => ({ id: z.zone_id, name: z.name, seconds: 'process_time_s' in z ? z.process_time_s : z.mean_dwell_s }))
    .sort((a, b) => (b.seconds ?? 0) - (a.seconds ?? 0))

  const ask = async () => {
    const text = question.trim()
    if (!sessionId || !text || busy) return
    setBusy(true)
    setError(null)
    try {
      const advice = await api.workflowAdvice(sessionId, text)
      setTurns((cur) => [...cur, { question: text, advice }])
      setQuestion('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const lookFurther = async (id: string, text: string) => {
    if (!sessionId || digging) return
    setDigging(id)
    try {
      const advice = await api.workflowAdvice(sessionId, `Explain this further from the measured facts only: ${text}`.slice(0, 400))
      setDeeper((cur) => ({ ...cur, [id]: advice.summary || advice.reason || 'No further detail.' }))
    } catch (e) {
      setDeeper((cur) => ({ ...cur, [id]: e instanceof Error ? e.message : String(e) }))
    } finally {
      setDigging(null)
    }
  }

  const duration = metrics ? `${Math.floor(metrics.duration_ms / 60000)}:${((metrics.duration_ms % 60000) / 1000).toFixed(1).padStart(4, '0')}` : null
  const cards = [
    ...(shown?.summary ? [{ id: 'summary', text: shown.summary }] : []),
    ...(shown?.suggestions ?? []).map((text, i) => ({ id: `s-${i}`, text })),
  ]

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {view === 'overview' && (
        <div className="shrink-0 px-3 pt-3">
          <div className="rounded-xl border border-white/10 bg-[#1c2740] px-3 py-3">
            <div className="flex items-baseline justify-between gap-3 text-xs text-mute">
              <span>{metrics?.simulated || shown?.simulated ? 'Simulated session' : sessionId ? 'Live session' : 'No session'}</span>
              {duration && <span className="font-mono">Duration {duration}</span>}
            </div>
            {rows.length > 0 ? (
              <ul className="mt-3 max-h-52 space-y-2 overflow-y-auto">
                {rows.map((z) => (
                  <li key={z.id} className="flex items-baseline justify-between gap-3">
                    <span className="text-sm text-paper">{z.name}</span>
                    <span className="font-mono text-sm text-paper">{z.seconds != null ? `${z.seconds.toFixed(1)} s` : '—'}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-sm leading-relaxed text-mute">{sessionId ? 'Waiting for a zone visit.' : 'Open a session. Station times show up here.'}</p>
            )}
          </div>
        </div>
      )}
      <div ref={threadRef} className="min-h-0 flex-1 overflow-y-auto px-3 py-3" aria-live="polite">
        {view === 'overview' && (
          <div className="space-y-3">
            <div>
              <p className="px-1 text-xs font-medium text-mute">Analysis</p>
              <div className="mt-2 space-y-2">
                {shown?.status === 'unavailable' && shown.reason && <p className="rounded-xl border border-white/10 px-3 py-3 text-sm leading-relaxed text-amber-200">{shown.reason}</p>}
                {cards.map((card) => (
                  <AnalysisCard
                    key={card.id}
                    text={card.text}
                    open={openId === card.id}
                    detail={deeper[card.id] ?? null}
                    busy={digging === card.id}
                    onToggle={() => setOpenId(openId === card.id ? null : card.id)}
                    onDeeper={() => lookFurther(card.id, card.text)}
                  />
                ))}
                {shown?.status === 'ok' && cards.length === 0 && <p className="px-1 text-sm text-mute">No analysis yet.</p>}
                {!shown && sessionId && rows.length > 0 && <p className="px-1 text-sm text-mute">Reading the station times.</p>}
              </div>
            </div>
            {turns
              .filter((t) => t.advice.session_id === sessionId)
              .map((t, i) => (
                <div key={`${i}-${t.question}`} className="space-y-2 px-1">
                  <p className="text-right text-sm text-paper">{t.question}</p>
                  <p className="text-sm leading-relaxed text-mute">{t.advice.summary || t.advice.reason}</p>
                </div>
              ))}
          </div>
        )}
        {view === 'insights' && (
          <div className="space-y-3 text-sm">
            {shown?.handoffs?.length ? (
              <ul className="space-y-2">
                {shown.handoffs.map((h) => (
                  <li key={`${h.from_zone}-${h.to_zone}`} className="rounded-xl border border-white/10 bg-white/[0.04] px-3 py-3">
                    <p className="text-paper">
                      {h.from_name} to {h.to_name}
                    </p>
                    <p className="mt-1 text-mute">
                      {h.count} moves · {h.mean_gap_s.toFixed(1)} s between
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-mute">No moves between stations yet.</p>
            )}
            {metrics?.data_quality?.calibration_rmse_m != null && (
              <p className="text-mute">Calibration error {metrics.data_quality.calibration_rmse_m.toFixed(3)} m</p>
            )}
          </div>
        )}
        {view === 'notes' && (
          <div className="space-y-2 text-sm">
            {model?.notes?.length ? (
              model.notes.map((n, i) => (
                <p key={i} className="rounded-xl border border-white/10 bg-white/[0.04] px-3 py-3 text-mute">
                  {n}
                </p>
              ))
            ) : (
              <p className="text-mute">{model ? `${model.name}. No notes yet.` : 'No workspace loaded.'}</p>
            )}
          </div>
        )}
        {notice && <p className="mt-3 text-sm text-mute">{notice}</p>}
        {error && <p className="mt-3 text-sm text-rose-200">{error}</p>}
      </div>
      <form
        className="flex shrink-0 items-center gap-2 border-t border-white/[0.08] px-3 py-3"
        onSubmit={(e) => {
          e.preventDefault()
          ask()
        }}
      >
        <label className="min-w-0 flex-1">
          <span className="sr-only">Ask about this room</span>
          <input
            value={question}
            disabled={!sessionId || busy}
            placeholder={sessionId ? 'Ask about this room…' : 'Start a session to ask'}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                ask()
              }
            }}
            className="input w-full"
          />
        </label>
        <button className="btn btn-primary" type="submit" disabled={!sessionId || busy || !question.trim()}>
          {busy ? 'Sending' : 'Send'}
        </button>
      </form>
    </div>
  )
}

export function RightPanel() {
  const [pane, setPane] = useState<'chat' | 'edit'>('chat')
  const [view, setView] = useState<'overview' | 'insights' | 'notes'>('overview')
  const [more, setMore] = useState<Tab>('entities')
  const running = useTwin((s) => s.status.state === 'running')
  const [job, setJob] = useState<AnalysisJob | null>(null)
  const [computeError, setComputeError] = useState<string | null>(null)
  const applied = useRef<string | null>(null)

  useEffect(() => {
    if (!job || job.state !== 'running') return
    const timer = setInterval(async () => {
      try {
        const next = await api.analysisStatus(job.job_id)
        setJob(next)
        if (next.state === 'done' && next.result && applied.current !== next.job_id) {
          applied.current = next.job_id
          const keepsRoom = (useScene.getState().model?.fixtures.length ?? 0) > 0 && next.result.model.fixtures.length === 0
          if (!keepsRoom) await applyWorkspace(next.result.model.model_id)
        }
        if (next.state === 'error') setComputeError(next.error ?? 'Workspace compute failed')
      } catch (e) {
        setComputeError(e instanceof Error ? e.message : String(e))
      }
    }, 1000)
    return () => clearInterval(timer)
  }, [job])

  const compute = async () => {
    setComputeError(null)
    try {
      const started = await api.analyzeWorkspace({ video_id: null })
      setJob(started)
      if (started.state === 'done' && started.result && applied.current !== started.job_id) {
        applied.current = started.job_id
        const keepsRoom = (useScene.getState().model?.fixtures.length ?? 0) > 0 && started.result.model.fixtures.length === 0
        if (!keepsRoom) await applyWorkspace(started.result.model.model_id)
      }
      if (started.state === 'error') setComputeError(started.error ?? 'Workspace compute failed')
    } catch (e) {
      setComputeError(e instanceof Error ? e.message : String(e))
    }
  }

  const notice = computeError ?? (job?.state === 'running' ? job.stage || 'Computing the floor' : null)

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between px-4 py-2.5">
        <span className="text-xs font-medium">Workspace</span>
        <span className="flex items-center gap-3">
          <button
            className="text-xs font-medium text-mute transition hover:text-paper disabled:cursor-not-allowed disabled:opacity-40"
            disabled={!running || job?.state === 'running'}
            title={running ? 'Measure the floor from this session, including the simulation' : 'Start a session first, including the simulation'}
            onClick={compute}
          >
            {job?.state === 'running' ? 'Computing' : 'Compute'}
          </button>
          <button className="text-xs font-medium text-mute transition hover:text-paper" onClick={() => setPane(pane === 'chat' ? 'edit' : 'chat')}>
            {pane === 'chat' ? 'Edit' : 'Advice'}
          </button>
        </span>
      </div>
      {pane === 'chat' && (
        <div className="px-3">
          <div className="seg w-full">
            {(['overview', 'insights', 'notes'] as const).map((item) => (
              <button key={item} className={`flex-1 capitalize ${view === item ? 'on' : ''}`} onClick={() => setView(item)}>
                {item}
              </button>
            ))}
          </div>
        </div>
      )}
      {pane === 'chat' ? (
        <AdviceAgent notice={notice} view={view} />
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
          <WorkspacePanel />
          <details className="mt-6">
            <summary className="cursor-pointer text-sm text-mute">More</summary>
            <div className="mt-4">
              <div className="seg">
                {(['entities', 'events', 'metrics', 'scene'] as Tab[]).map((t) => (
                  <button key={t} className={more === t ? 'on' : ''} onClick={() => setMore(t)}>
                    {t}
                  </button>
                ))}
              </div>
              <div className="mt-4 text-xs">
                {more === 'entities' && <EntitiesTab />}
                {more === 'events' && <EventsTab />}
                {more === 'metrics' && <MetricsTab />}
                {more === 'scene' && <SceneTab />}
              </div>
            </div>
          </details>
        </div>
      )}
    </div>
  )
}
