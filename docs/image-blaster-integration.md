# Image-Blaster integration

Inspected: `https://github.com/neilsonnn/image-blaster` at commit `4acb43ba` (2026-05-14), cloned into
`third_party/image-blaster/` (git-ignored, reference only).

## What Image-Blaster is

Image-Blaster is an **offline asset-generation pipeline driven by Claude Code skills**, plus a local React
viewer for its outputs. It is not a library and has no runtime API.

| Concern | Finding |
| --- | --- |
| License | MIT (Neilson Koerner-Safrata, 2026). Reuse with attribution is fine. |
| Environment generation | `.claude/scripts/world/generate-world.mjs` calls the **World Labs Marble API** (`marble-1.1`, `WORLD_LABS_API_KEY`, paid). One image (or text prompt) in, one world out, after minutes of polling. |
| Object meshes / SFX / edits | FAL (`FAL_KEY`, paid): Hunyuan-3D, nano-banana / gpt-image-2, ElevenLabs SFX. |
| Outputs (disk-first) | `worlds/<slug>/output/world/N-world.json` (Marble response), `N-world-full_res.spz` (+ `100k`/`500k` LODs), `N-world.glb` (collider mesh), `N-world-pano.png`, `N-world-thumbnail.webp`; objects in `worlds/<slug>/output/<object>/N-<object>.glb`. |
| Viewer stack | Vite 5, React 19, `@react-three/fiber` 9, `drei` 10, `three` 0.180, `@react-three/rapier`, `@sparkjsdev/spark` ^2 (Gaussian splats), zustand 5, Tailwind 3, Radix themes. |
| Splat loading | `app/src/modules/splat/SplatRenderer.tsx`: one `SparkRenderer` wrapping a `SplatMesh({ url })`, inside a group with `position=[0, ground_plane_offset, 0]`, `rotation=[flip_y ? PI : 0, 0, 0]`, `scale=metric_scale_factor` (all from `world.assets.splats.semantics_metadata`; `flip_y` defaults to `true`). |
| Mesh loading | `WorldCollider.tsx` uses drei `useGLTF` on the collider GLB with the same transform, wrapped in a Rapier trimesh body. Objects (`SceneObject.tsx`) also use `useGLTF`. |
| Asset discovery | A Vite plugin (`vite.config.ts`, `virtual:worlds`, `/__worlds`) scans `worlds/` at dev time and serves `/worlds/...` files. |
| Real-time features | **None.** No video input, tracking, calibration, metric alignment, networking, or analytics. |

## What Image-Blaster does *not* give us

- No real-time reconstruction: one Marble generation takes minutes and costs money. ECHO generates once and reuses.
- No metric guarantee: `metric_scale_factor` is a model estimate. Splats are a *visual* representation only;
  the collider GLB is approximate and is not used for measurement or collision.
- No camera model linking the reference photo to a live camera.
- No tracking, persistence or analytics.

## Decisions

1. **Reuse the asset format and renderer, not the app.** The viewer is tightly coupled to its own UI (character
   controller, Rapier physics, placement editor, butterflies, audio). Embedding it would drag all of that in.
   ECHO instead:
   - pins the same render stack (React 19 / R3F 9 / drei 10 / three 0.180 / Spark 2) so components are
     drop-in compatible;
   - ports `SplatRenderer` (minus the debug-store DOF shader patch) to
     `frontend/src/scene/ImageBlasterWorld.tsx`, keeping the exact `flip_y` / `ground_plane_offset` /
     `metric_scale_factor` transform so worlds render the same as in Image-Blaster;
   - loads the collider GLB with `useGLTF` as an optional wireframe overlay (visual aid only).
2. **Same on-disk layout.** ECHO's `worlds/` directory uses Image-Blaster's layout, so a world blasted in an
   Image-Blaster checkout can be copied into `ECHO/worlds/` unchanged. The backend (`echo/scenes/`) replaces the
   Vite plugin: it scans `worlds/*/output/world/N-world.json`, resolves local `.spz`/`.ply`/`.glb` files, and
   serves them under `/worlds/...`.
3. **Generation is invoked, not reimplemented.** `POST /api/scenes/generate` stages the uploaded reference
   image as `worlds/<slug>/source/0-<slug>.<ext>` and, only if `WORLD_LABS_API_KEY` is set, runs
   `third_party/image-blaster/.claude/scripts/world/generate-world.mjs --world <slug>` with cwd = ECHO root
   (the script reads `.env` and writes `worlds/<slug>` relative to cwd). Without a key the endpoint reports
   `unavailable` and the procedural fallback is used. Generation runs once per scene, never per frame.
4. **Separate authoritative spatial model.** Zones, floor bounds and calibration reference points live in
   `backend/echo/spatial/` (meters). The splat is aligned to it through an editable world-to-render transform
   (translation, yaw, scale) stored per scene config. Analytics never read the splat.
5. **Procedural fallback.** `procedural-kitchen` is built from the spatial model itself (floor, counters per
   zone), so it is metrically aligned by construction. It is the default scene.
6. **Test splat.** No Marble world was available (no API key). `scripts/make_test_splat.py` writes a synthetic
   Gaussian-splat `.ply` of the procedural kitchen into `worlds/synthetic-kitchen/` using Image-Blaster's file
   layout, to exercise the Spark loading path end to end. It is labeled synthetic in its `world.json`.

## Dependency / license risks

- **Ultralytics YOLO is AGPL-3.0.** Fine for internal development; commercial distribution needs an
  Ultralytics enterprise license or a swap to another detector. The detector is behind an interface for this
  reason.
- Spark 2.x requires WebGL2; `three` is pinned to 0.180 to match Image-Blaster's tested combination.
- World Labs and FAL are paid; neither is on the live perception path.
