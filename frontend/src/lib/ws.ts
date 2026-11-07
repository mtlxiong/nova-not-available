import { wsUrl } from './backend'
import type { ServerMessage } from './types'

export interface VideoFrame {
  bitmap: ImageBitmap
  frameIndex: number
  timestampMs: number
}

type FrameListener = (frame: VideoFrame) => void

/** Latest decoded video frame, kept outside React state so 15 fps video doesn't re-render the tree. */
export const frameBus = {
  current: null as VideoFrame | null,
  listeners: new Set<FrameListener>(),
  publish(frame: VideoFrame) {
    this.current?.bitmap.close()
    this.current = frame
    this.listeners.forEach((l) => l(frame))
  },
  subscribe(l: FrameListener) {
    this.listeners.add(l)
    return () => this.listeners.delete(l)
  },
}

export function parseFrameMessage(buf: ArrayBuffer): { frameIndex: number; timestampMs: number; jpeg: Blob } {
  const view = new DataView(buf)
  return {
    frameIndex: view.getUint32(0, true),
    timestampMs: view.getUint32(4, true),
    jpeg: new Blob([buf.slice(8)], { type: 'image/jpeg' }),
  }
}

export interface Connection {
  close: () => void
  setFrames: (enabled: boolean) => void
}

export function connect(onMessage: (msg: ServerMessage) => void, onState: (connected: boolean) => void): Connection {
  let ws: WebSocket | null = null
  let closed = false
  let retry = 0
  let wantFrames = true
  let decoding = false

  const open = () => {
    ws = new WebSocket(wsUrl())
    ws.binaryType = 'arraybuffer'
    ws.onopen = () => {
      retry = 0
      onState(true)
      ws?.send(JSON.stringify({ type: 'client.options', frames: wantFrames }))
    }
    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        onMessage(JSON.parse(ev.data) as ServerMessage)
        return
      }
      // Skip frames while the previous one is still decoding: stay current rather than queue.
      if (decoding) return
      decoding = true
      const { frameIndex, timestampMs, jpeg } = parseFrameMessage(ev.data as ArrayBuffer)
      createImageBitmap(jpeg)
        .then((bitmap) => frameBus.publish({ bitmap, frameIndex, timestampMs }))
        .catch(() => undefined)
        .finally(() => {
          decoding = false
        })
    }
    ws.onclose = () => {
      onState(false)
      if (closed) return
      const delay = Math.min(5000, 250 * 2 ** retry++)
      setTimeout(open, delay)
    }
  }
  open()

  return {
    close: () => {
      closed = true
      ws?.close()
    },
    setFrames: (enabled) => {
      wantFrames = enabled
      if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'client.options', frames: enabled }))
    },
  }
}
