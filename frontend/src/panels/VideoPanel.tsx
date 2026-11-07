import { useCallback, useEffect, useRef } from 'react'
import { applyHomography, invert3, worldToImage } from '../lib/alignment'
import { fixtureName, PROVENANCE_COLORS } from '../lib/model'
import { frameBus } from '../lib/ws'
import type { CalibrationOverlay, CalibrationResult, Point2 } from '../lib/types'
import { entityColor } from '../scene/EntitiesLayer'
import { useTwin } from '../state/twin'
import { useCalibration, useScene, useWorkspace } from '../state/ui'
import { CalibrationPanel } from './CalibrationPanel'

interface Rect {
  x: number
  y: number
  w: number
  h: number
  scale: number
}

function fitRect(cw: number, ch: number, fw: number, fh: number): Rect {
  const scale = Math.min(cw / fw, ch / fh)
  const w = fw * scale
  const h = fh * scale
  return { x: (cw - w) / 2, y: (ch - h) / 2, w, h, scale }
}

function wSign(cal: CalibrationResult): number {
  const [u, v] = cal.points[0]?.image ?? [cal.image_width / 2, cal.image_height]
  const h = cal.homography
  return h[2][0] * u + h[2][1] * v + h[2][2] >= 0 ? 1 : -1
}

function drawPolygon(ctx: CanvasRenderingContext2D, pts: (Point2 | null)[], r: Rect, sx: number, sy: number) {
  const valid = pts.filter((p): p is Point2 => !!p)
  if (valid.length < 3) return
  ctx.beginPath()
  valid.forEach(([u, v], i) => {
    const x = r.x + u * sx
    const y = r.y + v * sy
    if (i === 0) ctx.moveTo(x, y)
    else ctx.lineTo(x, y)
  })
  ctx.closePath()
}

export function VideoPanel() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const rectRef = useRef<Rect | null>(null)
  const mode = useTwin((s) => s.mode)
  const hasFrame = useTwin((s) => !!s.lastFrame)
  const simulated = useTwin((s) => s.lastFrame?.simulated ?? false)
  const calEditing = useCalibration((s) => s.editing)
  const drawingZone = useWorkspace((s) => s.zoneDraft !== null)
  const editing = calEditing || drawingZone

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    const wrap = wrapRef.current
    if (!canvas || !wrap) return
    const dpr = window.devicePixelRatio || 1
    const cw = wrap.clientWidth
    const ch = wrap.clientHeight
    if (canvas.width !== Math.round(cw * dpr) || canvas.height !== Math.round(ch * dpr)) {
      canvas.width = Math.round(cw * dpr)
      canvas.height = Math.round(ch * dpr)
      canvas.style.width = `${cw}px`
      canvas.style.height = `${ch}px`
    }
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.fillStyle = '#09090b'
    ctx.fillRect(0, 0, cw, ch)

    const info = useTwin.getState().lastFrame
    const frame = frameBus.current
    const fw = info?.frame_width ?? frame?.bitmap.width ?? 1280
    const fh = info?.frame_height ?? frame?.bitmap.height ?? 720
    const r = fitRect(cw, ch, fw, fh)
    rectRef.current = r
    if (frame) ctx.drawImage(frame.bitmap, r.x, r.y, r.w, r.h)
    const sx = r.w / fw
    const sy = r.h / fh

    const cal = useCalibration.getState()
    const overlay: CalibrationOverlay | null = cal.preview?.overlay ?? cal.overlay
    if (overlay) {
      for (const z of overlay.zones) {
        drawPolygon(ctx, z.image_polygon, r, sx, sy)
        ctx.strokeStyle = z.color
        ctx.lineWidth = 1.5
        ctx.setLineDash([5, 4])
        ctx.stroke()
      }
      ctx.setLineDash([])
    }

    if (info) {
      for (const e of info.entities) {
        if (!e.bbox || e.state !== 'active') continue
        const x = r.x + e.bbox.x1 * sx
        const y = r.y + e.bbox.y1 * sy
        const w = (e.bbox.x2 - e.bbox.x1) * sx
        const h = (e.bbox.y2 - e.bbox.y1) * sy
        const color = entityColor(e)
        const selected = useTwin.getState().selectedId === e.entity_id
        ctx.strokeStyle = selected ? '#ffffff' : color
        ctx.lineWidth = selected ? 3 : 2
        ctx.strokeRect(x, y, w, h)
        const text = e.label ?? e.entity_id
        ctx.font = '11px ui-monospace, monospace'
        const tw = ctx.measureText(text).width + 8
        ctx.fillStyle = selected ? '#ffffff' : color
        ctx.fillRect(x, y - 15, tw, 15)
        ctx.fillStyle = '#000'
        ctx.fillText(text, x + 4, y - 4)
        // ground-contact reference point used for projection
        ctx.fillStyle = '#facc15'
        ctx.beginPath()
        ctx.arc(x + w / 2, y + h, 3, 0, Math.PI * 2)
        ctx.fill()
      }
    }

    const activeCal = cal.preview?.calibration ?? cal.active
    const { model: saved, showObjects } = useScene.getState()
    const ws = useWorkspace.getState()
    const model = ws.draft && saved && ws.draft.model_id === saved.model_id ? ws.draft : saved
    // image_bbox is in the analyzing calibration's pixel space; only meaningful for that same camera view.
    if (showObjects && model && activeCal && model.calibration_id === activeCal.calibration_id) {
      const ku = sx * (fw / activeCal.image_width)
      const kv = sy * (fh / activeCal.image_height)
      ctx.font = '10px ui-monospace, monospace'
      for (const f of model.fixtures) {
        if (!f.image_bbox) continue
        const [x1, y1, x2, y2] = f.image_bbox
        const x = r.x + x1 * ku
        const y = r.y + y1 * kv
        ctx.strokeStyle = PROVENANCE_COLORS[f.provenance] ?? '#a1a1aa'
        ctx.lineWidth = 1
        ctx.setLineDash([3, 3])
        ctx.strokeRect(x, y, (x2 - x1) * ku, (y2 - y1) * kv)
        ctx.setLineDash([])
        const text = fixtureName(f)
        ctx.fillStyle = 'rgba(0,0,0,0.65)'
        ctx.fillRect(x, y + 1, ctx.measureText(text).width + 6, 13)
        ctx.fillStyle = '#fde68a'
        ctx.fillText(text, x + 3, y + 11)
      }
    }

    if (ws.zoneDraft && activeCal) {
      const inv = invert3(activeCal.homography)
      const ku = sx * (fw / activeCal.image_width)
      const kv = sy * (fh / activeCal.image_height)
      const px = inv ? ws.zoneDraft.map(([x, y]) => worldToImage(inv, x, y)) : []
      ctx.strokeStyle = '#facc15'
      ctx.fillStyle = 'rgba(250,204,21,0.18)'
      ctx.lineWidth = 2
      ctx.beginPath()
      px.forEach((p, i) => {
        if (!p) return
        const X = r.x + p[0] * ku
        const Y = r.y + p[1] * kv
        if (i === 0) ctx.moveTo(X, Y)
        else ctx.lineTo(X, Y)
      })
      if (px.length >= 3) {
        ctx.closePath()
        ctx.fill()
      }
      ctx.stroke()
      ctx.fillStyle = '#facc15'
      for (const p of px) {
        if (!p) continue
        ctx.beginPath()
        ctx.arc(r.x + p[0] * ku, r.y + p[1] * kv, 4, 0, Math.PI * 2)
        ctx.fill()
      }
    }

    if (cal.editing) {
      cal.points.forEach((p, i) => {
        const x = r.x + p.image[0] * sx
        const y = r.y + p.image[1] * sy
        ctx.strokeStyle = '#facc15'
        ctx.lineWidth = 2
        ctx.beginPath()
        ctx.moveTo(x - 8, y)
        ctx.lineTo(x + 8, y)
        ctx.moveTo(x, y - 8)
        ctx.lineTo(x, y + 8)
        ctx.stroke()
        ctx.fillStyle = '#facc15'
        ctx.font = 'bold 12px ui-monospace, monospace'
        ctx.fillText(String(i + 1), x + 6, y - 6)
      })
    }
  }, [])

  useEffect(() => {
    const unsubFrame = frameBus.subscribe(() => requestAnimationFrame(draw))
    const unsubTwin = useTwin.subscribe((s, p) => {
      if (s.lastFrame !== p.lastFrame || s.selectedId !== p.selectedId) requestAnimationFrame(draw)
    })
    const unsubCal = useCalibration.subscribe(() => requestAnimationFrame(draw))
    const unsubScene = useScene.subscribe((s, p) => {
      if (s.model !== p.model || s.showObjects !== p.showObjects) requestAnimationFrame(draw)
    })
    const unsubWs = useWorkspace.subscribe(() => requestAnimationFrame(draw))
    const ro = new ResizeObserver(() => draw())
    if (wrapRef.current) ro.observe(wrapRef.current)
    draw()
    return () => {
      unsubFrame()
      unsubTwin()
      unsubCal()
      unsubScene()
      unsubWs()
      ro.disconnect()
    }
  }, [draw])

  const toImage = (ev: React.MouseEvent): Point2 | null => {
    const r = rectRef.current
    const info = useTwin.getState().lastFrame
    if (!r || !info) return null
    const bounds = (ev.target as HTMLElement).getBoundingClientRect()
    const u = ((ev.clientX - bounds.left - r.x) / r.w) * info.frame_width
    const v = ((ev.clientY - bounds.top - r.y) / r.h) * info.frame_height
    if (u < 0 || v < 0 || u > info.frame_width || v > info.frame_height) return null
    return [Math.round(u * 10) / 10, Math.round(v * 10) / 10]
  }

  const toWorld = (ev: React.MouseEvent): Point2 | null => {
    const cal = useCalibration.getState()
    const c = cal.preview?.calibration ?? cal.active
    const p = toImage(ev)
    if (!c || !p) return null
    const info = useTwin.getState().lastFrame
    const su = c.image_width / (info?.frame_width ?? c.image_width)
    const sv = c.image_height / (info?.frame_height ?? c.image_height)
    return applyHomography(c.homography, p[0] * su, p[1] * sv, wSign(c))
  }

  const onMove = (ev: React.MouseEvent) => {
    const cal = useCalibration.getState()
    const w = toWorld(ev)
    if (w || cal.hoverWorld) cal.set({ hoverWorld: w })
  }

  const onClick = (ev: React.MouseEvent) => {
    const ws = useWorkspace.getState()
    if (ws.zoneDraft) {
      const w = toWorld(ev)
      if (w) ws.set({ zoneDraft: [...ws.zoneDraft, [Math.round(w[0] * 100) / 100, Math.round(w[1] * 100) / 100]] })
      return
    }
    const cal = useCalibration.getState()
    if (!cal.editing) return
    const p = toImage(ev)
    if (p) cal.set({ points: [...cal.points, { image: p, world: null, referenceId: null }], preview: null })
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-4 py-2.5">
        <span className="text-xs font-medium">Video</span>
        <span className="flex gap-1.5">
          {simulated && <span className="chip text-sky-200">Simulated</span>}
          {calEditing && <span className="chip text-amber-200">Click floor points</span>}
          {drawingZone && <span className="chip text-amber-200">Click zone corners</span>}
        </span>
      </div>
      <div ref={wrapRef} className="relative min-h-0 flex-1">
        <canvas
          ref={canvasRef}
          className={`absolute inset-0 ${editing ? 'cursor-crosshair' : 'cursor-default'}`}
          onMouseMove={onMove}
          onMouseLeave={() => useCalibration.getState().set({ hoverWorld: null })}
          onClick={onClick}
        />
        {!hasFrame && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center px-8 text-center text-sm leading-relaxed text-mute">
            {mode === 'replay' ? 'Replay has no stored frames.' : 'Waiting for the first frame.'}
          </div>
        )}
      </div>
      <CalibrationPanel />
    </div>
  )
}
