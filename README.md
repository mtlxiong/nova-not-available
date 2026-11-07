# ECHO — real-time spatial digital twin

ECHO turns a camera feed (webcam or video file) into a live 3D twin of the space it sees. People and ArUco-tagged
objects are detected and tracked in the video, projected onto the floor plane through a calibration, and drawn as
moving entities, with trails, inside a 3D environment. Every observation is stored, so sessions can be replayed and
measured afterwards.

ECHO does not assume a type of space. A **workspace** (floor extent, labeled objects, zones, calibration) is
reconstructed from the camera footage itself:

1. **Background plate:** people are masked out across sampled frames and a per-pixel median gives a clean view
   of the empty space.
2. **Floor plane:** either a measured calibration (4+ clicked floor points with known positions) or an
   *approximate* auto-calibration that fits camera height and pitch so detected people come out ~1.70 m tall.
3. **Object labels:** an open-vocabulary detector (YOLO-World) labels static objects on the background plate. The
   default vocabulary covers about 60 indoor, retail, industrial, office, and outdoor terms; pass your own for a
   specific site. Floor footprints are projected through the calibration. Heights are measured against people
   standing nearby when possible. Any dimension that comes from a category default is marked as estimated.
4. **Activity zones** are *proposed* where people actually dwell (from track density and speed), named after the
   nearest detected object. You rename them, change their kind, delete them, or draw your own on the video.
5. **Floor texture:** the background plate is warped top-down onto the 3D floor.

A kitchen layout ships only as a read-only **template** that the labeled simulation uses. A fresh install starts
with a blank, unmapped workspace.

## Setup

Requirements: Python 3.11+, Node 20+. No paid APIs are needed for perception.

```bash
# backend
cd backend
python3 -m venv .venv
.venv/bin/pip install -e '.[yolo,dev]'
.venv/bin/pip install -e '.[world]'      # optional: open-vocabulary labeling (otherwise COCO classes only)
mkdir -p models
.venv/bin/python -c "from ultralytics import YOLO; YOLO('yolo11n.pt')" && mv yolo11n.pt models/
.venv/bin/python -c "from ultralytics import YOLO; YOLO('yolov8s-worldv2.pt')" && mv yolov8s-worldv2.pt models/

# frontend
cd ../frontend
npm install

# sample video (OpenCV sample data)
mkdir -p ../data/samples
curl -L -o ../data/samples/vtest.avi https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/vtest.avi
```

## Run

```bash
cd backend && MPLCONFIGDIR=/tmp/echo-mpl .venv/bin/python -m uvicorn echo.api.app:create_app --factory --host 127.0.0.1 --port 8000
cd frontend && npm run dev          # http://127.0.0.1:5180
```

Or build once (`npm run build`) and open http://127.0.0.1:8000, where the backend serves `frontend/dist`.

### Mapping a new space

1. Top bar: **Video file → Upload…** (or choose **Webcam**).
2. Right panel, **Workspace** tab: pick the video, or **Running session** for a live camera, then **Analyze**.
   Use a measured calibration if you have one; otherwise auto-calibration is used and labeled *approximate*.
3. Check the background plate, notes, and warnings, then click **Use this workspace**. If a session is running,
   its zones and calibration are swapped in live.
4. Fix labels: rename objects, delete false detections, rename or re-kind zones, or **Draw zone on video** (needs a
   calibration). Then **Save changes**.
5. **Start** a session. Tracked people move on the reconstructed floor; zone entries and exits use your zones.

Headless equivalent: `backend/.venv/bin/python backend/scripts/analyze_workspace.py data/samples/vtest.avi`.
It writes a debug overlay and the floor texture to `/tmp/echo-analysis`.

### Optional: photoreal 3D world (Image-Blaster)

The **Generate 3D world from this view** button sends the people-free background plate to
[Image-Blaster](https://github.com/neilsonnn/image-blaster) once (World Labs Marble, paid,
`WORLD_LABS_API_KEY`). The result becomes a selectable scene. It is a visual backdrop only; tracking never depends
on it. See [docs/image-blaster-integration.md](docs/image-blaster-integration.md).

## Tests

```bash
cd backend && MPLCONFIGDIR=/tmp/echo-mpl .venv/bin/python -m pytest      # 60 tests
cd frontend && npm test && npm run typecheck && npm run build              # 14 tests
```

The backend suite includes a synthetic-camera recovery test for auto-calibration and an end-to-end analysis of
`vtest.avi`, which is skipped when the video or weights are missing.

## Measured (this machine: macOS arm64, CPU inference, `yolo11n`)

| Run | Result |
| --- | --- |
| `vtest.avi` 768×576, live UI session | 10 fps processed, YOLO ~48–52 ms, end-to-end (capture → browser) ~61 ms, 0 dropped |
| Webcam 1920×1080 | 30 fps capture, YOLO ~38 ms, pipeline ~47 ms |
| Workspace analysis of `vtest.avi` (80 frames) | ~21 s; 14 objects → 12 floor fixtures, 8 proposed zones |
| vtest auto-calibration | camera ~5.2 m high, 27° pitch; estimated position error ~0.73 m RMS (approximate, not surveyed) |

These numbers come from single runs and are not benchmarks.

## Limits (read these)

- Auto-calibration assumes a level, distortion-free camera, a guessed field of view (65° default), and an average
  person height. Its scale error is several percent of the distance to the camera. For real measurements, calibrate
  with measured floor points.
- Object labels come from an open-vocabulary detector with a low threshold (0.15). Expect some wrong or junk labels;
  that is why every label can be edited and every proposed zone is marked `proposed`.
- Object depth (and sometimes width) cannot be seen from one camera. It comes from category priors and is flagged in
  `estimated_dims` and shown with a `*` in the UI.
- Only floor-standing objects are placed. Windows, wall signs, and similar objects are skipped, with a note.
- YOLO-World weights and Ultralytics are AGPL-3.0. Use the COCO-only fallback (no `[world]` extra) if that matters.
- ECHO does not identify people. No face recognition, and no per-person productivity metrics. Tracks are anonymous IDs.

Architecture: [docs/architecture.md](docs/architecture.md).
