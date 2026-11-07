# ECHO architecture

```
 camera / file ──► capture thread ──► latest-frame slot ──► process thread ─────────────────────────────┐
                    (never blocks)     (old frames dropped)   YOLO+ByteTrack, ArUco                     │
                                                              → GroundPlaneProjector (homography)       │
                                                              → EntityRegistry (states, zones)          │
                                                              → Recorder (SQLite)                       │
                                                              → SceneSampleBuffer (for analysis)        │
                                                                                                        ▼
 browser ◄── WebSocket hub ◄── tracking.update JSON + binary JPEG frame, event, session.status ◄────────┘
   React 19 · R3F · Zustand: VideoPanel (2D overlay) · TwinViewer (3D) · Timeline (replay) · panels
```

Backend: `backend/echo/`, FastAPI with one `SessionManager` that owns at most one `SessionRunner`.
Frontend: `frontend/src/`.

## Coordinates

- **Image:** pixels, origin top-left, in the calibration's `image_width × image_height`. Frames at another
  resolution are scaled before projection.
- **World:** meters, z-up, floor at z = 0. x and y are whatever the calibration defines. Analyzed workspaces put
  the origin about 1 m outside the 1st-percentile floor position of people, so coordinates are non-negative.
- **Render (three.js):** `(x, z, -y)`, then the scene `Alignment` (yaw, scale, translation). This is shared with
  the backend through `shared/alignment_vectors.json` and tested on both sides.

A position is only produced when a calibration exists and the image point is below the horizon. Otherwise the
position is `null` and nothing is placed in 3D. Positions are never invented or interpolated live.

## Tracking and states

`perception/detectors.py` runs YOLO (`yolo11n`, persons by default). `perception/trackers.py` runs ByteTrack, and
`perception/markers.py` handles ArUco. Each entity's ground-contact point is the bottom-center of its box.
`perception/entities.py` keeps these states:

- `active`: seen this frame.
- `lost`: not seen. The last position is kept, but flagged.
- `removed`: lost for 3 s or more.

Zone membership uses `ZoneTracker` with a 400 ms minimum dwell, as hysteresis against boundary flicker. The
frontend adds `stale` (no updates arriving) as a display state only.

Workspace zones can be replaced in a running session (`runner.set_zones`). The registry first closes current
memberships (emitting `zone.exit`), then evaluates the new zones from the next frame.

## Persistence ("GhostTrace") and replay

`memory/db.py` defines these tables:

- `sessions`, `video_sources`, `cameras`, `calibrations` (with `method`)
- `spatial_models` (with `source` and `active`), `zones` (with `provenance`)
- `scene_configs`
- `entities`, `observations` (every processed frame per entity, including null positions), `events`
- `orders`, `order_events`, `order_entity_links`

New columns are added by an additive `ALTER TABLE` migration at startup.

Replay (`memory/replay.py`) rebuilds movement from stored observations only. No computer vision is rerun, and
video frames are not stored. Interpolation happens only between two `active`, positioned samples.

Metrics (`analytics/metrics.py`) are computed per session against the workspace the session was recorded with
(`sessions.spatial_model_id`). Simulated sessions use the kitchen template. If the workspace has since been
deleted, metrics use the blank workspace (no zones) instead of silently using a different one.

## Workspaces (spatial models)

A `SpatialModel` holds:

- `floor_polygon`
- `zones`, each with `provenance`: template, detected, proposed, or manual
- `fixtures`: a free-form `kind`, an editable `label`, a `confidence`, `estimated_dims`, and an `image_bbox`
- an optional `calibration_id` and `camera_id`
- `floor_texture` and `background_url`

Each model also has a `source`:

| source | meaning |
| --- | --- |
| `blank` | built-in default, no layout |
| `template` | built-in kitchen used by the labeled simulation; read-only |
| `analyzed` | produced by workspace analysis |
| `manual` | user-created or edited copy |

One model is `active`. A non-simulated session uses the active model, or the one passed as
`SessionConfig.spatial_model_id`. If no calibration is given and the model's `camera_id` matches, the session uses
the model's calibration.

### Workspace analysis (`echo/analysis/`)

`WorkspaceAnalyzer` runs as a background job (`POST /api/workspaces/analyze`, then poll
`GET /api/workspaces/analyze/{job}`). It runs these stages:

1. **Gather** (`analyzer._gather`):
   - From a video file: up to 80 evenly sampled frames, with YOLO person boxes. If a stopped session already
     exists on that video, its *tracked* boxes are used too, so dwell can be separated from walking.
   - From a running session: the runner's `SceneSampleBuffer`, which holds about one frame per second (max 60)
     plus every person box with its track ID.
2. **Background plate** (`background.py`): each frame's person boxes (plus padding) are masked, then a per-pixel
   `nanmedian` is taken. The analysis reports the fraction of pixels never seen clear.
3. **Calibration** (`autocal.py`), when no measured calibration is given:
   - Camera model: pinhole, zero roll, principal point at the image center, focal length from the assumed
     horizontal FOV.
   - People are filtered first: confidence ≥ 0.4, height ≥ 24 px, height/width ≥ 1.6, not touching the frame edge.
   - Grid search over pitch (0–80°) and camera height (0.8–15 m), then a refinement pass. It minimizes the trimmed
     relative error between predicted and observed head rows for a person of the assumed height standing at each
     foot point.
   - Requirements: ≥ 15 people samples, and their foot rows must span ≥ 12% of the image height.
   - Ground homography: `H_w2i = K · [[1,0,0],[0,−s,c·h],[0,c,s·h]]` (s, c = sin and cos of pitch, h = camera
     height), inverted, then offset in world coordinates.
   - Uncertainty comes from the spread of implied person heights, with a 5% floor. The result is stored with
     `method = "auto-person-height"` and warnings, and is shown as approximate everywhere.
4. **Objects** (`objects.py`):
   - YOLO-World `yolov8s-worldv2` runs on the plate at 1280 px with a configurable vocabulary. Without CLIP or the
     weights, it falls back to COCO YOLO static classes.
   - Boxes nested inside a same-label box are suppressed.
5. **Fixtures** (`layout.project_fixtures`):
   - The box's bottom corners are projected to the floor, which gives width and yaw.
   - Depth comes from category priors. Vehicles get an orientation-aware length.
   - Height comes from a vertical pixels-per-meter model fitted to standing people at similar image rows, falling
     back to a prior.
   - Thin uprights (poles, tripods) take their width from priors.
   - Labels that are usually off the floor (windows, wall signs) are skipped, with a note.
   - Everything assumed is listed in `estimated_dims`. `image_bbox` is stored in calibration pixels for the video
     overlay.
6. **Floor bounds:** the 1st–99th percentile of people and fixture positions, plus 1 m, clamped to 3–40 m per
   side.
7. **Activity zones** (`layout.activity_zones`):
   - A person-occupancy grid (0.4 m cells), weighted toward slow (< 0.35 m/s, i.e. dwelling) tracked points, then
     Gaussian-blurred.
   - Threshold at the 80th percentile of occupied cells, take connected components, and use each component's
     convex hull as the zone.
   - Zones are named after the nearest fixture within 2 m, else "Activity area N". All have `provenance="proposed"`.
8. **Floor texture:** the plate is warped top-down through the homography (`WARP_INVERSE_MAP`) into a BGRA PNG
   covering the floor bounds. Areas the camera can't see are transparent.

Assets go to `data/workspaces/<model_id>/` (`background.jpg`, `floor.png`) and are served at `/workspace-assets/`.
The model is saved but **not** activated; the user reviews it and clicks "Use this workspace".

`POST /api/workspaces/{id}/generate-world` stages the plate as the Image-Blaster reference image (one paid
generation; never per frame).

### Accuracy, as tested

- **Synthetic camera** (`tests/test_analysis.py`): pitch recovered within 1.5°, height within 8%, median relative
  floor-position error < 6%, with noisy people heights.
- **`vtest.avi`:** camera ~5.2 m, pitched 27°. The floor texture comes out rectilinear (parking bays look
  rectangular). Measured heights: traffic cones 0.41–0.57 m, van 1.9 m, tripod 1.5 m. Detection still produces
  low-confidence junk labels at the default 0.15 threshold.
- There is no ground-truth survey for `vtest.avi`, so metric accuracy on real footage is **not** verified. The
  displayed ±0.73 m is the model's own estimate.

## API summary

| Route | Purpose |
| --- | --- |
| `POST /api/sessions/start` · `POST /api/sessions/stop` · `GET /api/sessions/current` | live session control |
| `GET /api/sessions` · `/{id}/replay` · `/{id}/metrics` · `/{id}/model` | stored sessions |
| `GET/PUT /api/spatial-model` | workspace shown now (running session's, else active) / save |
| `GET /api/spatial-models` · `GET/DELETE /api/spatial-models/{id}` · `POST /{id}/activate` | workspace library |
| `POST /api/workspaces/analyze` · `GET /api/workspaces/analyze/{job}` | analysis jobs |
| `POST /api/calibrations` · `/preview` · `GET /api/calibrations` | measured calibration |
| `GET /api/scenes` · `PUT /api/scenes/{id}/config` · `POST /api/scenes/generate` | 3D scenes, Image-Blaster |
| `WS /ws` | `tracking.update`, `event`, `session.status` JSON + binary JPEG frames |

## Frontend

- **State:** `state/twin.ts` holds entities, trails, events, and live vs. replay mode. `state/ui.ts` holds the
  scene, calibration, workspace draft, and replay state.
- **`TwinViewer`:**
  - `WorkspaceEnvironment` draws the floor polygon, the warped floor texture, fixtures (colored by provenance,
    translucent when dimensions are estimated, with labels), and walls only for template or manual layouts.
  - `ZonesLayer` and `EntitiesLayer` draw zones and entities.
  - An optional Image-Blaster splat replaces the procedural floor when a generated world is selected.
- **`VideoPanel`:** draws boxes, zone outlines projected through the calibration, the detected objects'
  `image_bbox`, the calibration click workflow, and the zone-drawing workflow (clicks go image → world through the
  active homography).
- **`WorkspacePanel`:** analyze, review, use, edit labels and zones, save or save-as, delete, and generate a
  world.
