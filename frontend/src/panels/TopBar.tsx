import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from '../lib/api'
import { useVideos } from '../state/videos'
import { AddVideosButton } from './AddVideosButton'
import type { SessionConfig, SourceKind } from '../lib/types'
import { useTwin } from '../state/twin'
import { useCalibration, useScene } from '../state/ui'
import { loadScenes } from '../App'

function Perf() {
  const f = useTwin((s) => s.lastFrame)
  if (!f?.perf) return null
  const p = f.perf
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[10px] text-mute" title="Measured by the running pipeline (EMA). e2e = browser receipt minus server capture time.">
      <span>cap {p.capture_fps.toFixed(1)}</span>
      <span>proc {p.process_fps.toFixed(1)}</span>
      <span>infer {p.inference_ms.toFixed(0)} ms</span>
      <span>track {p.tracking_ms.toFixed(0)} ms</span>
      <span>e2e {f.e2e_ms.toFixed(0)} ms</span>
      <span>drop {p.dropped_frames}</span>
    </div>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1">
      <span className="kicker">{label}</span>
      {children}
    </label>
  )
}

export function TopBar({ connected }: { connected: boolean }) {
  const status = useTwin((s) => s.status)
  const mode = useTwin((s) => s.mode)
  const scenes = useScene((s) => s.scenes)
  const sceneId = useScene((s) => s.sceneId)
  const [source, setSource] = useState<SourceKind>('simulation')
  const [cameraIndex, setCameraIndex] = useState(0)
  const videos = useVideos((s) => s.videos)
  const [videoId, setVideoId] = useState<string>('')
  const [detector, setDetector] = useState<'yolo' | 'none'>('yolo')
  const [aruco, setAruco] = useState(true)
  const [fps, setFps] = useState(10)
  const [calibrationId, setCalibrationId] = useState<string>('')
  const [calibrations, setCalibrations] = useState<{ calibration_id: string; label: string | null; image_width: number; image_height: number }[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [genStatus, setGenStatus] = useState<string | null>(null)
  const refImageRef = useRef<HTMLInputElement>(null)
  const running = status.state === 'running' || status.state === 'stopping'

  useEffect(() => {
    useVideos.getState().refresh().then(() => {
      const list = useVideos.getState().videos
      if (list[0]) setVideoId((cur) => cur || list[0].video_id)
    }).catch(() => undefined)
    api.calibrations().then(setCalibrations).catch(() => undefined)
  }, [status.state])

  const start = async () => {
    setBusy(true)
    setError(null)
    const config: SessionConfig = {
      source,
      camera_index: cameraIndex,
      file_id: source === 'file' ? videoId : null,
      detector,
      enable_aruco: aruco,
      inference_fps: fps,
      realtime: true,
      scene_id: sceneId,
      calibration_id: source === 'simulation' ? null : calibrationId || null,
    }
    try {
      if (mode !== 'live') useTwin.getState().setMode('live')
      await api.startSession(config)
      const cur = await api.currentSession()
      useCalibration.getState().set({ active: cur.calibration, overlay: cur.overlay })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const stop = async () => {
    setBusy(true)
    try {
      useTwin.getState().setStatus(await api.stopSession())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const generate = async (file: File) => {
    const slug = (file.name.replace(/\.[^.]+$/, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'kitchen').slice(0, 60)
    setGenStatus('staging reference image…')
    try {
      const res = await api.generateScene(slug, file)
      setGenStatus(res.status === 'unavailable' ? `Image-Blaster unavailable: ${res.reason}` : `Image-Blaster ${res.status} (${slug})`)
      if (res.status === 'running') {
        const poll = setInterval(async () => {
          const s = await api.generationStatus(slug)
          setGenStatus(`Image-Blaster ${s.status} (${slug})${s.reason ? `: ${s.reason.slice(0, 120)}` : ''}`)
          if (s.status !== 'running') {
            clearInterval(poll)
            await loadScenes()
            if (s.status === 'completed') useScene.getState().set({ sceneId: slug })
          }
        }, 5000)
      }
    } catch (e) {
      setGenStatus(e instanceof Error ? e.message : String(e))
    }
  }

  const stateDot = status.state === 'running' ? 'bg-emerald-400' : status.state === 'error' ? 'bg-rose-400' : 'bg-white/30'

  return (
    <header className="shrink-0 border-b border-white/[0.06] bg-panel/80 px-3 py-2.5 backdrop-blur-md">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2.5">
          <span className="text-[13px] font-semibold tracking-[0.28em]">ECHO</span>
          <span className={`h-1.5 w-1.5 rounded-full ${connected ? 'bg-emerald-400' : 'bg-rose-400'}`} title={connected ? 'connected' : 'backend disconnected'} />
        </div>
        <span className="chip">
          <span className={`h-1.5 w-1.5 rounded-full ${stateDot}`} />
          {status.state}
        </span>
        {status.simulated && <span className="chip text-sky-200">Simulated</span>}
        {status.state === 'running' && !status.calibrated && <span className="chip text-amber-200">Uncalibrated</span>}
        <div className="ml-auto flex items-end gap-2">
          <Field label="Scene">
            <select className="input max-w-52" value={sceneId} onChange={(e) => useScene.getState().set({ sceneId: e.target.value })}>
              {scenes.map((s) => (
                <option key={s.scene_id} value={s.scene_id}>
                  {s.display_name}
                  {s.kind === 'image-blaster' && s.synthetic ? ' (synthetic)' : ''}
                </option>
              ))}
            </select>
          </Field>
          <button className="btn mb-px" title="Optional. Upload a reference image and run World Labs Marble once. Needs WORLD_LABS_API_KEY." onClick={() => refImageRef.current?.click()}>
            World Labs
          </button>
          <input ref={refImageRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={(e) => e.target.files?.[0] && generate(e.target.files[0])} />
        </div>
      </div>

      <div className="mt-2.5 flex flex-wrap items-end gap-2">
        <Field label="Source">
          <select className="input" value={running && status.source_kind ? status.source_kind : source} disabled={running} onChange={(e) => setSource(e.target.value as SourceKind)}>
            <option value="simulation">Simulation</option>
            <option value="webcam">Webcam</option>
            <option value="file">Video file</option>
          </select>
        </Field>
        {source === 'webcam' && (
          <Field label="Index">
            <input className="input w-16" type="number" min={0} value={cameraIndex} disabled={running} onChange={(e) => setCameraIndex(parseInt(e.target.value) || 0)} />
          </Field>
        )}
        {source === 'file' && (
          <>
            <Field label="Video">
              <select className="input max-w-56" value={videoId} disabled={running} onChange={(e) => setVideoId(e.target.value)}>
                {videos.length === 0 && <option value="">No uploads</option>}
                {videos.map((v) => (
                  <option key={v.video_id} value={v.video_id}>
                    {v.name} ({v.width}×{v.height})
                  </option>
                ))}
              </select>
            </Field>
<div className="flex flex-col gap-1">
              <span className="kicker">Library</span>
              <AddVideosButton
                disabled={running}
                onAdded={(added) => {
                  setVideoId(added[0].video_id)
                  setSource('file')
                }}
                onError={setError}
              />
            </div>
          </>
        )}
        {source !== 'simulation' && (
          <>
            <Field label="Detector">
              <select className="input" value={detector} disabled={running} onChange={(e) => setDetector(e.target.value as 'yolo' | 'none')}>
                <option value="yolo">YOLO + ByteTrack</option>
                <option value="none">ArUco only</option>
              </select>
            </Field>
            <Field label="Calibration">
              <select className="input max-w-48" value={calibrationId} disabled={running} onChange={(e) => setCalibrationId(e.target.value)}>
                <option value="">None — calibrate live</option>
                {calibrations.map((c) => (
                  <option key={c.calibration_id} value={c.calibration_id}>
                    {c.label ?? c.calibration_id} · {c.image_width}×{c.image_height}
                  </option>
                ))}
              </select>
            </Field>
          </>
        )}
        <label className="mb-1 flex h-7 items-center gap-1.5 text-[11px] text-mute">
          <input type="checkbox" checked={aruco} disabled={running} onChange={(e) => setAruco(e.target.checked)} />
          ArUco
        </label>
        <Field label="fps">
          <input className="input w-16" type="number" min={1} max={30} value={fps} disabled={running} onChange={(e) => setFps(Math.max(1, parseFloat(e.target.value) || 10))} />
        </Field>
        {!running ? (
          <button className="btn btn-primary mb-px px-3" disabled={busy || (source === 'file' && !videoId)} onClick={start}>
            Start
          </button>
        ) : (
          <button className="btn btn-danger mb-px px-3" disabled={busy} onClick={stop}>
            Stop
          </button>
        )}
        <div className="mb-1 ml-auto">
          <Perf />
        </div>
      </div>
      {(error || status.message || genStatus) && (
        <div className="mt-2 text-[11px]">
          {error && <span className="mr-3 text-rose-300">{error}</span>}
          {status.state === 'error' && status.message && <span className="mr-3 text-rose-300">{status.message}</span>}
          {genStatus && <span className="text-mute">{genStatus}</span>}
        </div>
      )}
    </header>
  )
}
