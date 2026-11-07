import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { withBackend } from '../lib/backend'
import { useVideos } from '../state/videos'
import { AddVideosButton } from './AddVideosButton'
import { fixtureName, localId, PROVENANCE_COLORS } from '../lib/model'
import { ZONE_KINDS, type AnalysisJob, type SpatialModel, type SpatialModelListItem, type ZoneKind } from '../lib/types'
import { reloadLiveModel } from '../lib/workspace'
import { setWorldView } from '../lib/worldgen'
import { useTwin } from '../state/twin'
import { useCalibration, useScene, useWorkspace } from '../state/ui'
import { refreshActiveCalibration } from './CalibrationPanel'

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

function useShownModel(): SpatialModel | null {
  const saved = useScene((s) => s.model)
  const draft = useWorkspace((s) => s.draft)
  return draft && saved && draft.model_id === saved.model_id ? draft : saved
}

function edit(fn: (m: SpatialModel) => void) {
  const saved = useScene.getState().model
  const { draft } = useWorkspace.getState()
  const base = draft && saved && draft.model_id === saved.model_id ? draft : saved
  if (!base) return
  const next = structuredClone(base)
  fn(next)
  useWorkspace.getState().set({ draft: next, dirty: true })
}

export async function applyWorkspace(modelId: string) {
  await api.activateSpatialModel(modelId)
  useWorkspace.getState().set({ draft: null, dirty: false, zoneDraft: null })
  await reloadLiveModel()
  await refreshActiveCalibration()
}

function AnalyzeForm({ onListChanged }: { onListChanged: () => void }) {
  const status = useTwin((s) => s.status)
  const live = status.state === 'running'
  const videos = useVideos((s) => s.videos)
  const [from, setFrom] = useState<'video' | 'live'>(status.state === 'running' ? 'live' : 'video')
  const [videoId, setVideoId] = useState('')
  const [calibrationMode, setCalibrationMode] = useState<'auto' | 'existing'>('auto')
  const [calibrationId, setCalibrationId] = useState('')
  const [calibrations, setCalibrations] = useState<{ calibration_id: string; label: string | null; method?: string }[]>([])
  const [name, setName] = useState('')
  const [personHeight, setPersonHeight] = useState(1.7)
  const [hfov, setHfov] = useState(65)
  const [confidence, setConfidence] = useState(0.15)
  const [vocab, setVocab] = useState('')
  const [job, setJob] = useState<AnalysisJob | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    useVideos.getState().refresh().then(() => {
      const list = useVideos.getState().videos
      if (list[0]) setVideoId((cur) => cur || list[0].video_id)
    }).catch(() => undefined)
    api.calibrations().then(setCalibrations).catch(() => undefined)
  }, [status.state])

  useEffect(() => {
    if (!job || job.state !== 'running') return
    const t = setInterval(async () => {
      try {
        const j = await api.analysisStatus(job.job_id)
        setJob(j)
        if (j.state !== 'running') onListChanged()
      } catch (e) {
        setError(errText(e))
      }
    }, 1000)
    return () => clearInterval(t)
  }, [job, onListChanged])

  const run = async () => {
    setError(null)
    try {
      const words = vocab.split(/[,\n]/).map((w) => w.trim()).filter(Boolean)
      setJob(
        await api.analyzeWorkspace({
          video_id: from === 'video' ? videoId : null,
          calibration_id: calibrationMode === 'existing' ? calibrationId || null : null,
          name: name.trim() || null,
          vocabulary: words.length ? words : null,
          person_height_m: personHeight,
          hfov_deg: hfov,
          object_confidence: confidence,
        }),
      )
    } catch (e) {
      setError(errText(e))
    }
  }

  const result = job?.state === 'done' ? job.result : null
  return (
    <div className="space-y-2 border-t border-white/10 pt-3">
      <div className="text-sm font-medium text-paper">Analyze</div>
      <div className="flex gap-3">
        <label className="flex items-center gap-1">
          <input type="radio" checked={from === 'video'} onChange={() => setFrom('video')} /> Video file
        </label>
        <label className={`flex items-center gap-1 ${live ? '' : 'opacity-40'}`} title={live ? '' : 'Start a session first, including the simulation'}>
          <input type="radio" disabled={!live} checked={from === 'live'} onChange={() => setFrom('live')} /> Running session
        </label>
      </div>
      {from === 'video' && (
        <div className="flex items-start gap-1">
          <select className="input min-w-0 flex-1" value={videoId} onChange={(e) => setVideoId(e.target.value)}>
            {videos.length === 0 && <option value="">no videos yet</option>}
            {videos.map((v) => (
              <option key={v.video_id} value={v.video_id}>
                {v.name} ({v.width}×{v.height})
              </option>
            ))}
          </select>
          <AddVideosButton onAdded={(added) => setVideoId(added[0].video_id)} onError={setError} />
        </div>
      )}
      <div className="space-y-1">
        <label className="flex items-center gap-1">
          <input type="radio" checked={calibrationMode === 'auto'} onChange={() => setCalibrationMode('auto')} /> Auto-calibrate from people (approximate scale)
        </label>
        <label className="flex items-center gap-1">
          <input type="radio" checked={calibrationMode === 'existing'} onChange={() => setCalibrationMode('existing')} /> Use a measured calibration
        </label>
        {calibrationMode === 'existing' && (
          <select className="input w-full" value={calibrationId} onChange={(e) => setCalibrationId(e.target.value)}>
            <option value="">choose…</option>
            {calibrations.map((c) => (
              <option key={c.calibration_id} value={c.calibration_id}>
                {c.label ?? c.calibration_id}
                {c.method ? ` · ${c.method}` : ''}
              </option>
            ))}
          </select>
        )}
      </div>
      <details>
        <summary className="cursor-pointer text-zinc-400">Options</summary>
        <div className="mt-1 grid grid-cols-2 gap-1">
          <label className="col-span-2 flex items-center gap-1">
            Name <input className="input flex-1" placeholder="Workspace from …" value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="flex items-center gap-1" title="Average standing height of people in view; sets the floor scale when auto-calibrating">
            person m <input className="input w-14" type="number" step={0.05} value={personHeight} onChange={(e) => setPersonHeight(parseFloat(e.target.value) || 1.7)} />
          </label>
          <label className="flex items-center gap-1" title="Camera horizontal field of view">
            FOV° <input className="input w-14" type="number" value={hfov} onChange={(e) => setHfov(parseFloat(e.target.value) || 65)} />
          </label>
          <label className="flex items-center gap-1" title="Object detection confidence threshold">
            min conf <input className="input w-14" type="number" step={0.05} min={0.05} max={0.9} value={confidence} onChange={(e) => setConfidence(parseFloat(e.target.value) || 0.15)} />
          </label>
          <label className="col-span-2 flex flex-col gap-0.5">
            <span>Object vocabulary (comma separated; empty = broad default)</span>
            <textarea className="input h-14" placeholder="e.g. workbench, forklift, pallet rack, conveyor" value={vocab} onChange={(e) => setVocab(e.target.value)} />
          </label>
        </div>
      </details>
      <button className="btn btn-primary w-full" disabled={job?.state === 'running' || (from === 'video' && !videoId) || (calibrationMode === 'existing' && !calibrationId)} onClick={run}>
        {job?.state === 'running' ? 'Analyzing…' : 'Analyze'}
      </button>
      {job?.state === 'running' && (
        <div>
          <div className="h-1.5 overflow-hidden rounded bg-zinc-800">
            <div className="h-full bg-sky-500 transition-all" style={{ width: `${Math.round(job.progress * 100)}%` }} />
          </div>
          <div className="mt-0.5 text-[11px] text-zinc-400">{job.stage}</div>
        </div>
      )}
      {job?.state === 'error' && <div className="text-red-400">{job.error}</div>}
      {error && <div className="text-red-400">{error}</div>}
      {result && (
        <div className="space-y-1.5">
          {result.model.background_url && <img src={withBackend(result.model.background_url)} className="w-full rounded" alt="People-free background plate" />}
          <div className="grid grid-cols-2 gap-x-2 font-mono text-[11px] text-zinc-400">
            {Object.entries(result.stats).map(([k, v]) => (
              <span key={k}>
                {k}: {typeof v === 'number' ? (Number.isInteger(v) ? v : v.toFixed(2)) : String(v)}
              </span>
            ))}
          </div>
          {result.calibration.warnings?.length > 0 && (
            <ul className="list-disc pl-4 text-[11px] text-amber-300">
              {result.calibration.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}
          <ul className="list-disc pl-4 text-[11px] text-zinc-400">
            {result.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
          <button
            className="btn btn-primary w-full"
            onClick={() => applyWorkspace(result.model.model_id).then(onListChanged).catch((e) => setError(errText(e)))}
          >
            Use this workspace{live ? ' (applies to the running session)' : ''}
          </button>
        </div>
      )}
    </div>
  )
}

function ObjectsEditor({ model, readOnly }: { model: SpatialModel; readOnly: boolean }) {
  if (!model.fixtures.length) return <div className="text-zinc-500">No objects.</div>
  return (
    <div className="space-y-1">
      {model.fixtures.map((f, i) => (
        <div key={f.fixture_id} className="flex items-center gap-1">
          <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: PROVENANCE_COLORS[f.provenance] }} title={f.provenance} />
          <input
            className="input min-w-0 flex-1"
            value={fixtureName(f)}
            disabled={readOnly}
            title={`detected as "${f.kind}"`}
            onChange={(e) => edit((m) => void (m.fixtures[i].label = e.target.value))}
          />
          <span className="w-9 shrink-0 text-right font-mono text-[10px] text-zinc-500">{f.confidence != null ? `${(f.confidence * 100).toFixed(0)}%` : ''}</span>
          <span
            className="w-24 shrink-0 truncate font-mono text-[10px] text-zinc-500"
            title={f.estimated_dims.length ? `assumed from category: ${f.estimated_dims.join(', ')}` : 'measured from the image'}
          >
            {f.size.map((s) => s.toFixed(1)).join('×')}
            {f.estimated_dims.length ? '*' : ''}
          </span>
          {!readOnly && (
            <button className="btn px-1.5" title="Remove (false detection)" onClick={() => edit((m) => void m.fixtures.splice(i, 1))}>
              ✕
            </button>
          )}
        </div>
      ))}
      <div className="text-[10px] text-zinc-500">Size is W×D×H m; * = dimension assumed from the category, not measured.</div>
    </div>
  )
}

function ZonesEditor({ model, readOnly }: { model: SpatialModel; readOnly: boolean }) {
  const zoneDraft = useWorkspace((s) => s.zoneDraft)
  const hasCalibration = useCalibration((s) => !!(s.preview?.calibration ?? s.active))
  const hasVideo = useTwin((s) => !!s.lastFrame)

  const finish = () => {
    const pts = useWorkspace.getState().zoneDraft ?? []
    if (pts.length < 3) return
    edit((m) =>
      void m.zones.push({ zone_id: localId('zone'), name: `Zone ${m.zones.length + 1}`, kind: 'workstation', polygon: pts, color: '#38bdf8', provenance: 'manual' }),
    )
    useWorkspace.getState().set({ zoneDraft: null })
  }

  return (
    <div className="space-y-1">
      {model.zones.length === 0 && <div className="text-zinc-500">No zones.</div>}
      {model.zones.map((z, i) => (
        <div key={z.zone_id} className="flex items-center gap-1">
          <input type="color" className="h-5 w-5 shrink-0 cursor-pointer bg-transparent" value={z.color} disabled={readOnly} onChange={(e) => edit((m) => void (m.zones[i].color = e.target.value))} />
          <input className="input min-w-0 flex-1" value={z.name} disabled={readOnly} onChange={(e) => edit((m) => void (m.zones[i].name = e.target.value))} />
          <select className="input w-24 shrink-0" value={z.kind} disabled={readOnly} onChange={(e) => edit((m) => void (m.zones[i].kind = e.target.value as ZoneKind))}>
            {ZONE_KINDS.map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
          <span className="w-14 shrink-0 text-[10px] text-zinc-500">{z.provenance}</span>
          {!readOnly && (
            <button className="btn px-1.5" onClick={() => edit((m) => void m.zones.splice(i, 1))}>
              ✕
            </button>
          )}
        </div>
      ))}
      {!readOnly &&
        (zoneDraft ? (
          <div className="flex items-center gap-1">
            <span className="flex-1 text-yellow-300">{zoneDraft.length} corner(s) — click the floor in the video</span>
            <button className="btn" onClick={() => useWorkspace.getState().set({ zoneDraft: zoneDraft.slice(0, -1) })} disabled={!zoneDraft.length}>
              Undo
            </button>
            <button className="btn btn-primary" disabled={zoneDraft.length < 3} onClick={finish}>
              Finish
            </button>
            <button className="btn" onClick={() => useWorkspace.getState().set({ zoneDraft: null })}>
              Cancel
            </button>
          </div>
        ) : (
          <button
            className="btn w-full"
            disabled={!hasCalibration || !hasVideo}
            title={!hasCalibration ? 'Needs an active calibration to map clicks to the floor' : !hasVideo ? 'Needs live video' : ''}
            onClick={() => useWorkspace.getState().set({ zoneDraft: [] })}
          >
            + Draw zone on video
          </button>
        ))}
    </div>
  )
}

export function WorkspacePanel() {
  const model = useShownModel()
  const dirty = useWorkspace((s) => s.dirty)
  const mode = useTwin((s) => s.mode)
  const showLabels = useScene((s) => s.showLabels)
  const showObjects = useScene((s) => s.showObjects)
  const [list, setList] = useState<SpatialModelListItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const worldBuild = useScene((s) => s.worldBuild)
  const imageRef = useRef<HTMLInputElement>(null)

  const refreshList = () => {
    api.spatialModels().then(setList).catch(() => undefined)
  }
  useEffect(refreshList, [model?.model_id])

  if (!model) return <div className="text-zinc-500">Loading workspace…</div>
  const builtIn = model.source === 'template' || model.source === 'blank'
  const readOnly = mode === 'replay'

  const save = async (asCopy: boolean) => {
    setError(null)
    try {
      const m = structuredClone(model)
      if (asCopy || builtIn) {
        m.model_id = localId('ws')
        m.name = `${m.name} (copy)`
        m.source = 'manual'
      }
      const saved = await api.putSpatialModel(m, false)
      useWorkspace.getState().set({ draft: null, dirty: false })
      if (saved.model_id === model.model_id) useScene.getState().set({ model: saved })
      else await applyWorkspace(saved.model_id)
      refreshList()
    } catch (e) {
      setError(errText(e))
    }
  }

  const remove = async (id: string) => {
    if (!confirm('Delete this workspace? Recorded sessions keep their data but lose its zones for metrics.')) return
    try {
      await api.deleteSpatialModel(id)
      await reloadLiveModel()
      refreshList()
    } catch (e) {
      setError(errText(e))
    }
  }

  const generateWorld = () => {
    void setWorldView(true)
  }

  const generateFromImage = async (file: File) => {
    const slug = (file.name.replace(/\.[^.]+$/, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'room').slice(0, 60)
    useScene.getState().set({ worldBuild: 'Sending the reference image to World Labs…' })
    try {
      const res = await api.generateScene(slug, file)
      useScene.getState().set({ worldBuild: res.status === 'unavailable' ? `World Labs unavailable. ${res.reason ?? ''}`.trim() : `World Labs ${res.status}` })
      if (res.status !== 'running') return
      const poll = setInterval(async () => {
        const s = await api.generationStatus(slug)
        useScene.getState().set({ worldBuild: s.reason ? s.reason : `World Labs ${s.status}` })
        if (s.status === 'running') return
        clearInterval(poll)
        const scenes = await api.scenes()
        const current = scenes.find((sc) => sc.scene_id === slug) ?? scenes[0]
        if (current) useScene.getState().set({ scenes, sceneId: current.scene_id, alignment: current.config.alignment })
      }, 5000)
    } catch (e) {
      useScene.getState().set({ worldBuild: errText(e) })
    }
  }

  const runWorldLabs = () => {
    if (model.background_url) generateWorld()
    else imageRef.current?.click()
  }

  return (
    <div className="space-y-5">
      <div>
        <input
          className="w-full bg-transparent text-base font-medium text-paper outline-none placeholder:text-mute disabled:opacity-60"
          value={model.name}
          disabled={readOnly}
          aria-label="Workspace name"
          onChange={(e) => edit((m) => void (m.name = e.target.value))}
        />
        <p className="mt-3 font-mono text-sm text-paper">
          {model.width_m.toFixed(1)} × {model.depth_m.toFixed(1)} m
        </p>
        <p className="mt-1 text-sm text-mute">
          {model.fixtures.length} objects · {model.zones.length} zones
          {model.source !== 'analyzed' ? ` · ${model.source}` : ''}
        </p>
      </div>

      {!readOnly && (
        <div className="space-y-2">
          <button
            className="btn"
            onClick={runWorldLabs}
            title={model.background_url ? 'Build a Gaussian splat from this workspace background. Needs a World Labs key.' : 'No background plate yet. Choose a reference image for World Labs.'}
          >
            Run World Labs
          </button>
          <input ref={imageRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={(e) => e.target.files?.[0] && generateFromImage(e.target.files[0])} />
          {worldBuild && <p className="text-sm text-mute">{worldBuild}</p>}
        </div>
      )}
      {error && <p className="text-sm text-rose-200">{error}</p>}

      <div className="space-y-4 text-sm">
          {model.notes.length > 0 && (
            <ul className="space-y-1 text-mute">
              {model.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          )}
          {readOnly && <p className="text-mute">Replay is read-only.</p>}
          {!readOnly && (dirty || builtIn) && (
            <div className="flex flex-wrap gap-1">
              {dirty && !builtIn && (
                <button className="btn btn-primary" onClick={() => save(false)}>
                  Save
                </button>
              )}
              {(dirty || model.source === 'template') && (
                <button className="btn" onClick={() => save(true)}>
                  Save copy
                </button>
              )}
              {dirty && (
                <button className="btn" onClick={() => useWorkspace.getState().set({ draft: null, dirty: false, zoneDraft: null })}>
                  Discard
                </button>
              )}
            </div>
          )}

          {!readOnly && <AnalyzeForm onListChanged={refreshList} />}

          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-paper">Objects</span>
              <span className="flex gap-3 text-mute">
                <label className="flex items-center gap-1">
                  <input type="checkbox" checked={showLabels} onChange={(e) => useScene.getState().set({ showLabels: e.target.checked })} /> Labels
                </label>
                <label className="flex items-center gap-1">
                  <input type="checkbox" checked={showObjects} onChange={(e) => useScene.getState().set({ showObjects: e.target.checked })} /> Video
                </label>
              </span>
            </div>
            <ObjectsEditor model={model} readOnly={readOnly} />
          </div>

          <div>
            <div className="mb-2 text-paper">Zones</div>
            <ZonesEditor model={model} readOnly={readOnly} />
          </div>

          <div className="space-y-1">
            {list.map((w) => (
              <div key={w.model_id} className={`flex items-center gap-2 py-1 ${w.model_id === model.model_id ? 'text-paper' : 'text-mute'}`}>
                <span className="min-w-0 flex-1 truncate" title={w.model_id}>
                  {w.name}
                </span>
                <span className="font-mono text-xs">
                  {w.fixtures} · {w.zones}
                </span>
                {w.active ? (
                  <span className="text-xs">Active</span>
                ) : (
                  <button className="btn px-1.5" disabled={readOnly} onClick={() => applyWorkspace(w.model_id).then(refreshList).catch((e) => setError(errText(e)))}>
                    Use
                  </button>
                )}
                {w.source !== 'template' && w.source !== 'blank' && (
                  <button className="btn px-1.5" disabled={readOnly} onClick={() => remove(w.model_id)} aria-label={`Delete ${w.name}`}>
                    Delete
                  </button>
                )}
              </div>
            ))}
          </div>
      </div>
    </div>
  )
}
