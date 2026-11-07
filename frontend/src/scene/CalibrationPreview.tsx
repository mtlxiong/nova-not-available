import { Html } from '@react-three/drei'
import type { SpatialModel } from '../lib/types'
import { useCalibration } from '../state/ui'

/**
 * Shows, inside the 3D scene, where calibration points land and where the pixel under the cursor
 * on the video would place a tracked entity. All positions go through the same world->render
 * alignment as live entities.
 */
export function CalibrationPreview({ model }: { model: SpatialModel }) {
  const editing = useCalibration((s) => s.editing)
  const hover = useCalibration((s) => s.hoverWorld)
  const points = useCalibration((s) => s.points)
  const active = useCalibration((s) => s.preview?.calibration ?? s.active)

  return (
    <group>
      {editing &&
        model.reference_points.map((p) => (
          <group key={p.point_id} position={[p.x, 0, -p.y]}>
            <mesh position-y={0.25}>
              <cylinderGeometry args={[0.03, 0.03, 0.5, 8]} />
              <meshBasicMaterial color="#e879f9" />
            </mesh>
            <Html position={[0, 0.6, 0]} center style={{ pointerEvents: 'none' }}>
              <div className="whitespace-nowrap rounded bg-fuchsia-900/80 px-1 text-[9px] text-fuchsia-100">{p.point_id}</div>
            </Html>
          </group>
        ))}
      {editing &&
        points.map(
          (p, i) =>
            p.world && (
              <mesh key={i} position={[p.world[0], 0.05, -p.world[1]]}>
                <sphereGeometry args={[0.09, 12, 12]} />
                <meshBasicMaterial color="#facc15" />
              </mesh>
            ),
        )}
      {!editing &&
        active?.points.map((p, i) => (
          <mesh key={i} position={[p.world[0], 0.03, -p.world[1]]}>
            <sphereGeometry args={[0.05, 8, 8]} />
            <meshBasicMaterial color="#facc15" transparent opacity={0.6} />
          </mesh>
        ))}
      {hover && (
        <group position={[hover[0], 0, -hover[1]]}>
          <mesh position-y={0.8}>
            <capsuleGeometry args={[0.2, 1.1, 4, 10]} />
            <meshBasicMaterial color="#facc15" wireframe />
          </mesh>
          <Html position={[0, 1.8, 0]} center style={{ pointerEvents: 'none' }}>
            <div className="whitespace-nowrap rounded bg-yellow-400 px-1 font-mono text-[10px] text-black">
              preview ({hover[0].toFixed(2)}, {hover[1].toFixed(2)}) m
            </div>
          </Html>
        </group>
      )}
    </group>
  )
}
