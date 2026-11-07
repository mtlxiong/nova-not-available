"""Owns the (single) active session: builds sources/perception from config, wires publishing."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from echo.config import Settings
from echo.memory.db import Database, new_id
from echo.memory.recorder import SessionRecorder
from echo.memory import store
from echo.perception.detectors import Detector, NullDetector, SimulationDetector, YoloDetector
from echo.perception.entities import EntityRegistry
from echo.perception.markers import ArucoMarkerDetector, MarkerRegistry, default_marker_registry
from echo.perception.trackers import ByteTrackTracker, IouTracker, Tracker
from echo.pipeline.runner import RunnerOptions, SessionRunner
from echo.schemas import (
    CalibrationResult,
    EntitySource,
    EventKind,
    SessionConfig,
    SessionStatusMessage,
    SourceKind,
    SpatialEvent,
    SpatialModel,
)
from echo.spatial.calibration import calibration_from_homography
from echo.spatial.kitchen import default_kitchen
from echo.video.simulation import SimulationSource
from echo.video.sources import FileSource, VideoSource, VideoSourceError, WebcamSource

log = logging.getLogger("echo.manager")


class SessionStartError(RuntimeError):
    pass


def build_source(config: SessionConfig, settings: Settings, db: Database, model: SpatialModel) -> tuple[VideoSource, str | None, str]:
    """Returns (source, video_source_id, camera_id)."""
    if config.source == SourceKind.SIMULATION:
        src = SimulationSource(
            model,
            fps=config.inference_fps,
            width=config.width or 1280,
            height=config.height or 720,
            entities=config.simulation_entities,
            duration_s=config.simulation_duration_s,
            seed=config.simulation_seed,
        )
        return src, None, "sim-camera"
    if config.source == SourceKind.WEBCAM:
        return WebcamSource(config.camera_index, config.width, config.height, config.capture_fps), None, f"webcam-{config.camera_index}"
    if not config.file_id:
        raise SessionStartError("file source requires file_id (upload a video first)")
    video = store.get_video(db, config.file_id)
    if video is None or not video.path:
        raise SessionStartError(f"unknown video {config.file_id}")
    return FileSource(Path(video.path), sample_fps=config.inference_fps, max_width=config.width), video.id, f"file-{video.id}"


def build_detector(config: SessionConfig, settings: Settings) -> Detector:
    if config.source == SourceKind.SIMULATION:
        return SimulationDetector(seed=config.simulation_seed)
    if config.detector == "none":
        return NullDetector()
    return YoloDetector(settings.yolo_weights, config.classes, confidence=config.confidence)


def build_tracker(config: SessionConfig) -> Tracker:
    source = EntitySource.SIMULATION if config.source == SourceKind.SIMULATION else EntitySource.YOLO
    if config.tracker == "iou":
        return IouTracker(source)
    return ByteTrackTracker(source, frame_rate=config.inference_fps)


class SessionManager:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        publish_json: Callable[[str], None],
        publish_frame: Callable[[bytes], None],
        wants_frames: Callable[[], bool],
        marker_registry: MarkerRegistry | None = None,
    ):
        self.settings = settings
        self.db = db
        self._publish_json = publish_json
        self._publish_frame = publish_frame
        self._wants_frames = wants_frames
        self.marker_registry = marker_registry or default_marker_registry()
        self._lock = threading.RLock()
        self.runner: SessionRunner | None = None
        self.config: SessionConfig | None = None
        self.model: SpatialModel | None = None
        self.last_status = SessionStatusMessage(session_id=None, state="idle")

    # ------------------------------------------------------------------------------ status
    def _status(self, state, message: str | None = None) -> SessionStatusMessage:
        r = self.runner
        status = SessionStatusMessage(
            session_id=r.session_id if r else None,
            state=state,
            source_kind=self.config.source.value if self.config else None,
            simulated=bool(r and r.source.simulated),
            calibrated=bool(r and r.calibration is not None),
            message=message,
            spatial_model_id=self.model.model_id if r and self.model else None,
        )
        self.last_status = status
        self._publish_json(status.model_dump_json())
        return status

    def status(self) -> SessionStatusMessage:
        return self.last_status

    # ------------------------------------------------------------------------------ lifecycle
    def start(self, config: SessionConfig) -> SessionStatusMessage:
        with self._lock:
            if self.runner is not None and not self.runner.wait(0):
                raise SessionStartError("a session is already running; stop it first")
            if config.source == SourceKind.SIMULATION:
                model = default_kitchen()  # the simulation's world is the labeled kitchen template
            else:
                matched = store.model_for_video(self.db, config.file_id) if config.source == SourceKind.FILE and config.file_id else None
                try:
                    model = matched or store.get_spatial_model(self.db, config.spatial_model_id)
                except KeyError as exc:
                    raise SessionStartError(f"unknown workspace {config.spatial_model_id}") from exc
            source, video_id, camera_id = build_source(config, self.settings, self.db, model)
            if config.calibration_id is None and model.calibration_id and model.camera_id == camera_id:
                config = config.model_copy(update={"calibration_id": model.calibration_id})
            try:
                source.open()
            except VideoSourceError as exc:
                raise SessionStartError(str(exc)) from exc
            try:
                detector = build_detector(config, self.settings)
                tracker = build_tracker(config)
            except Exception as exc:
                source.close()
                raise SessionStartError(f"perception setup failed: {exc}") from exc

            calibration = self._resolve_calibration(config, source, model, camera_id)
            store.ensure_camera(self.db, camera_id, kind=source.kind, device_index=config.camera_index if config.source == SourceKind.WEBCAM else None, width=source.width, height=source.height)
            session_id = new_id("ses")
            store.create_session(
                self.db,
                session_id=session_id,
                config=config,
                source_kind=source.kind,
                simulated=source.simulated,
                spatial_model_id=model.model_id,
                calibration_id=calibration.calibration_id if calibration else None,
                video_source_id=video_id,
                camera_id=camera_id,
            )
            registry = EntityRegistry(session_id, model.zones, self.marker_registry, lost_timeout_ms=config.lost_timeout_ms)
            recorder = SessionRecorder(self.db, session_id, observation_interval_ms=config.observation_interval_ms)
            recorder.add_events(
                [
                    SpatialEvent(
                        session_id=session_id,
                        timestamp_ms=0,
                        kind=EventKind.SESSION_STARTED,
                        data={"source": source.describe(), "simulated": source.simulated, "calibration_id": calibration.calibration_id if calibration else None},
                    )
                ]
            )
            self.config = config
            self.model = model
            self.runner = SessionRunner(
                session_id=session_id,
                source=source,
                detector=detector,
                tracker=tracker,
                registry=registry,
                recorder=recorder,
                marker_detector=ArucoMarkerDetector() if config.enable_aruco else None,
                calibration=calibration,
                options=RunnerOptions(inference_fps=config.inference_fps, realtime=config.realtime, stream_frames=config.stream_frames),
                publish_json=self._publish_json,
                publish_frame=self._publish_frame,
                wants_frames=self._wants_frames,
                on_finished=self._on_finished,
            )
            self.runner.start()
            log.info("session started %s source=%s simulated=%s calibrated=%s", session_id, source.kind, source.simulated, calibration is not None)
            return self._status("running")

    def _resolve_calibration(self, config: SessionConfig, source: VideoSource, model: SpatialModel, camera_id: str) -> CalibrationResult | None:
        if config.calibration_id:
            cal = store.get_calibration(self.db, config.calibration_id)
            if cal is None:
                raise SessionStartError(f"unknown calibration {config.calibration_id}")
            return cal
        if isinstance(source, SimulationSource):
            world_pts = [(p.x, p.y) for p in model.reference_points]
            cal = calibration_from_homography(
                source.homography,
                source.width,
                source.height,
                world_pts,
                camera_id=camera_id,
                simulated=True,
                label="simulated camera (known homography)",
            )
            return store.save_calibration(self.db, cal)
        return None

    def stop(self) -> SessionStatusMessage:
        with self._lock:
            runner = self.runner
            if runner is None:
                return self._status("idle")
            if runner.wait(0):
                return self._closed_status(runner)
            self._status("stopping")
        runner.stop()
        finished = runner.wait(10)
        with self._lock:
            if self.runner is not runner:
                return self.last_status
            if finished and self.last_status.state != "stopping":
                return self.last_status
            if not finished:
                log.warning("session %s did not stop within timeout; releasing it", runner.session_id)
                status = self._status("stopped", "stop timed out; session released")
                self.runner = None
                return status
            return self._closed_status(runner)

    def _closed_status(self, runner: SessionRunner) -> SessionStatusMessage:
        state = "error" if runner.result.error else "stopped"
        return self._status(state, runner.result.error)

    def _on_finished(self, runner: SessionRunner) -> None:
        with self._lock:
            if self.runner is not runner:
                return
            self._closed_status(runner)

    def set_calibration(self, calibration_id: str | None) -> SessionStatusMessage:
        with self._lock:
            if self.runner is None or self.runner.wait(0):
                raise SessionStartError("no running session")
            cal = store.get_calibration(self.db, calibration_id) if calibration_id else None
            if calibration_id and cal is None:
                raise SessionStartError(f"unknown calibration {calibration_id}")
            self.runner.set_calibration(cal)
            store.set_session_calibration(self.db, self.runner.session_id, calibration_id)
            return self._status("running", "calibration applied" if cal else "calibration cleared")

    def running(self) -> bool:
        return self.runner is not None and not self.runner.wait(0)

    def current_model(self) -> SpatialModel:
        """The running session's workspace, else the active one."""
        if self.running() and self.model is not None:
            return self.model
        return store.get_spatial_model(self.db)

    def apply_workspace(self, model: SpatialModel) -> SessionStatusMessage | None:
        """Hot-swap a (re)mapped workspace into the running session: zones, and its calibration
        when the workspace was built for this session's camera."""
        with self._lock:
            if not self.running() or self.config is None or self.config.source == SourceKind.SIMULATION:
                return None
            assert self.runner is not None
            self.model = model
            self.runner.set_zones(model.zones)
            message = "workspace applied"
            if model.calibration_id:
                cal = store.get_calibration(self.db, model.calibration_id)
                if cal is not None:
                    self.runner.set_calibration(cal)
                    store.set_session_calibration(self.db, self.runner.session_id, cal.calibration_id)
                    message = "workspace and its calibration applied"
            return self._status("running", message)

    def shutdown(self) -> None:
        if self.runner is not None and not self.runner.wait(0):
            self.runner.stop()
            self.runner.wait(10)
