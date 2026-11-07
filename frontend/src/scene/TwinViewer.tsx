import { useEffect, useMemo, useRef, useState } from 'react'
import { Canvas, useThree } from '@react-three/fiber'
import * as THREE from 'three'
import { OrbitControls } from '@react-three/drei'
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib'
import { api } from '../lib/api'
import { worldToRender } from '../lib/alignment'
import { verticalFovDeg, type VideoPose, videoCameraPose } from '../lib/cameraPose'
import type { CalibrationResult, Point2, SpatialModel } from '../lib/types'
import { useReplay, useScene, useWorkspace } from '../state/ui'
import { useTwin } from '../state/twin'
import { CalibrationPreview } from './CalibrationPreview'
import { EntitiesLayer } from './EntitiesLayer'
import { HeatmapLayer } from './HeatmapLayer'
import { ImageBlasterWorld } from './ImageBlasterWorld'
import { FloorOutline, WorkspaceEnvironment } from './WorkspaceEnvironment'
import { withBackend } from '../lib/backend'
import { modelBounds } from '../lib/model'
import { ZonesLayer } from './ZonesLayer'

function CameraRig({ model, pose }: { model: SpatialModel; pose: VideoPose | null }) {
  const controls = useRef<OrbitControlsImpl>(null)
  const camera = useThree((s) => s.camera)
  const view = useScene((s) => s.view)
  const nonce = useScene((s) => s.viewNonce)
  const alignment = useScene((s) => s.alignment)
  const size = useThree((s) => s.size)

  useEffect(() => {
    const b = modelBounds(model)
    const [cx, cy, cz] = worldToRender(alignment, (b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2)
    const span = Math.max(b.x1 - b.x0, b.y1 - b.y0) * alignment.scale
    const persp = camera as THREE.PerspectiveCamera
    if (view === 'video' && pose) {
      const [px, py, pz] = worldToRender(alignment, pose.position[0], pose.position[1], pose.position[2])
      const [lx, ly, lz] = worldToRender(alignment, pose.lookAt[0], pose.lookAt[1], 0)
      persp.fov = verticalFovDeg(pose.hfovDeg, size.width / Math.max(size.height, 1))
      persp.updateProjectionMatrix()
      camera.position.set(px, py, pz)
      controls.current?.target.set(lx, ly, lz)
    } else if (view === 'top') {
      persp.fov = 50
      persp.updateProjectionMatrix()
      camera.position.set(cx, cy + span * 1.25, cz + 0.001)
      controls.current?.target.set(cx, cy, cz)
    } else if (view === 'iso') {
      persp.fov = 50
      persp.updateProjectionMatrix()
      camera.position.set(cx - span * 0.55, cy + span * 0.75, cz + span * 0.85)
      controls.current?.target.set(cx, cy, cz)
    } else return
    controls.current?.update()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce, model, camera, pose, size.width, size.height])

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
  const view = useScene((s) => s.view)
  const worldBuild = useScene((s) => s.worldBuild)
  const metrics = useReplay((s) => s.metrics)
  const select = useTwin((s) => s.select)
  const [assetError, setAssetError] = useState<string | null>(null)
  const [workspaceCal, setWorkspaceCal] = useState<CalibrationResult | null>(null)
  const scene = scenes.find((s) => s.scene_id === sceneId)
  const blaster = scene?.kind === 'image-blaster' ? scene : null

  useEffect(() => setAssetError(null), [sceneId])
  useEffect(() => {
    const id = model?.calibration_id
    if (!id) {
      setWorkspaceCal(null)
      return
    }
    let cancel = false
    api.calibration(id).then((res) => {
      if (!cancel) setWorkspaceCal(res.calibration)
    }).catch(() => {
      if (!cancel) setWorkspaceCal(null)
    })
    return () => {
      cancel = true
    }
  }, [model?.calibration_id])

  const pose = useMemo(() => (workspaceCal && model ? videoCameraPose(workspaceCal, model.notes) : null), [workspaceCal, model])

  if (!model) return <div className="flex h-full items-center justify-center text-sm text-zinc-500">Loading spatial model…</div>

  return (
    <div className="relative h-full w-full">
      <Canvas camera={{ fov: 50, near: 0.05, far: 500, position: [-3, 9, 6] }} dpr={[1, 2]} shadows onPointerMissed={() => select(null)}>
        <color attach="background" args={['#0c0e13']} />
        <hemisphereLight args={['#e4e4e7', '#18181b', 0.9]} />
        <directionalLight position={[6, 12, 4]} intensity={1.1} castShadow shadow-mapSize={[1024, 1024]} />
        {blaster && (
          <ImageBlasterWorld
            splatUrl={withBackend(blaster.splat_url)}
            colliderUrl={withBackend(blaster.collider_url)}
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
        <CameraRig model={model} pose={pose} />
      </Canvas>
      <div className="pointer-events-none absolute left-3 top-3 max-w-[70%] rounded-2xl bg-black/55 px-3 py-2.5 text-[11px] text-paper/90 shadow-panel ring-1 ring-white/10 backdrop-blur-md">
        {!blaster ? (
          <div>
            <div className="font-medium">{model.name}</div>
            {model.source === 'template' && <div className="mt-0.5 text-amber-200/90">Template layout, not from this camera</div>}
            {model.source === 'blank' && <div className="mt-0.5 text-mute">Not mapped yet. Workspace tab, then Analyze.</div>}
            {model.source === 'analyzed' && <div className="mt-0.5 text-mute">Reconstructed from camera footage</div>}
          </div>
        ) : (
          <div>
            <div className="font-medium">{blaster.display_name}</div>
            <div className="mt-0.5 text-mute">{blaster.synthetic ? 'Synthetic test splat' : 'Image-Blaster world'}</div>
            {assetError && <div className="mt-1 text-rose-300">{assetError} — showing the procedural room</div>}
          </div>
        )}
        <div className="seg pointer-events-auto mt-2">
          <button className={view === 'iso' ? 'on' : ''} onClick={() => useScene.getState().setView('iso')}>
            Iso
          </button>
          <button className={view === 'top' ? 'on' : ''} onClick={() => useScene.getState().setView('top')}>
            Top
          </button>
          <button className={view === 'video' ? 'on' : ''} disabled={!pose} title={pose ? 'Look through the camera that filmed this workspace' : 'This workspace has no camera pose'} onClick={() => useScene.getState().setView('video')}>
            Video
          </button>
        </div>
        {worldBuild && <div className="mt-2 text-amber-200">{worldBuild}</div>}
      </div>
    </div>
  )
}
