import { useEffect, useMemo } from 'react'
import * as THREE from 'three'
import type { SessionMetrics } from '../lib/types'

/** Time-weighted occupancy heatmap from stored, calibrated observations. */
export function HeatmapLayer({ heatmap }: { heatmap: SessionMetrics['heatmap'] }) {
  const texture = useMemo(() => {
    const { cols, rows, seconds } = heatmap
    const max = Math.max(1e-6, ...seconds.flat())
    const data = new Uint8Array(cols * rows * 4)
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const v = Math.sqrt(seconds[r][c] / max)
        const i = (r * cols + c) * 4
        data[i] = Math.round(255 * Math.min(1, v * 2))
        data[i + 1] = Math.round(255 * Math.max(0, v * 2 - 1))
        data[i + 2] = Math.round(80 * (1 - v))
        data[i + 3] = seconds[r][c] > 0 ? Math.round(60 + 170 * v) : 0
      }
    }
    const tex = new THREE.DataTexture(data, cols, rows, THREE.RGBAFormat)
    tex.magFilter = THREE.NearestFilter
    tex.needsUpdate = true
    return tex
  }, [heatmap])
  useEffect(() => () => texture.dispose(), [texture])

  const w = heatmap.cols * heatmap.cell_m
  const h = heatmap.rows * heatmap.cell_m
  // Texture row 0 = lowest world y. Plane in XY (world-local after rotation) with v up = +y.
  return (
    <mesh rotation-x={-Math.PI / 2} position={[heatmap.origin[0] + w / 2, 0.02, -(heatmap.origin[1] + h / 2)]} renderOrder={2}>
      <planeGeometry args={[w, h]} />
      <meshBasicMaterial map={texture} transparent depthWrite={false} />
    </mesh>
  )
}
