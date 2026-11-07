import { useEffect, useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { Html } from '@react-three/drei'
import * as THREE from 'three'
import { TRAIL_MAX_POINTS, useTwin, type EntityView } from '../state/twin'
import { useScene } from '../state/ui'
import { useZoneName } from '../lib/workspace'

/** No update for this long while "active" means the stream stalled; render as stale. */
export const STALE_AFTER_MS = 1500
/** Exponential smoothing time constant for position interpolation between tracking updates. */
const SMOOTHING_TAU_S = 0.12

export function entityColor(e: Pick<EntityView, 'class_name' | 'source'>): string {
  if (e.source === 'aruco') return '#f59e0b'
  if (e.class_name === 'person') return '#38bdf8'
  return '#a3e635'
}

export function visualState(e: EntityView, now: number): 'active' | 'lost' | 'stale' {
  if (e.state === 'lost') return 'lost'
  if (now - e.received_at > STALE_AFTER_MS) return 'stale'
  return 'active'
}

function Trail({ id, color }: { id: string; color: string }) {
  const lastTrail = useRef<[number, number][] | null>(null)
  const { geom, line } = useMemo(() => {
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(TRAIL_MAX_POINTS * 3 + 3), 3))
    g.setDrawRange(0, 0)
    const l = new THREE.Line(g, new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.7 }))
    l.frustumCulled = false
    return { geom: g, line: l }
  }, [color])
  useEffect(
    () => () => {
      geom.dispose()
      ;(line.material as THREE.Material).dispose()
    },
    [geom, line],
  )

  useFrame(() => {
    const e = useTwin.getState().entities[id]
    const show = useScene.getState().showTrails
    line.visible = show && !!e && e.trail.length > 1
    if (!e || e.trail === lastTrail.current) return
    lastTrail.current = e.trail
    const attr = geom.getAttribute('position') as THREE.BufferAttribute
    const n = Math.min(e.trail.length, TRAIL_MAX_POINTS + 1)
    const start = e.trail.length - n
    for (let i = 0; i < n; i++) {
      const [x, y] = e.trail[start + i]
      attr.setXYZ(i, x, 0.03, -y)
    }
    attr.needsUpdate = true
    geom.setDrawRange(0, n)
  })

  return <primitive object={line} />
}

function Avatar({ id }: { id: string }) {
  const group = useRef<THREE.Group>(null)
  const body = useRef<THREE.MeshStandardMaterial>(null)
  const ring = useRef<THREE.Mesh>(null)
  const initialized = useRef(false)
  const cls = useTwin((s) => s.entities[id]?.class_name ?? 'unknown')
  const source = useTwin((s) => s.entities[id]?.source ?? 'yolo')
  const label = useTwin((s) => s.entities[id]?.label ?? null)
  const state = useTwin((s) => s.entities[id]?.state ?? 'lost')
  const zone = useTwin((s) => s.entities[id]?.zone_id ?? null)
  const zoneName = useZoneName()
  const selected = useTwin((s) => s.selectedId === id)
  const select = useTwin((s) => s.select)
  const color = entityColor({ class_name: cls, source })
  const isPerson = cls === 'person'

  useFrame((_, delta) => {
    const g = group.current
    const e = useTwin.getState().entities[id]
    if (!g) return
    if (!e || !e.position) {
      g.visible = false
      return
    }
    g.visible = true
    const tx = e.position.x
    const tz = -e.position.y
    if (!initialized.current) {
      g.position.set(tx, 0, tz)
      initialized.current = true
    } else {
      const k = 1 - Math.exp(-delta / SMOOTHING_TAU_S)
      g.position.x += (tx - g.position.x) * k
      g.position.z += (tz - g.position.z) * k
    }
    const vs = visualState(e, Date.now())
    if (body.current) {
      body.current.opacity = vs === 'active' ? 1 : vs === 'stale' ? 0.55 : 0.3
      body.current.color.set(vs === 'lost' ? '#71717a' : color)
    }
    if (ring.current) ring.current.visible = useTwin.getState().selectedId === id
  })

  return (
    <group
      ref={group}
      visible={false}
      onClick={(ev) => {
        ev.stopPropagation()
        select(selected ? null : id)
      }}
    >
      {isPerson ? (
        <mesh position-y={0.8} castShadow>
          <capsuleGeometry args={[0.2, 1.1, 6, 12]} />
          <meshStandardMaterial ref={body} color={color} transparent roughness={0.5} />
        </mesh>
      ) : (
        <mesh position-y={0.15} castShadow>
          <boxGeometry args={[0.35, 0.3, 0.28]} />
          <meshStandardMaterial ref={body} color={color} transparent roughness={0.5} />
        </mesh>
      )}
      <mesh ref={ring} rotation-x={-Math.PI / 2} position-y={0.02} visible={false}>
        <ringGeometry args={[0.32, 0.42, 32]} />
        <meshBasicMaterial color="#fafafa" />
      </mesh>
      <Html position={[0, isPerson ? 1.75 : 0.55, 0]} center zIndexRange={[20, 10]} style={{ pointerEvents: 'none' }}>
        <div
          className={`whitespace-nowrap rounded px-1.5 py-0.5 font-mono text-[10px] ${selected ? 'bg-white text-black' : 'bg-black/70 text-white'}`}
        >
          {label ?? id}
          {state === 'lost' ? ' · lost' : ''}
          {zone ? ` · ${zoneName(zone)}` : ''}
        </div>
      </Html>
    </group>
  )
}

export function EntitiesLayer() {
  const ids = useTwin((s) => s.entityIds)
  return (
    <group>
      {ids.map((id) => (
        <group key={id}>
          <Avatar id={id} />
          <TrailForId id={id} />
        </group>
      ))}
    </group>
  )
}

function TrailForId({ id }: { id: string }) {
  const cls = useTwin((s) => s.entities[id]?.class_name ?? 'unknown')
  const source = useTwin((s) => s.entities[id]?.source ?? 'yolo')
  return <Trail id={id} color={entityColor({ class_name: cls, source })} />
}
