/**
 * Gaussian-splat environment renderer, ported from Image-Blaster (MIT,
 * app/src/modules/splat/SplatRenderer.tsx). Same Spark renderer and the same
 * flip_y / ground_plane_offset / metric_scale_factor transform, so a world renders exactly as in
 * Image-Blaster. The depth-of-field shader patch and debug store were dropped.
 */
import { Component, Suspense, useEffect, useMemo, useRef, type ReactNode } from 'react'
import { extend, useThree } from '@react-three/fiber'
import { useGLTF } from '@react-three/drei'
import { SparkRenderer, SplatMesh } from '@sparkjsdev/spark'
import * as THREE from 'three'

const SparkRendererEl = extend(SparkRenderer)
const SplatMeshEl = extend(SplatMesh)
const ignoreRaycast: THREE.Object3D['raycast'] = () => {}

interface Semantics {
  metric_scale_factor: number
  ground_plane_offset: number
  flip_y: boolean
}

function SplatLayer({ url, semantics, visible }: { url: string; semantics: Semantics; visible: boolean }) {
  const gl = useThree((s) => s.gl)
  const sparkRef = useRef<SparkRenderer>(null)
  const splatRef = useRef<SplatMesh>(null)
  const sparkArgs = useMemo(() => ({ renderer: gl }), [gl])
  // A new SplatMesh is created only when the asset URL changes; tracking updates never touch it.
  const splatArgs = useMemo(() => ({ url }), [url])

  useEffect(() => {
    if (sparkRef.current) sparkRef.current.raycast = ignoreRaycast
    if (splatRef.current) splatRef.current.raycast = ignoreRaycast
  }, [url])

  return (
    <SparkRendererEl ref={sparkRef} args={[sparkArgs]} visible={visible}>
      <group
        position={[0, semantics.ground_plane_offset, 0]}
        rotation={[semantics.flip_y ? Math.PI : 0, 0, 0]}
        scale={semantics.metric_scale_factor}
      >
        <SplatMeshEl ref={splatRef} args={[splatArgs]} />
      </group>
    </SparkRendererEl>
  )
}

function ColliderWireframe({ url, semantics }: { url: string; semantics: Semantics }) {
  const { scene } = useGLTF(url)
  const wire = useMemo(() => {
    const clone = scene.clone(true)
    const mat = new THREE.MeshBasicMaterial({ color: '#38bdf8', wireframe: true, transparent: true, opacity: 0.15, depthWrite: false })
    clone.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) {
        ;(o as THREE.Mesh).material = mat
        o.raycast = ignoreRaycast
      }
    })
    return clone
  }, [scene])
  return (
    <primitive
      object={wire}
      position={[0, semantics.ground_plane_offset, 0]}
      rotation={[semantics.flip_y ? Math.PI : 0, 0, 0]}
      scale={semantics.metric_scale_factor}
    />
  )
}

export class AssetBoundary extends Component<{ label: string; children: ReactNode; onError?: (msg: string) => void }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  componentDidCatch(error: unknown) {
    const msg = `Failed to load ${this.props.label}: ${error instanceof Error ? error.message : String(error)}`
    console.warn(msg)
    this.props.onError?.(msg)
  }
  render() {
    return this.state.failed ? null : this.props.children
  }
}

export function ImageBlasterWorld({
  splatUrl,
  colliderUrl,
  semantics,
  showSplat,
  showCollider,
  onError,
}: {
  splatUrl: string | null
  colliderUrl: string | null
  semantics: Semantics
  showSplat: boolean
  showCollider: boolean
  onError?: (msg: string) => void
}) {
  return (
    <>
      {splatUrl && (
        <AssetBoundary key={splatUrl} label={splatUrl} onError={onError}>
          <SplatLayer url={splatUrl} semantics={semantics} visible={showSplat} />
        </AssetBoundary>
      )}
      {colliderUrl && showCollider && (
        <AssetBoundary key={colliderUrl} label={colliderUrl} onError={onError}>
          <Suspense fallback={null}>
            <ColliderWireframe url={colliderUrl} semantics={semantics} />
          </Suspense>
        </AssetBoundary>
      )}
    </>
  )
}
