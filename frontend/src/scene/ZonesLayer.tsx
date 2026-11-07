import { useMemo } from 'react'
import { Html } from '@react-three/drei'
import * as THREE from 'three'
import type { Zone } from '../lib/types'
import { useTwin } from '../state/twin'

function ZoneMesh({ zone }: { zone: Zone }) {
  const occupancy = useTwin((s) => {
    let n = 0
    for (const id of s.entityIds) if (s.entities[id]?.zone_id === zone.zone_id && s.entities[id]?.state === 'active') n++
    return n
  })
  const { shape, outline, centroid } = useMemo(() => {
    const shape = new THREE.Shape(zone.polygon.map(([x, y]) => new THREE.Vector2(x, y)))
    const pts = zone.polygon.map(([x, y]) => new THREE.Vector3(x, y, 0))
    pts.push(pts[0].clone())
    const outline = new THREE.BufferGeometry().setFromPoints(pts)
    const cx = zone.polygon.reduce((a, p) => a + p[0], 0) / zone.polygon.length
    const cy = zone.polygon.reduce((a, p) => a + p[1], 0) / zone.polygon.length
    return { shape, outline, centroid: [cx, cy] as const }
  }, [zone])

  return (
    <group>
      {/* rotation-x = -90deg maps shape (x, y) to world-local (x, 0, -y) */}
      <group rotation-x={-Math.PI / 2} position-y={0.012}>
        <mesh renderOrder={1}>
          <shapeGeometry args={[shape]} />
          <meshBasicMaterial color={zone.color} transparent opacity={occupancy > 0 ? 0.32 : 0.12} depthWrite={false} />
        </mesh>
        <lineLoop geometry={outline}>
          <lineBasicMaterial color={zone.color} />
        </lineLoop>
      </group>
      <Html position={[centroid[0], 0.05, -centroid[1]]} center zIndexRange={[10, 0]} style={{ pointerEvents: 'none' }}>
        <div className="whitespace-nowrap rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-medium" style={{ color: zone.color }}>
          {zone.name}
          {occupancy > 0 ? ` · ${occupancy}` : ''}
        </div>
      </Html>
    </group>
  )
}

export function ZonesLayer({ zones }: { zones: Zone[] }) {
  return (
    <group>
      {zones.map((z) => (
        <ZoneMesh key={z.zone_id} zone={z} />
      ))}
    </group>
  )
}
