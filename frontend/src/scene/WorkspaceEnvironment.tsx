import { Suspense, useMemo } from 'react'
import { Html, useTexture } from '@react-three/drei'
import * as THREE from 'three'
import { withBackend } from '../lib/backend'
import { fixtureName, modelBounds, PROVENANCE_COLORS } from '../lib/model'
import type { Fixture, FloorTexture, SpatialModel } from '../lib/types'
import { AssetBoundary } from './ImageBlasterWorld'

/** Top-down image of the real floor, warped from the camera's people-free background plate. */
function FloorImage({ tex }: { tex: FloorTexture }) {
  const map = useTexture(withBackend(tex.url))
  map.colorSpace = THREE.SRGBColorSpace
  map.anisotropy = 8
  const [w, d] = tex.size_m
  return (
    <mesh position={[tex.origin[0] + w / 2, 0.002, -(tex.origin[1] + d / 2)]} rotation-x={-Math.PI / 2} receiveShadow>
      <planeGeometry args={[w, d]} />
      <meshStandardMaterial map={map} transparent alphaTest={0.05} roughness={1} />
    </mesh>
  )
}

function FixtureBox({ f, showLabel }: { f: Fixture; showLabel: boolean }) {
  const [w, d, h] = f.size
  const color = PROVENANCE_COLORS[f.provenance] ?? '#a1a1aa'
  const guessed = f.estimated_dims.length > 0
  return (
    <group position={[f.center[0], 0, -f.center[1]]} rotation-y={(f.yaw_deg * Math.PI) / 180}>
      <mesh position={[0, h / 2, 0]} castShadow receiveShadow>
        <boxGeometry args={[w, h, d]} />
        <meshStandardMaterial color={f.provenance === 'template' ? '#a1a1aa' : color} roughness={0.6} transparent={guessed} opacity={guessed ? 0.72 : 1} />
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
  const walls = useMemo(() => {
    if (model.source === 'analyzed' || model.source === 'blank') return []
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
        <meshStandardMaterial color={model.source === 'blank' ? '#27272a' : '#3f3f46'} roughness={0.9} />
      </mesh>
      {model.floor_texture && (
        <AssetBoundary key={model.floor_texture.url} label="floor texture">
          <Suspense fallback={null}>
            <FloorImage tex={model.floor_texture} />
          </Suspense>
        </AssetBoundary>
      )}
      <gridHelper args={[span, Math.round(span * 2), '#52525b', '#3f3f46']} position={[(b.x0 + b.x1) / 2, model.floor_texture ? 0.004 : 0.002, -(b.y0 + b.y1) / 2]}>
        <meshBasicMaterial attach="material" transparent opacity={model.floor_texture ? 0.25 : 1} />
      </gridHelper>
      {walls.map((wl, i) => (
        <mesh key={i} position={wl.pos} rotation-y={wl.rot}>
          <boxGeometry args={[wl.len, 0.3, 0.06]} />
          <meshStandardMaterial color="#27272a" />
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
