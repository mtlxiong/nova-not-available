import { useEffect, useMemo, useRef, useState } from 'react'
import { Canvas, useThree } from '@react-three/fiber'
import * as THREE from 'three'
import { OrbitControls } from '@react-three/drei'
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib'
import { worldToRender } from '../lib/alignment'
import type { Point2, SpatialModel } from '../lib/types'
import { useReplay, useScene, useWorkspace } from '../state/ui'
import { useTwin } from '../state/twin'
import { CalibrationPreview } from './CalibrationPreview'
import { EntitiesLayer } from './EntitiesLayer'
import { HeatmapLayer } from './HeatmapLayer'
import { ImageBlasterWorld } from './ImageBlasterWorld'
import { FloorOutline, WorkspaceEnvironment } from './WorkspaceEnvironment'
import { modelBounds } from '../lib/model'
import { ZonesLayer } from './ZonesLayer'

function CameraRig({ model }: { model: SpatialModel }) {
  const controls = useRef<OrbitControlsImpl>(null)
  const camera = useThree((s) => s.camera)
  const view = useScene((s) => s.view)
  const nonce = useScene((s) => s.viewNonce)
  const alignment = useScene((s) => s.alignment)

  useEffect(() => {
    const b = modelBounds(model)
    const [cx, cy, cz] = worldToRender(alignment, (b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2)
    const span = Math.max(b.x1 - b.x0, b.y1 - b.y0) * alignment.scale
    if (view === 'top') camera.position.set(cx, cy + span * 1.25, cz + 0.001)
    else if (view === 'iso') camera.position.set(cx - span * 0.55, cy + span * 0.75, cz + span * 0.85)
    else return
    controls.current?.target.set(cx, cy, cz)
    controls.current?.update()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce, model, camera])

  return <OrbitControls ref={controls} makeDefault enableDamping dampingFactor={0.12} onStart={() => useScene.setState({ view: 'free' })} />
}

function ZoneDraftLine({ points }: { points: Point2[] }) {
  const geom = useMemo(() => new THREE.BufferGeometry().setFromPoints(points.map(([x, y]) => new THREE.Vector3(x, 0.03, -y))), [points])
  return (
    <group>
      <line>
        <primitive object={geom} attach="geometry" />
        <lineBasicMaterial color="#facc15" />
      </line>
      {points.map(([x, y], i) => (
        <mesh key={i} position={[x, 0.05, -y]}>
          <sphereGeometry args={[0.08, 12, 12]} />
          <meshBasicMaterial color="#facc15" />
        </mesh>
      ))}
    </group>
  )
}

export function TwinViewer() {
  const savedModel = useScene((s) => s.model)
  const draft = useWorkspace((s) => s.draft)
  const zoneDraft = useWorkspace((s) => s.zoneDraft)
  const model = draft && savedModel && draft.model_id === savedModel.model_id ? draft : savedModel
  const scenes = useScene((s) => s.scenes)
  const sceneId = useScene((s) => s.sceneId)
  const alignment = useScene((s) => s.alignment)
  const showZones = useScene((s) => s.showZones)
  const showHeatmap = useScene((s) => s.showHeatmap)
  const showSplat = useScene((s) => s.showSplat)
  const showCollider = useScene((s) => s.showCollider)
  const showLabels = useScene((s) => s.showLabels)
  const metrics = useReplay((s) => s.metrics)
  const select = useTwin((s) => s.select)
  const [assetError, setAssetError] = useState<string | null>(null)
  const scene = scenes.find((s) => s.scene_id === sceneId)
  const blaster = scene?.kind === 'image-blaster' ? scene : null

  useEffect(() => setAssetError(null), [sceneId])

  if (!model) return <div className="flex h-full items-center justify-center text-sm text-zinc-500">Loading spatial model…</div>

  return (
    <div className="relative h-full w-full">
      <Canvas camera={{ fov: 50, near: 0.05, far: 500, position: [-3, 9, 6] }} dpr={[1, 2]} shadows onPointerMissed={() => select(null)}>
        <color attach="background" args={['#0b0d10']} />
        <hemisphereLight args={['#e4e4e7', '#18181b', 0.9]} />
        <directionalLight position={[6, 12, 4]} intensity={1.1} castShadow shadow-mapSize={[1024, 1024]} />
        {blaster && (
          <ImageBlasterWorld
            splatUrl={blaster.splat_url}
            colliderUrl={blaster.collider_url}
            semantics={blaster.semantics ?? { metric_scale_factor: 1, ground_plane_offset: 0, flip_y: true }}
            showSplat={showSplat}
            showCollider={showCollider}
            onError={setAssetError}
          />
        )}
        <group position={alignment.translation} rotation-y={(alignment.yaw_deg * Math.PI) / 180} scale={alignment.scale}>
          {blaster && !assetError ? <FloorOutline model={model} /> : <WorkspaceEnvironment model={model} showLabels={showLabels} />}
          {showZones && <ZonesLayer zones={model.zones} />}
          {showHeatmap && metrics && <HeatmapLayer heatmap={metrics.heatmap} />}
          <EntitiesLayer />
          <CalibrationPreview model={model} />
          {zoneDraft && zoneDraft.length > 0 && <ZoneDraftLine points={zoneDraft} />}
        </group>
        <CameraRig model={model} />
      </Canvas>
      {!blaster && (
        <div className="pointer-events-none absolute left-2 top-2 max-w-[60%] rounded bg-black/60 px-2 py-1 text-[11px] text-zinc-300">
          {model.name}
          {model.source === 'template' && <span className="text-amber-300"> · TEMPLATE layout (not from your camera)</span>}
          {model.source === 'blank' && <span className="text-zinc-400"> · not mapped yet — Workspace tab → Analyze</span>}
          {model.source === 'analyzed' && <span className="text-zinc-400"> · reconstructed from camera footage</span>}
        </div>
      )}
      {blaster && (
        <div className="pointer-events-none absolute left-2 top-2 max-w-[60%] rounded bg-black/60 px-2 py-1 text-[11px] text-zinc-300">
          {blaster.display_name}
          {blaster.synthetic ? ' · SYNTHETIC test splat (not Image-Blaster-generated)' : ' · Image-Blaster world'}
          {assetError && <div className="text-red-400">{assetError} — showing procedural fallback</div>}
        </div>
      )}
    </div>
  )
}
