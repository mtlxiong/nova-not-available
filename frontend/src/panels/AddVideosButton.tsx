import { useRef, useState } from 'react'
import { api, type VideoInfo } from '../lib/api'
import { useVideos } from '../state/videos'

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** Uploads one or more video files into the shared library. */
export function AddVideosButton({
  disabled,
  onAdded,
  onError,
}: {
  disabled?: boolean
  onAdded?: (videos: VideoInfo[]) => void
  onError?: (message: string | null) => void
}) {
  const ref = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const upload = async (list: FileList | null) => {
    const files = [...(list ?? [])]
    if (ref.current) ref.current.value = ''
    if (!files.length) return
    setBusy(true)
    setError(null)
    onError?.(null)
    const added: VideoInfo[] = []
    const errors: string[] = []
    for (const [i, file] of files.entries()) {
      setProgress(`${i + 1}/${files.length} ${file.name}`)
      try {
        added.push(await api.uploadVideo(file))
      } catch (e) {
        errors.push(`${file.name}: ${errText(e)}`)
      }
    }
    if (added.length) {
      useVideos.getState().prepend(added)
      onAdded?.(added)
    }
    const message = errors.length ? errors.join(' ') : null
    setError(message)
    onError?.(message)
    setProgress(null)
    setBusy(false)
  }

  return (
    <span className="inline-flex min-w-0 flex-col">
      <button className="btn" disabled={disabled || busy} onClick={() => ref.current?.click()}>
        {busy ? `Uploading ${progress ?? ''}`.trim() : 'Add videos…'}
      </button>
      <input ref={ref} type="file" accept="video/*,.avi,.mp4,.mov,.mkv,.webm" multiple className="hidden" onChange={(e) => upload(e.target.files)} />
      {error && <span className="mt-0.5 max-w-64 truncate text-[10px] text-red-400">{error}</span>}
    </span>
  )
}
