import { useEffect, useMemo } from 'react'
import { Html } from '@react-three/drei'
import * as THREE from 'three'
import { fixtureName, modelBounds } from '../lib/model'
import type { Fixture, SpatialModel } from '../lib/types'

type Surface = 'floor' | 'wood' | 'fabric' | 'metal'

const textures = new Map<Surface, THREE.Texture>()

function paintSurface(kind: Surface, ctx: CanvasRenderingContext2D) {
  const n = 256
  if (kind === 'floor') {
    ctx.fillStyle = '#c4a484'
    ctx.fillRect(0, 0, n, n)
    for (let y = 0; y < n; y += 32) {
      ctx.fillStyle = y % 64 === 0 ? '#a8845c' : '#d2b48c'
      ctx.fillRect(0, y, n, 30)
      ctx.fillStyle = 'rgba(80, 48, 20, 0.35)'
      ctx.fillRect(0, y + 30, n, 2)
    }
    ctx.strokeStyle = 'rgba(90, 55, 25, 0.25)'
    for (let i = 0; i < 40; i++) {
      ctx.beginPath()
      ctx.moveTo(0, 8 + i * 6)
      ctx.bezierCurveTo(80, 6 + i * 6, 160, 12 + i * 6, n, 8 + i * 6)
      ctx.stroke()
    }
    return
  }
  if (kind === 'wood') {
    ctx.fillStyle = '#8d6e53'
    ctx.fillRect(0, 0, n, n)
    ctx.strokeStyle = 'rgba(60, 36, 18, 0.35)'
    for (let y = 4; y < n; y += 7) {
      ctx.beginPath()
      ctx.moveTo(0, y)
      ctx.bezierCurveTo(70, y - 2, 150, y + 3, n, y)
      ctx.stroke()
    }
    return
  }
  if (kind === 'fabric') {
    ctx.fillStyle = '#6e7c86'
    ctx.fillRect(0, 0, n, n)
    ctx.fillStyle = 'rgba(255,255,255,0.06)'
    for (let y = 0; y < n; y += 4) ctx.fillRect(0, y, n, 1)
    ctx.fillStyle = 'rgba(0,0,0,0.08)'
    for (let x = 0; x < n; x += 4) ctx.fillRect(x, 0, 1, n)
    return
  }
  const g = ctx.createLinearGradient(0, 0, n, n)
  g.addColorStop(0, '#9ca3af')
  g.addColorStop(0.45, '#e5e7eb')
  g.addColorStop(1, '#6b7280')
  ctx.fillStyle = g
  ctx.fillRect(0, 0, n, n)
}

function surfaceTexture(kind: Surface) {
  const cached = textures.get(kind)
  if (cached) return cached
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 256
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  paintSurface(kind, ctx)
  const tex = new THREE.CanvasTexture(canvas)
  tex.colorSpace = THREE.SRGBColorSpace
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping
  tex.anisotropy = 8
  textures.set(kind, tex)
  return tex
}

/** A stock material for the kind of thing the detector named, not a crop of the camera frame. */
export function surfaceForKind(kind: string): Surface {
  const k = kind.toLowerCase()
  if (/sofa|couch|cushion|bed|pillow|mattress/.test(k)) return 'fabric'
  if (/bin|box|fridge|appliance|machine|metal|cart|rack/.test(k)) return 'metal'
  return 'wood'
}

function tiled(kind: Surface, repeatX: number, repeatY: number) {
  const base = surfaceTexture(kind)
  if (!base) return null
  const map = base.clone()
  map.wrapS = map.wrapT = THREE.RepeatWrapping
  map.repeat.set(Math.max(repeatX, 0.5), Math.max(repeatY, 0.5))
  return map
}

function FixtureBox({ f, showLabel }: { f: Fixture; showLabel: boolean }) {
  const [w, d, h] = f.size
  const surface = surfaceForKind(f.kind)
  const map = useMemo(() => tiled(surface, w / 0.45, h / 0.45), [surface, w, h])
  useEffect(() => () => map?.dispose(), [map])
  return (
    <group position={[f.center[0], 0, -f.center[1]]} rotation-y={(f.yaw_deg * Math.PI) / 180}>
      <mesh position={[0, h / 2, 0]} castShadow receiveShadow>
        <boxGeometry args={[w, h, d]} />
        <meshStandardMaterial map={map ?? undefined} color={map ? '#ffffff' : '#a1a1aa'} roughness={surface === 'metal' ? 0.35 : 0.8} metalness={surface === 'metal' ? 0.45 : 0} />
      </mesh>
      {showLabel && f.provenance !== 'template' && (
        <Html position={[0, h + 0.15, 0]} center distanceFactor={10} zIndexRange={[10, 0]}>
          <div className="pointer-events-none whitespace-nowrap rounded bg-black/70 px-1 text-[10px] text-amber-200">
            {fixtureName(f)}
            {f.confidence != null && <span className="text-zinc-400"> {(f.confidence * 100).toFixed(0)}%</span>}
          </div>
        </Html>
      )}
    </group>
  )
}

export function WorkspaceEnvironment({ model, showLabels = true }: { model: SpatialModel; showLabels?: boolean }) {
  const b = modelBounds(model)
  const w = b.x1 - b.x0
  const d = b.y1 - b.y0
  const floorShape = useMemo(() => new THREE.Shape(model.floor_polygon.map(([x, y]) => new THREE.Vector2(x, y))), [model])
  const floorMap = useMemo(() => (model.source === 'blank' ? null : tiled('floor', w / 1.5, d / 1.5)), [model.source, w, d])
  useEffect(() => () => floorMap?.dispose(), [floorMap])
  const walls = useMemo(() => {
    if (model.source === 'blank') return []
    const out: { pos: [number, number, number]; len: number; rot: number }[] = []
    const pts = model.floor_polygon
    for (let i = 0; i < pts.length; i++) {
      const [x1, y1] = pts[i]
      const [x2, y2] = pts[(i + 1) % pts.length]
      out.push({ pos: [(x1 + x2) / 2, 0.15, -(y1 + y2) / 2], len: Math.hypot(x2 - x1, y2 - y1), rot: Math.atan2(y2 - y1, x2 - x1) })
    }
    return out
  }, [model])
  const span = Math.max(w, d)

  return (
    <group>
      <mesh rotation-x={-Math.PI / 2} receiveShadow>
        <shapeGeometry args={[floorShape]} />
        <meshStandardMaterial map={floorMap ?? undefined} color={floorMap ? '#ffffff' : '#27272a'} roughness={0.9} />
      </mesh>
      <gridHelper args={[span, Math.round(span * 2), '#52525b', '#3f3f46']} position={[(b.x0 + b.x1) / 2, 0.004, -(b.y0 + b.y1) / 2]}>
        <meshBasicMaterial attach="material" transparent opacity={0.35} />
      </gridHelper>
      {walls.map((wl, i) => (
        <mesh key={i} position={wl.pos} rotation-y={wl.rot}>
          <boxGeometry args={[wl.len, 0.3, 0.06]} />
          <meshStandardMaterial color="#3f3f46" />
        </mesh>
      ))}
      {model.fixtures.map((f) => (
        <FixtureBox key={f.fixture_id} f={f} showLabel={showLabels} />
      ))}
    </group>
  )
}

/** Floor boundary outline, drawn over Image-Blaster worlds to judge alignment. */
export function FloorOutline({ model }: { model: SpatialModel }) {
  const geom = useMemo(() => {
    const pts = model.floor_polygon.map(([x, y]) => new THREE.Vector3(x, 0.01, -y))
    pts.push(pts[0].clone())
    return new THREE.BufferGeometry().setFromPoints(pts)
  }, [model])
  return (
    <lineLoop geometry={geom}>
      <lineBasicMaterial color="#f4f4f5" />
    </lineLoop>
  )
}
