"""Threaded capture -> perception -> spatial -> memory -> publish pipeline for one session."""

from __future__ import annotations

import json
import logging
import queue
import struct
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

import cv2

from echo.analysis.samples import PersonBox, SceneSampleBuffer
from echo.memory.recorder import SessionRecorder
from echo.perception.detectors import Detector
from echo.perception.entities import EntityRegistry
from echo.perception.markers import ArucoMarkerDetector
from echo.perception.trackers import Tracker
from echo.schemas import CalibrationResult, EventKind, EventMessage, PerfStats, SpatialEvent, TrackingUpdate, Zone
from echo.spatial.calibration import GroundPlaneProjector
from echo.video.sources import Frame, VideoSource

log = logging.getLogger("echo.pipeline")

_SENTINEL = object()
FRAME_HEADER = struct.Struct("<II")
"""Binary frame message header: frame_index, timestamp_ms (little-endian uint32), followed by JPEG bytes."""


class _Rate:
    """Exponential moving average of an event rate in Hz."""

    def __init__(self, alpha: float = 0.1):
        self.alpha = alpha
        self.value = 0.0
        self._last: float | None = None

    def tick(self, now: float) -> None:
        if self._last is not None:
            dt = now - self._last
            if dt > 0:
                inst = 1.0 / dt
                self.value = inst if self.value == 0 else self.value + self.alpha * (inst - self.value)
        self._last = now


class _Ema:
    def __init__(self, alpha: float = 0.1):
        self.alpha = alpha
        self.value = 0.0

    def add(self, x: float) -> None:
        self.value = x if self.value == 0 else self.value + self.alpha * (x - self.value)


@dataclass
class RunnerOptions:
    inference_fps: float = 10.0
    realtime: bool = True
    queue_size: int = 2
    stream_frames: bool = True
    stream_max_fps: float = 15.0
    stream_max_width: int = 960
    jpeg_quality: int = 70
    perf_log_interval_s: float = 5.0


@dataclass
class RunnerResult:
    state: str = "running"
    error: str | None = None
    processed_frames: int = 0
    last_timestamp_ms: int = 0
    dropped_frames: int = 0
    events: list[SpatialEvent] = field(default_factory=list)


class SessionRunner:
    def __init__(
        self,
        *,
        session_id: str,
        source: VideoSource,
        detector: Detector,
        tracker: Tracker,
        registry: EntityRegistry,
        recorder: SessionRecorder | None,
        marker_detector: ArucoMarkerDetector | None = None,
        calibration: CalibrationResult | None = None,
        options: RunnerOptions | None = None,
        publish_json: Callable[[str], None] | None = None,
        publish_frame: Callable[[bytes], None] | None = None,
        wants_frames: Callable[[], bool] | None = None,
        on_finished: Callable[["SessionRunner"], None] | None = None,
        keep_events: bool = False,
    ):
        self.session_id = session_id
        self.source = source
        self.detector = detector
        self.tracker = tracker
        self.registry = registry
        self.recorder = recorder
        self.marker_detector = marker_detector
        self.options = options or RunnerOptions()
        self._publish_json = publish_json or (lambda _m: None)
        self._publish_frame = publish_frame or (lambda _b: None)
        self._wants_frames = wants_frames or (lambda: True)
        self._on_finished = on_finished
        self._keep_events = keep_events

        self._queue: queue.Queue = queue.Queue(maxsize=self.options.queue_size)
        self._stop = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._process_thread: threading.Thread | None = None
        self._calibration_lock = threading.Lock()
        self._calibration = calibration
        self._projector: GroundPlaneProjector | None = None
        self._projector_dims: tuple[int, int] | None = None

        self.result = RunnerResult()
        self._capture_rate = _Rate()
        self._process_rate = _Rate()
        self._inference_ms = _Ema()
        self._tracking_ms = _Ema()
        self._pipeline_ms = _Ema()
        self._last_stream = 0.0
        self._last_perf_log = time.monotonic()
        self._finished = threading.Event()
        self.recent_updates: deque[TrackingUpdate] = deque(maxlen=1)
        self.scene_samples = SceneSampleBuffer()
        self._pending_zones: list[Zone] | None = None

    # ----------------------------------------------------------------------------- control
    def start(self) -> None:
        self._capture_thread = threading.Thread(target=self._capture_loop, name=f"capture-{self.session_id}", daemon=True)
        self._process_thread = threading.Thread(target=self._process_loop, name=f"process-{self.session_id}", daemon=True)
        self._process_thread.start()
        self._capture_thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        for t in (self._capture_thread, self._process_thread):
            if t is not None and t.is_alive() and t is not threading.current_thread():
                t.join(timeout)

    def wait(self, timeout: float | None = None) -> bool:
        return self._finished.wait(timeout)

    @property
    def calibration(self) -> CalibrationResult | None:
        with self._calibration_lock:
            return self._calibration

    def set_calibration(self, calibration: CalibrationResult | None) -> None:
        with self._calibration_lock:
            self._calibration = calibration
            self._projector = None
            self._projector_dims = None

    def set_zones(self, zones: list[Zone]) -> None:
        """Swap the workspace's zones; applied on the next processed frame."""
        with self._calibration_lock:
            self._pending_zones = list(zones)

    def _get_projector(self, width: int, height: int) -> GroundPlaneProjector | None:
        with self._calibration_lock:
            if self._calibration is None:
                return None
            if self._projector is None or self._projector_dims != (width, height):
                self._projector = GroundPlaneProjector(self._calibration, width, height)
                self._projector_dims = (width, height)
            return self._projector

    # ----------------------------------------------------------------------------- capture
    def _capture_loop(self) -> None:
        opts = self.options
        min_interval = 1.0 / opts.inference_fps if opts.inference_fps > 0 else 0.0
        next_due = 0.0
        wall0: float | None = None
        media0: int | None = None
        try:
            while not self._stop.is_set():
                frame = self.source.read()
                if frame is None:
                    break
                now = time.monotonic()
                self._capture_rate.tick(now)
                if self.source.is_live:
                    # Fixed schedule rather than "interval since last": with a 30 fps camera and a
                    # 10 fps target, the latter only fires every 4th frame (7.5 fps).
                    if now < next_due:
                        continue
                    next_due = max(next_due + min_interval, now - min_interval)
                    frame.capture_wall_ms = time.time() * 1000.0
                    self._put_dropping_oldest(frame)
                else:
                    if opts.realtime:
                        if wall0 is None:
                            wall0, media0 = now, frame.timestamp_ms
                        delay = wall0 + (frame.timestamp_ms - media0) / 1000.0 - time.monotonic()
                        if delay > 0 and self._stop.wait(delay):
                            break
                        frame.capture_wall_ms = time.time() * 1000.0
                    self._put_blocking(frame)
        except Exception as exc:  # hardware unplugged, decode failure, ...
            log.exception("capture failed", extra={"session_id": self.session_id})
            self.result.error = f"capture failed: {exc}"
        finally:
            self._put_blocking(_SENTINEL, force=True)

    def _put_dropping_oldest(self, item) -> None:
        while True:
            try:
                self._queue.put_nowait(item)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self.result.dropped_frames += 1
                except queue.Empty:
                    pass

    def _put_blocking(self, item, force: bool = False) -> None:
        while True:
            if self._stop.is_set() and force:
                # drain so the sentinel always fits
                try:
                    self._queue.put_nowait(item)
                    return
                except queue.Full:
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        pass
                    continue
            if self._stop.is_set() and not force:
                return
            try:
                self._queue.put(item, timeout=0.1)
                return
            except queue.Full:
                continue

    # ----------------------------------------------------------------------------- processing
    def _process_loop(self) -> None:
        last_t = 0
        try:
            while True:
                try:
                    item = self._queue.get(timeout=0.25)
                except queue.Empty:
                    if self._stop.is_set() and not (self._capture_thread and self._capture_thread.is_alive()):
                        break
                    continue
                if item is _SENTINEL:
                    break
                frame: Frame = item
                self._process_frame(frame)
                last_t = frame.timestamp_ms
            self.result.state = "error" if self.result.error else "stopped"
        except Exception as exc:
            log.exception("processing failed", extra={"session_id": self.session_id})
            self.result.state = "error"
            self.result.error = f"processing failed: {exc}"
        finally:
            self._finish(last_t)

    def _process_frame(self, frame: Frame) -> None:
        t0 = time.perf_counter()
        detections = self.detector.detect(frame)
        t1 = time.perf_counter()
        tracks = self.tracker.update(detections, frame)
        if self.marker_detector is not None:
            tracks.extend(self.marker_detector.detect(frame))
        t2 = time.perf_counter()

        h, w = frame.image.shape[:2]
        projector = self._get_projector(w, h)
        people = [
            PersonBox(frame.timestamp_ms, t.track_id, t.bbox.x1, t.bbox.y1, t.bbox.x2, t.bbox.y2, t.confidence)
            for t in tracks
            if t.class_name == "person" and t.bbox is not None
        ]
        self.scene_samples.add(frame.timestamp_ms, frame.image, people)
        zone_events: list[SpatialEvent] = []
        with self._calibration_lock:
            pending, self._pending_zones = self._pending_zones, None
        if pending is not None:
            zone_events = self.registry.set_zones(pending, frame.timestamp_ms)
        entities, events = self.registry.update(tracks, frame.timestamp_ms, projector)
        events = zone_events + events
        if self._keep_events:
            self.result.events.extend(events)

        self._inference_ms.add((t1 - t0) * 1000.0)
        self._tracking_ms.add((t2 - t1) * 1000.0)
        now_mono = time.monotonic()
        self._process_rate.tick(now_mono)
        self._pipeline_ms.add(time.time() * 1000.0 - frame.capture_wall_ms)
        self.result.processed_frames += 1
        self.result.last_timestamp_ms = frame.timestamp_ms

        perf = PerfStats(
            capture_fps=round(self._capture_rate.value, 2),
            process_fps=round(self._process_rate.value, 2),
            inference_ms=round(self._inference_ms.value, 2),
            tracking_ms=round(self._tracking_ms.value, 2),
            pipeline_ms=round(self._pipeline_ms.value, 2),
            dropped_frames=self.result.dropped_frames,
            queue_depth=self._queue.qsize(),
        )
        update = TrackingUpdate(
            session_id=self.session_id,
            frame_index=frame.index,
            timestamp_ms=frame.timestamp_ms,
            capture_wall_ms=frame.capture_wall_ms,
            emitted_wall_ms=time.time() * 1000.0,
            frame_width=w,
            frame_height=h,
            calibrated=projector is not None,
            simulated=self.source.simulated,
            entities=entities,
            perf=perf,
        )
        if self.recorder is not None:
            self.recorder.record(update, events)
        self.recent_updates.append(update)

        if self.options.stream_frames and self._wants_frames() and now_mono - self._last_stream >= 1.0 / self.options.stream_max_fps:
            self._last_stream = now_mono
            self._publish_frame(self._encode_frame(frame))
        self._publish_json(update.model_dump_json())
        for ev in events:
            self._publish_json(EventMessage(event=ev).model_dump_json())

        if now_mono - self._last_perf_log >= self.options.perf_log_interval_s:
            self._last_perf_log = now_mono
            log.info("perf %s", json.dumps({"session_id": self.session_id, "entities": len(entities), **perf.model_dump()}))

    def _encode_frame(self, frame: Frame) -> bytes:
        image = frame.image
        h, w = image.shape[:2]
        if w > self.options.stream_max_width:
            scale = self.options.stream_max_width / w
            image = cv2.resize(image, (self.options.stream_max_width, int(h * scale)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self.options.jpeg_quality])
        if not ok:
            return b""
        return FRAME_HEADER.pack(frame.index & 0xFFFFFFFF, max(0, frame.timestamp_ms) & 0xFFFFFFFF) + buf.tobytes()

    def _finish(self, last_t: int) -> None:
        try:
            closing = self.registry.close_all(last_t)
            stop_event = SpatialEvent(
                session_id=self.session_id,
                timestamp_ms=last_t,
                kind=EventKind.SESSION_STOPPED,
                data={"processed_frames": self.result.processed_frames, "dropped_frames": self.result.dropped_frames, "error": self.result.error},
            )
            closing.append(stop_event)
            if self._keep_events:
                self.result.events.extend(closing)
            for ev in closing:
                self._publish_json(EventMessage(event=ev).model_dump_json())
            if self.recorder is not None:
                self.recorder.add_events(closing)
                self.recorder.finish(
                    duration_ms=last_t,
                    processed_frames=self.result.processed_frames,
                    state=self.result.state if self.result.state != "running" else "stopped",
                    error=self.result.error,
                )
        except Exception:
            log.exception("failed to finalize session", extra={"session_id": self.session_id})
        finally:
            try:
                self.source.close()
            finally:
                self.detector.close()
                self._finished.set()
                if self._on_finished:
                    self._on_finished(self)
