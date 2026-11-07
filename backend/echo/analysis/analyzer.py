"""Workspace analysis job: footage in, spatial model (floor, fixtures, zones, texture) out.

Steps: gather frames + person detections -> background plate (people removed) -> ground-plane
calibration (supplied, or approximate auto-estimate from person heights) -> open-vocabulary
labeling of static objects on the plate -> fixtures + activity zones + floor texture.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from echo.analysis import autocal, layout
from echo.analysis.background import BackgroundBuilder
from echo.analysis.objects import StaticObjectDetector
from echo.analysis.samples import PersonBox
from echo.config import Settings
from echo.memory import store
from echo.memory.db import Database, new_id
from echo.schemas import CalibrationResult, FloorTexture, SpatialModel, WorkspaceAnalysisRequest
from echo.spatial.calibration import GroundPlaneProjector

log = logging.getLogger("echo.analysis")


class AnalysisError(RuntimeError):
    pass


@dataclass
class AnalysisJob:
    job_id: str
    request: WorkspaceAnalysisRequest
    state: str = "running"
    stage: str = "queued"
    progress: float = 0.0
    error: str | None = None
    started_wall_ms: float = field(default_factory=lambda: time.time() * 1000)
    result: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "state": self.state,
            "stage": self.stage,
            "progress": round(self.progress, 3),
            "error": self.error,
            "result": self.result,
        }


@dataclass
class Observations:
    frames: list[np.ndarray]
    people: list[PersonBox]
    image_size: tuple[int, int]
    camera_id: str
    source_label: str
    frame_people: list[list[tuple[float, float, float, float]]]
    """Person boxes per frame in ``frames`` coordinates (for background masking)."""


def sample_video(path: Path, max_frames: int, detect_people) -> tuple[list[np.ndarray], list[list[PersonBox]], tuple[int, int], float]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise AnalysisError(f"cannot open video {path.name}")
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0) or 30.0
        if total <= 0:
            raise AnalysisError("video reports no frames")
        targets = np.unique(np.linspace(0, total - 1, min(max_frames, total)).astype(int))
        frames: list[np.ndarray] = []
        people: list[list[PersonBox]] = []
        size = None
        seek = total > 5000
        idx = -1
        for target in targets:
            if seek:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(target))
                ok, img = cap.read()
                idx = target
            else:
                ok, img = True, None
                while idx < target and ok:
                    ok = cap.grab()
                    idx += 1
                if ok:
                    ok, img = cap.retrieve()
            if not ok or img is None:
                continue
            size = (img.shape[1], img.shape[0])
            t_ms = int(round(idx / fps * 1000))
            frames.append(img)
            people.append(detect_people(img, t_ms))
        if not frames or size is None:
            raise AnalysisError("could not decode any frames")
        return frames, people, size, total / fps
    finally:
        cap.release()


class WorkspaceAnalyzer:
    def __init__(self, settings: Settings, db: Database, session_manager=None):
        self.settings = settings
        self.db = db
        self.manager = session_manager
        self.objects = StaticObjectDetector(settings.yolo_world_weights, settings.yolo_weights)
        self._person_model = None
        self._jobs: dict[str, AnalysisJob] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------------------ jobs
    def start(self, request: WorkspaceAnalysisRequest) -> AnalysisJob:
        with self._lock:
            if any(j.state == "running" for j in self._jobs.values()):
                raise AnalysisError("an analysis is already running")
            job = AnalysisJob(new_id("ana"), request)
            self._jobs[job.job_id] = job
        threading.Thread(target=self._run, args=(job,), name=f"analysis-{job.job_id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> AnalysisJob | None:
        return self._jobs.get(job_id)

    def run_sync(self, request: WorkspaceAnalysisRequest) -> dict[str, Any]:
        job = AnalysisJob(new_id("ana"), request)
        self._run(job)
        if job.error:
            raise AnalysisError(job.error)
        assert job.result is not None
        return job.result

    def _stage(self, job: AnalysisJob, stage: str, progress: float) -> None:
        job.stage, job.progress = stage, progress
        log.info("analysis %s: %s", job.job_id, stage)

    def _run(self, job: AnalysisJob) -> None:
        try:
            job.result = self._analyze(job)
            job.state, job.stage, job.progress = "done", "done", 1.0
        except (AnalysisError, autocal.AutoCalibrationError) as exc:
            job.state, job.error = "error", str(exc)
        except Exception as exc:  # pragma: no cover - surfaced to the UI
            log.exception("analysis failed")
            job.state, job.error = "error", f"analysis failed: {exc}"

    # ------------------------------------------------------------------------------ steps
    def _detect_people(self, image: np.ndarray, t_ms: int) -> list[PersonBox]:
        if self._person_model is None:
            from ultralytics import YOLO

            self._person_model = YOLO(str(self.settings.yolo_weights))
        res = self._person_model.predict(image, conf=0.3, classes=[0], device="cpu", verbose=False)[0]
        return [PersonBox(t_ms, None, *map(float, b), float(c)) for b, c in zip(res.boxes.xyxy.cpu().numpy(), res.boxes.conf.cpu().numpy())]

    def _gather(self, job: AnalysisJob) -> Observations:
        req = job.request
        if req.video_id:
            video = store.get_video(self.db, req.video_id)
            if video is None or not video.path:
                raise AnalysisError(f"unknown video {req.video_id}")
            self._stage(job, "sampling frames and detecting people", 0.05)
            frames, per_frame, size, duration_s = sample_video(Path(video.path), req.max_frames, self._detect_people)
            people = [p for ps in per_frame for p in ps]
            people.extend(self._session_people(req.video_id, size))
            return Observations(frames, people, size, f"file-{video.id}", f"{video.original_name} ({duration_s:.0f} s)", [[p.xyxy for p in ps] for ps in per_frame])

        runner = self.manager.runner if self.manager else None
        if runner is None:
            raise AnalysisError("no video selected and no running session to analyze")
        self._stage(job, "reading live session samples", 0.05)
        frames_s, boxes, size = runner.scene_samples.snapshot()
        if size is None or not frames_s:
            raise AnalysisError("the live session has not produced frames yet")
        if len(frames_s) < 10:
            raise AnalysisError(f"only {len(frames_s)} s of footage buffered; let the session run at least 10 s")
        by_t: dict[int, list[tuple[float, float, float, float]]] = {}
        for b in boxes:
            by_t.setdefault(b.t_ms, []).append(b.xyxy)
        frames, frame_people = [], []
        for t_ms, img, scale in frames_s:
            if scale != 1.0:
                img = cv2.resize(img, size, interpolation=cv2.INTER_LINEAR)
            frames.append(img)
            frame_people.append(by_t.get(t_ms, []))
        camera_id = f"{runner.source.kind}-{self.manager.config.camera_index}" if runner.source.kind == "webcam" else f"live-{runner.session_id}"
        return Observations(frames, boxes, size, camera_id, f"live session {runner.session_id}", frame_people)

    def _session_people(self, video_id: str, size: tuple[int, int]) -> list[PersonBox]:
        """Tracked person boxes from earlier sessions on this video (adds dwell/speed information)."""
        from sqlalchemy import select

        from echo.memory.db import EntityRow, ObservationRow, SessionRow

        with self.db.Session() as s:
            sid = s.scalars(
                select(SessionRow.id).where(SessionRow.video_source_id == video_id, SessionRow.state == "stopped").order_by(SessionRow.created_wall_ms.desc())
            ).first()
            if sid is None:
                return []
            people_ids = set(s.scalars(select(EntityRow.entity_id).where(EntityRow.session_id == sid, EntityRow.class_name == "person")).all())
            rows = s.execute(
                select(ObservationRow.t_ms, ObservationRow.entity_id, ObservationRow.bbox, ObservationRow.confidence).where(
                    ObservationRow.session_id == sid, ObservationRow.state == "active"
                )
            ).all()
        out = []
        for t_ms, eid, bbox, conf in rows:
            if eid in people_ids and bbox:
                x1, y1, x2, y2 = bbox if isinstance(bbox, list) and len(bbox) == 4 else (bbox["x1"], bbox["y1"], bbox["x2"], bbox["y2"])
                out.append(PersonBox(int(t_ms), f"{sid}:{eid}", float(x1), float(y1), float(x2), float(y2), float(conf or 0.5)))
        return out

    def _analyze(self, job: AnalysisJob) -> dict[str, Any]:
        req = job.request
        obs = self._gather(job)
        w_img, h_img = obs.image_size
        notes: list[str] = []

        self._stage(job, "building background plate (removing people)", 0.35)
        bg = BackgroundBuilder(max_frames=len(obs.frames))
        for img, boxes in zip(obs.frames, obs.frame_people):
            bg.add(img, boxes)
        plate, never_clear = bg.build()
        if never_clear > 0.02:
            notes.append(f"{never_clear:.0%} of the image was never seen without a person; those pixels may contain ghosts")

        self._stage(job, "calibrating the floor plane", 0.5)
        samples = autocal.filter_person_boxes([(p.x1, p.y1, p.x2, p.y2, p.conf) for p in obs.people], w_img, h_img)
        camera: autocal.CameraModel | None = None
        if req.calibration_id:
            calibration = store.get_calibration(self.db, req.calibration_id)
            if calibration is None:
                raise AnalysisError(f"unknown calibration {req.calibration_id}")
        else:
            camera, quality = autocal.estimate_camera(samples, w_img, h_img, person_height_m=req.person_height_m, hfov_deg=req.hfov_deg)
            first = autocal.calibration_from_camera(camera, samples, quality, camera_id=obs.camera_id)
            pts = layout.people_on_floor(obs.people, GroundPlaneProjector(first, w_img, h_img))
            if len(pts) < 5:
                raise AnalysisError("auto-calibration produced no usable floor positions")
            xs, ys = np.array([p.x for p in pts]), np.array([p.y for p in pts])
            offset = (1.0 - float(np.percentile(xs, 1)), 1.0 - float(np.percentile(ys, 1)))
            calibration = autocal.calibration_from_camera(camera, samples, quality, camera_id=obs.camera_id, world_offset=offset)
            calibration = store.save_calibration(self.db, calibration)
            notes.append(
                f"Floor scale is approximate: camera ~{quality['camera_height_m']:.1f} m high, pitched {quality['pitch_deg']:.0f}° down, "
                f"assuming {req.person_height_m:.2f} m people and a {req.hfov_deg:.0f}° FOV"
            )
        projector = GroundPlaneProjector(calibration, w_img, h_img)

        self._stage(job, "labeling static objects", 0.65)
        objects = self.objects.detect(plate, req.vocabulary, confidence=req.object_confidence)
        vscale = layout.vertical_scale_fn(obs.people, req.person_height_m)
        fixtures, fixture_notes = layout.project_fixtures(objects, projector, (w_img, h_img), vscale)
        bx, by = calibration.image_width / w_img, calibration.image_height / h_img
        for f in fixtures:
            if f.image_bbox is not None:
                x1, y1, x2, y2 = f.image_bbox
                f.image_bbox = (round(x1 * bx, 1), round(y1 * by, 1), round(x2 * bx, 1), round(y2 * by, 1))
        notes.extend(fixture_notes)
        if vscale is None:
            notes.append("too few standing people to measure object heights; heights use category defaults")

        self._stage(job, "mapping floor and activity zones", 0.8)
        floor_pts = layout.people_on_floor(obs.people, projector)
        bounds = layout.floor_bounds(floor_pts, fixtures)
        if bounds is None:
            raise AnalysisError("no people or floor-standing objects were located on the floor; cannot size the workspace")
        weights = layout.dwell_weights(floor_pts)
        zones = layout.activity_zones(floor_pts, weights, bounds, fixtures)
        if not any(p.track for p in floor_pts):
            notes.append("zones are from person density in sampled frames (no tracking yet); run a session first to separate dwelling from walking")

        self._stage(job, "rendering floor texture", 0.9)
        model_id = req.model_id or new_id("ws")
        asset_dir = self.settings.data_dir / "workspaces" / model_id
        asset_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(asset_dir / "background.jpg"), plate, [cv2.IMWRITE_JPEG_QUALITY, 90])
        h_frame = np.array(calibration.homography) @ np.diag([calibration.image_width / w_img, calibration.image_height / h_img, 1.0])
        tex = layout.floor_texture(plate, h_frame, bounds)
        cv2.imwrite(str(asset_dir / "floor.png"), tex)
        stamp = int(time.time())

        model = SpatialModel(
            model_id=model_id,
            name=req.name or f"Workspace from {obs.source_label}",
            width_m=round(bounds.width, 3),
            depth_m=round(bounds.depth, 3),
            floor_polygon=[(round(x, 3), round(y, 3)) for x, y in bounds.polygon()],
            zones=zones,
            fixtures=fixtures,
            reference_points=[],
            source="analyzed",
            video_id=req.video_id,
            notes=notes,
            camera_id=obs.camera_id,
            calibration_id=calibration.calibration_id,
            floor_texture=FloorTexture(
                url=f"/workspace-assets/{model_id}/floor.png?v={stamp}",
                origin=(round(bounds.x0, 3), round(bounds.y0, 3)),
                size_m=(round(bounds.width, 3), round(bounds.depth, 3)),
            ),
            background_url=f"/workspace-assets/{model_id}/background.jpg?v={stamp}",
        )
        store.put_spatial_model(self.db, model)
        self._stage(job, "saved", 0.98)
        return {
            "model": model.model_dump(mode="json"),
            "calibration": calibration.model_dump(mode="json"),
            "stats": {
                "frames": len(obs.frames),
                "person_detections": len(obs.people),
                "standing_person_samples": len(samples),
                "floor_points": len(floor_pts),
                "tracked_points": sum(1 for p in floor_pts if p.track),
                "objects_detected": len(objects),
                "fixtures": len(fixtures),
                "zones": len(zones),
                "object_backend": self.objects.backend,
                "calibration_method": calibration.method,
                "never_clear_fraction": round(never_clear, 4),
            },
            "objects": [{"label": o.label, "confidence": round(o.confidence, 3), "bbox": o.bbox} for o in objects],
            "notes": notes,
        }
