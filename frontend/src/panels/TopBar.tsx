import { useEffect, useRef, useState } from 'react'
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
    <div className="flex gap-3 font-mono text-[11px] text-zinc-400" title="Measured by the running pipeline (EMA). e2e = browser receipt minus server capture time.">
      <span>cap {p.capture_fps.toFixed(1)} fps</span>
      <span>proc {p.process_fps.toFixed(1)} fps</span>
      <span>infer {p.inference_ms.toFixed(0)} ms</span>
      <span>track {p.tracking_ms.toFixed(0)} ms</span>
      <span>e2e {f.e2e_ms.toFixed(0)} ms</span>
      <span>drop {p.dropped_frames}</span>
    </div>
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
      await api.stopSession()
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

  const stateColor = status.state === 'running' ? 'bg-emerald-500' : status.state === 'error' ? 'bg-red-500' : 'bg-zinc-500'

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-zinc-800 bg-zinc-900/80 px-3 py-2 text-xs">
      <div className="flex items-center gap-2 pr-2">
        <span className="text-sm font-bold tracking-[0.25em]">ECHO</span>
        <span className={`h-2 w-2 rounded-full ${connected ? 'bg-emerald-400' : 'bg-red-500'}`} title={connected ? 'connected' : 'backend disconnected'} />
      </div>

      <label className="flex items-center gap-1">
        Source
        <select className="input" value={running && status.source_kind ? status.source_kind : source} disabled={running} onChange={(e) => setSource(e.target.value as SourceKind)}>
          <option value="simulation">Simulation (labeled)</option>
          <option value="webcam">Webcam</option>
          <option value="file">Video file</option>
        </select>
      </label>
      {source === 'webcam' && (
        <label className="flex items-center gap-1">
          index
          <input className="input w-12" type="number" min={0} value={cameraIndex} disabled={running} onChange={(e) => setCameraIndex(parseInt(e.target.value) || 0)} />
        </label>
      )}
      {source === 'file' && (
        <>
          <select className="input max-w-48" value={videoId} disabled={running} onChange={(e) => setVideoId(e.target.value)}>
            {videos.length === 0 && <option value="">no uploads</option>}
            {videos.map((v) => (
              <option key={v.video_id} value={v.video_id}>
                {v.name} ({v.width}×{v.height})
              </option>
            ))}
          </select>
          <AddVideosButton
            disabled={running}
            onAdded={(added) => {
              setVideoId(added[0].video_id)
              setSource('file')
            }}
            onError={setError}
          />
        </>
      )}
      {source !== 'simulation' && (
        <>
          <label className="flex items-center gap-1">
            Detector
            <select className="input" value={detector} disabled={running} onChange={(e) => setDetector(e.target.value as 'yolo' | 'none')}>
              <option value="yolo">YOLO + ByteTrack</option>
              <option value="none">ArUco only</option>
            </select>
          </label>
          <label className="flex items-center gap-1">
            Calibration
            <select className="input max-w-44" value={calibrationId} disabled={running} onChange={(e) => setCalibrationId(e.target.value)}>
              <option value="">none (calibrate live)</option>
              {calibrations.map((c) => (
                <option key={c.calibration_id} value={c.calibration_id}>
                  {c.label ?? c.calibration_id} · {c.image_width}×{c.image_height}
                </option>
              ))}
            </select>
          </label>
        </>
      )}
      <label className="flex items-center gap-1">
        <input type="checkbox" checked={aruco} disabled={running} onChange={(e) => setAruco(e.target.checked)} /> ArUco
      </label>
      <label className="flex items-center gap-1">
        fps
        <input className="input w-12" type="number" min={1} max={30} value={fps} disabled={running} onChange={(e) => setFps(Math.max(1, parseFloat(e.target.value) || 10))} />
      </label>
      {!running ? (
        <button className="btn btn-primary" disabled={busy || (source === 'file' && !videoId)} onClick={start}>
          ▶ Start
        </button>
      ) : (
        <button className="btn btn-danger" disabled={busy || status.state === 'stopping'} onClick={stop}>
          ■ Stop
        </button>
      )}
      <span className="flex items-center gap-1.5">
        <span className={`h-2 w-2 rounded-full ${stateColor}`} />
        {status.state}
        {status.simulated && <span className="rounded bg-sky-900 px-1 text-sky-200">SIMULATED</span>}
        {status.state === 'running' && !status.calibrated && <span className="rounded bg-amber-700 px-1 text-amber-100">uncalibrated</span>}
      </span>
      <Perf />

      <div className="ml-auto flex items-center gap-2">
        <label className="flex items-center gap-1">
          Scene
          <select className="input max-w-52" value={sceneId} onChange={(e) => useScene.getState().set({ sceneId: e.target.value })}>
            {scenes.map((s) => (
              <option key={s.scene_id} value={s.scene_id}>
                {s.display_name}
                {s.kind === 'image-blaster' && s.synthetic ? ' (synthetic)' : ''}
              </option>
            ))}
          </select>
        </label>
        <button className="btn" title="Upload a reference image and run Image-Blaster world generation once" onClick={() => refImageRef.current?.click()}>
          Reference image…
        </button>
        <input ref={refImageRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={(e) => e.target.files?.[0] && generate(e.target.files[0])} />
      </div>
      {(error || status.message || genStatus) && (
        <div className="w-full text-[11px]">
          {error && <span className="mr-3 text-red-400">{error}</span>}
          {status.state === 'error' && status.message && <span className="mr-3 text-red-400">{status.message}</span>}
          {genStatus && <span className="text-zinc-400">{genStatus}</span>}
        </div>
      )}
    </div>
  )
}
