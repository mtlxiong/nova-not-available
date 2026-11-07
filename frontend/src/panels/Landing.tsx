import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from '../lib/api'
import type { SourceKind } from '../lib/types'
import { useVideos } from '../state/videos'
import { useTwin } from '../state/twin'
import { useCalibration, useScene } from '../state/ui'

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

function Mark({ children }: { children: ReactNode }) {
  return (
    <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden>
      {children}
    </svg>
  )
}

function RoomScan() {
  const shell = 'M 390 390 L 637 532.6 L 453.7 638.4 L 206.7 495.8 Z M 390 247.4 L 637 390 L 453.7 495.8 L 206.7 353.2 Z M 390 390 L 390 247.4 M 637 532.6 L 637 390 M 453.7 638.4 L 453.7 495.8 M 206.7 495.8 L 206.7 353.2'
  const sofa = 'M 497.6 472.8 L 605.1 534.9 L 551.3 565.9 L 443.8 503.9 Z M 497.6 439.7 L 605.1 501.8 L 551.3 532.8 L 443.8 470.7 Z M 497.6 472.8 L 497.6 439.7 M 605.1 534.9 L 605.1 501.8 M 551.3 565.9 L 551.3 532.8 M 443.8 503.9 L 443.8 470.7'
  const back = 'M 497.6 439.7 L 605.1 501.8 L 594 508.2 L 486.4 446.1 Z M 497.6 414.4 L 605.1 476.5 L 594 482.9 L 486.4 420.8 Z M 497.6 439.7 L 497.6 414.4 M 605.1 501.8 L 605.1 476.5 M 594 508.2 L 594 482.9 M 486.4 446.1 L 486.4 420.8'
  const table = 'M 398 479.7 L 465.7 518.8 L 423.9 543 L 356.1 503.9 Z M 398 460.4 L 465.7 499.5 L 423.9 523.6 L 356.1 484.5 Z M 398 479.7 L 398 460.4 M 465.7 518.8 L 465.7 499.5 M 423.9 543 L 423.9 523.6 M 356.1 503.9 L 356.1 484.5'
  const window = 'M 417.9 353.2 L 479.6 388.9 L 477.2 390.2 L 415.5 354.6 Z M 417.9 291.1 L 479.6 326.8 L 477.2 328.1 L 415.5 292.5 Z M 417.9 353.2 L 417.9 291.1 M 479.6 388.9 L 479.6 326.8 M 448.5 322 L 446.4 384.5'
  const pot = 'M 388 407.2 L 409.9 419.9 L 388 432.6 L 366.1 419.9 Z M 388 386.6 L 409.9 399.2 L 388 411.9 L 366.1 399.2 Z M 388 407.2 L 388 386.6 M 409.9 419.9 L 409.9 399.2 M 366.1 419.9 L 366.1 399.2'
  const lamp = 'M 469.7 553.3 L 469.7 482 M 443.4 465.4 L 496 465.4 L 496 484.8 L 443.4 484.8 Z M 443.4 465.4 L 469.7 450.3 L 496 465.4'
  return (
    <svg viewBox="150 210 540 460" className="h-auto w-full max-w-[28rem]" role="img" aria-label="A wireframe of a room">
      <ellipse cx="420" cy="620" rx="210" ry="28" fill="#7f9ed4" opacity="0.18" />
      <g fill="none" stroke="#c5d4ef" strokeLinejoin="round" strokeLinecap="round">
        <path d={shell} strokeWidth="1.25" opacity="0.85" />
        <path d={window} strokeWidth="1.15" />
        <path d={sofa} strokeWidth="1.2" />
        <path d={back} strokeWidth="1.2" />
        <path d={table} strokeWidth="1.15" />
        <path d={pot} strokeWidth="1.15" />
        <path d={lamp} strokeWidth="1.15" />
        <path d="M 388 386 C 376 360 368 352 362 334 C 374 346 384 358 388 386 M 388 386 C 400 358 412 348 420 332 C 408 348 398 362 388 386 M 388 386 C 386 356 378 340 372 324" strokeWidth="1.15" />
      </g>
      {[[390, 390], [637, 532.6], [453.7, 638.4], [206.7, 495.8], [390, 247.4], [637, 390], [453.7, 495.8], [206.7, 353.2]].map(([cx, cy]) => (
        <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="2.2" fill="#d5e2f8" />
      ))}
    </svg>
  )
}

export function Landing({ onOpen, leaving = false }: { onOpen: () => void; leaving?: boolean }) {
  const videos = useVideos((s) => s.videos)
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    useVideos.getState().refresh().catch(() => undefined)
  }, [])

  const begin = async (source: SourceKind, fileId?: string) => {
    setError(null)
    setBusy(true)
    try {
      const live = useTwin.getState().status
      if (live.state === 'running' || live.state === 'stopping') await api.stopSession()
      if (useTwin.getState().mode !== 'live') useTwin.getState().setMode('live')
      await api.startSession({
        source,
        camera_index: 0,
        file_id: source === 'file' ? fileId ?? null : null,
        detector: 'yolo',
        enable_aruco: true,
        inference_fps: 10,
        realtime: true,
        scene_id: useScene.getState().sceneId,
        calibration_id: null,
      })
      const cur = await api.currentSession()
      useCalibration.getState().set({ active: cur.calibration, overlay: cur.overlay })
      onOpen()
    } catch (e) {
      setError(errText(e))
    } finally {
      setBusy(false)
      setStatus(null)
    }
  }

  const openFile = async (file: File) => {
    setBusy(true)
    setError(null)
    setStatus(file.name)
    try {
      const video = await api.uploadVideo(file)
      useVideos.getState().prepend([video])
      await begin('file', video.video_id)
    } catch (e) {
      setError(errText(e))
      setBusy(false)
      setStatus(null)
    }
  }

  return (
    <main
      className={`landing flex min-h-0 flex-1 text-paper ${over ? 'is-over' : ''} ${leaving ? 'is-leaving' : ''}`}
      onDragOver={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        const file = e.dataTransfer.files?.[0]
        if (file) openFile(file)
      }}
    >
      <div className={`landing-stage flex min-h-0 min-w-0 flex-1 flex-col ${leaving ? 'is-leaving' : ''}`}>
        <div className="flex items-center gap-2.5 px-8 pt-8 sm:px-14">
          <Mark>
            <path d="M8 1.5 14 5v6L8 14.5 2 11V5L8 1.5Z" />
            <path d="M8 8 14 5M8 8 2 5M8 8v6.5" />
          </Mark>
          <span className="text-sm font-medium tracking-[0.08em]">ECHO</span>
        </div>
        <div className="grid min-h-0 flex-1 items-center gap-6 px-8 pb-10 sm:px-14 lg:grid-cols-[minmax(0,1fr)_minmax(280px,0.9fr)] lg:gap-4">
          <section className="max-w-xl">
            <h1 className="text-5xl font-medium leading-[1.02] tracking-[-0.035em] sm:text-6xl lg:text-7xl">
              Drop a video
              <br />
              of the <span className="text-[#9eb6e8]">room.</span>
            </h1>
            <p className="mt-6 max-w-[34ch] text-lg leading-relaxed text-mute">Turn a video, webcam, or simulation into a spatial twin of the space it sees.</p>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <button type="button" className="btn btn-primary" disabled={busy} onClick={() => inputRef.current?.click()}>
                <Mark>
                  <path d="M8 11V3M5 5.5 8 2.5 11 5.5" />
                  <path d="M3 9.5V13h10V9.5" />
                </Mark>
                {busy && status ? status : 'Choose a file'}
              </button>
              <button className="btn" disabled={busy} onClick={() => begin('webcam')}>
                <Mark>
                  <path d="M2.5 5.5h7.2l2.3-1.6v8.2l-2.3-1.6H2.5v-5Z" />
                </Mark>
                Webcam
              </button>
              <button className="btn" disabled={busy} onClick={() => begin('simulation')}>
                <Mark>
                  <path d="M2.5 5.5 8 2.5l5.5 3L8 8.5 2.5 5.5Z" />
                  <path d="M2.5 8.2 8 11.2l5.5-3M2.5 10.8 8 13.8l5.5-3" />
                </Mark>
                Simulation
              </button>
            </div>
            <p className="mt-5 text-sm text-mute">{over ? 'Release to open' : 'Or drop a video anywhere on this page.'}</p>
            {videos.length > 0 && (
              <label className="mt-5 block max-w-xs text-sm text-mute">
                <span className="sr-only">A video already here</span>
                <select
                  className="input w-full"
                  defaultValue=""
                  disabled={busy}
                  aria-label="A video already here"
                  onChange={(e) => {
                    const id = e.target.value
                    e.target.value = ''
                    if (id) begin('file', id)
                  }}
                >
                  <option value="">A video already here</option>
                  {videos.map((v) => (
                    <option key={v.video_id} value={v.video_id}>
                      {v.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {error && (
              <p className="mt-4 max-w-md text-sm text-rose-200" role="alert">
                {error}
              </p>
            )}
          </section>
          <div className="flex items-center justify-center px-4 py-6">
            <RoomScan />
          </div>
        </div>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="video/*,.avi,.mp4,.mov,.mkv,.webm"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0]
          e.target.value = ''
          if (file) openFile(file)
        }}
      />
    </main>
  )
}
