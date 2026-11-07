import { useEffect, useState } from 'react'
import { api, type CalibrationListItem } from '../lib/api'
import type { CalibrationPoint } from '../lib/types'
import { useTwin } from '../state/twin'
import { useCalibration, useScene } from '../state/ui'

export async function refreshActiveCalibration() {
  try {
    const cur = await api.currentSession()
    useCalibration.getState().set({ active: cur.calibration, overlay: cur.overlay })
  } catch {
    /* backend unavailable */
  }
}

export function CalibrationPanel() {
  const cal = useCalibration()
  const model = useScene((s) => s.model)
  const lastFrame = useTwin((s) => s.lastFrame)
  const running = useTwin((s) => s.status.state === 'running')
  const [saved, setSaved] = useState<CalibrationListItem[]>([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (cal.editing) api.calibrations().then(setSaved).catch(() => undefined)
  }, [cal.editing])

  const complete = cal.points.filter((p) => p.world) as { image: [number, number]; world: [number, number] }[]
  const body = () => ({
    camera_id: 'ui-camera',
    image_width: lastFrame?.frame_width ?? 0,
    image_height: lastFrame?.frame_height ?? 0,
    points: complete.map<CalibrationPoint>((p) => ({ image: p.image, world: p.world })),
    label: 'manual (UI)',
  })

  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    cal.set({ error: null })
    try {
      await fn()
    } catch (e) {
      cal.set({ error: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const updatePoint = (i: number, patch: Partial<(typeof cal.points)[number]>) => {
    const points = cal.points.map((p, j) => (j === i ? { ...p, ...patch } : p))
    cal.set({ points, preview: null })
  }

  const active = cal.preview?.calibration ?? cal.active
  return (
    <div className="max-h-[45%] overflow-y-auto border-t border-white/[0.06] px-3 py-2.5 text-xs">
      <div className="flex items-center justify-between">
        <span className="kicker">Calibration</span>
        <div className="flex gap-1">
          {!cal.editing ? (
            <button className="btn" disabled={!lastFrame} onClick={() => cal.set({ editing: true, points: [], preview: null, error: null })}>
              Calibrate
            </button>
          ) : (
            <button className="btn" onClick={() => cal.set({ editing: false, points: [], preview: null, hoverWorld: null })}>
              Done
            </button>
          )}
        </div>
      </div>
      <div className="mt-1 text-zinc-400">
        {active ? (
          <>
            {active.method === 'auto-person-height' ? 'est. error' : 'RMSE'} <span className="font-mono text-zinc-200">{active.reprojection_rmse_m.toFixed(3)} m</span>
            {active.simulated && <span className="ml-1 rounded bg-sky-900 px-1 text-sky-200">simulated</span>}
            {active.method === 'auto-person-height' && (
              <span className="ml-1 rounded bg-amber-800 px-1 text-amber-100" title="Estimated from people's heights, not measured floor points. Scale is approximate; calibrate with measured points for accuracy.">
                approximate (auto)
              </span>
            )}
            {active.label && <div className="truncate text-zinc-500">{active.label}</div>}
            {active.warnings.map((w) => (
              <div key={w} className="text-amber-400">
                ⚠ {w}
              </div>
            ))}
          </>
        ) : (
          <span className="text-amber-400">Uncalibrated: positions are null and nothing is placed in 3D.</span>
        )}
      </div>

      {cal.editing && (
        <div className="mt-2 space-y-1.5">
          <p className="text-zinc-500">
            Click ≥4 floor points on the video (no 3 on a line), then give each its physical floor position in meters or pick a
            reference point.
          </p>
          {cal.points.map((p, i) => (
            <div key={i} className="flex items-center gap-1">
              <span className="w-4 font-mono text-yellow-400">{i + 1}</span>
              <span className="w-24 font-mono text-zinc-500">
                {p.image[0].toFixed(0)},{p.image[1].toFixed(0)} px
              </span>
              <select
                className="input w-28"
                value={p.referenceId ?? ''}
                onChange={(ev) => {
                  const ref = model?.reference_points.find((r) => r.point_id === ev.target.value)
                  updatePoint(i, { referenceId: ref?.point_id ?? null, world: ref ? [ref.x, ref.y] : p.world })
                }}
              >
                <option value="">custom</option>
                {model?.reference_points.map((r) => (
                  <option key={r.point_id} value={r.point_id}>
                    {r.label}
                  </option>
                ))}
              </select>
              {(['x', 'y'] as const).map((axis, k) => (
                <input
                  key={axis}
                  className="input w-14 font-mono"
                  type="number"
                  step="0.05"
                  placeholder={axis}
                  value={p.world ? p.world[k] : ''}
                  onChange={(ev) => {
                    const val = parseFloat(ev.target.value)
                    const w: [number, number] = p.world ? [...p.world] : [NaN, NaN]
                    w[k] = val
                    updatePoint(i, { world: Number.isFinite(w[0]) && Number.isFinite(w[1]) ? w : null, referenceId: null })
                  }}
                />
              ))}
              <button className="text-zinc-500 hover:text-red-400" onClick={() => cal.set({ points: cal.points.filter((_, j) => j !== i), preview: null })}>
                ✕
              </button>
            </div>
          ))}
          <div className="flex flex-wrap gap-1 pt-1">
            <button
              className="btn"
              disabled={busy || complete.length < 4}
              onClick={() => run(async () => cal.set({ preview: await api.previewCalibration(body()) }))}
            >
              Preview
            </button>
            <button
              className="btn btn-primary"
              disabled={busy || complete.length < 4 || !running}
              onClick={() =>
                run(async () => {
                  const res = await api.saveCalibration(body())
                  await api.applyCalibration(res.calibration.calibration_id)
                  cal.set({ active: res.calibration, overlay: res.overlay, preview: null, editing: false, points: [] })
                })
              }
            >
              Save & apply
            </button>
          </div>
          {cal.preview && (
            <div className="text-zinc-400">
              Preview RMSE {cal.preview.calibration.reprojection_rmse_m.toFixed(3)} m, max {cal.preview.calibration.max_error_m.toFixed(3)} m.
              Hover the video to see where a pixel lands in 3D.
            </div>
          )}
          {saved.length > 0 && (
            <div className="pt-1">
              <div className="text-zinc-500">Saved calibrations</div>
              {saved.slice(0, 6).map((c) => (
                <div key={c.calibration_id} className="flex items-center justify-between gap-1">
                  <span className="truncate font-mono text-zinc-400" title={c.label ?? ''}>
                    {c.calibration_id} · {c.image_width}×{c.image_height} · {c.reprojection_rmse_m.toFixed(3)} m{c.simulated ? ' · sim' : ''}
                  </span>
                  <button
                    className="btn"
                    disabled={!running || busy}
                    onClick={() =>
                      run(async () => {
                        await api.applyCalibration(c.calibration_id)
                        await refreshActiveCalibration()
                        cal.set({ editing: false, points: [], preview: null })
                      })
                    }
                  >
                    Apply
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {cal.error && <div className="mt-1 text-red-400">{cal.error}</div>}
    </div>
  )
}
