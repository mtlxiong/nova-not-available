"""Multi-object trackers that turn per-frame detections into tracks with tracker-scoped IDs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import SimpleNamespace

import numpy as np

from echo.schemas import BBox, Detection, EntitySource, ImagePoint, Track
from echo.video.sources import Frame


def reference_point_for(class_name: str, bbox: BBox) -> ImagePoint:
    """Ground-contact approximation: bottom-center for things standing on the floor."""
    return bbox.bottom_center


class Tracker(ABC):
    name: str

    def __init__(self, source: EntitySource):
        self.source = source

    @abstractmethod
    def update(self, detections: list[Detection], frame: Frame) -> list[Track]: ...

    @abstractmethod
    def reset(self) -> None: ...


class _DetectionBatch:
    """Adapter exposing the ``conf``/``xywh``/``cls`` interface Ultralytics trackers expect."""

    def __init__(self, xywh: np.ndarray, conf: np.ndarray, cls: np.ndarray):
        self.xywh = xywh
        self.conf = conf
        self.cls = cls

    def __len__(self) -> int:
        return len(self.conf)

    def __getitem__(self, idx) -> "_DetectionBatch":
        return _DetectionBatch(self.xywh[idx], self.conf[idx], self.cls[idx])


class ByteTrackTracker(Tracker):
    """ByteTrack via Ultralytics' implementation, fed by any ``Detector``."""

    name = "bytetrack"

    def __init__(
        self,
        source: EntitySource,
        frame_rate: float = 10.0,
        track_high_thresh: float = 0.25,
        track_low_thresh: float = 0.1,
        new_track_thresh: float = 0.25,
        track_buffer: int = 30,
        match_thresh: float = 0.8,
    ):
        super().__init__(source)
        from ultralytics.trackers.byte_tracker import BYTETracker

        self._args = SimpleNamespace(
            tracker_type="bytetrack",
            track_high_thresh=track_high_thresh,
            track_low_thresh=track_low_thresh,
            new_track_thresh=new_track_thresh,
            track_buffer=track_buffer,
            match_thresh=match_thresh,
            fuse_score=True,
        )
        self._frame_rate = frame_rate
        self._cls = BYTETracker
        self._names: dict[int, str] = {}
        self.reset()

    def reset(self) -> None:
        try:
            self._tracker = self._cls(self._args, frame_rate=int(round(self._frame_rate)))
        except TypeError:
            self._tracker = self._cls(self._args)
        self._tracker.reset_id()

    def update(self, detections: list[Detection], frame: Frame) -> list[Track]:
        for d in detections:
            self._names[d.class_id] = d.class_name
        if detections:
            xyxy = np.array([[d.bbox.x1, d.bbox.y1, d.bbox.x2, d.bbox.y2] for d in detections], dtype=np.float32)
            xywh = np.column_stack([(xyxy[:, 0] + xyxy[:, 2]) / 2, (xyxy[:, 1] + xyxy[:, 3]) / 2, xyxy[:, 2] - xyxy[:, 0], xyxy[:, 3] - xyxy[:, 1]])
            batch = _DetectionBatch(xywh, np.array([d.confidence for d in detections], dtype=np.float32), np.array([d.class_id for d in detections], dtype=np.float32))
        else:
            batch = _DetectionBatch(np.zeros((0, 4), np.float32), np.zeros((0,), np.float32), np.zeros((0,), np.float32))
        rows = self._tracker.update(batch, frame.image)
        tracks = []
        for row in rows:
            x1, y1, x2, y2, tid, score, cls = (float(v) for v in row[:7])
            bbox = BBox(x1=x1, y1=y1, x2=x2, y2=y2)
            name = self._names.get(int(cls), str(int(cls)))
            tracks.append(
                Track(
                    source=self.source,
                    track_id=str(int(tid)),
                    bbox=bbox,
                    confidence=float(np.clip(score, 0.0, 1.0)),
                    class_name=name,
                    reference_point=reference_point_for(name, bbox),
                )
            )
        return tracks


def _iou(a: BBox, b: BBox) -> float:
    ix = max(0.0, min(a.x2, b.x2) - max(a.x1, b.x1))
    iy = max(0.0, min(a.y2, b.y2) - max(a.y1, b.y1))
    inter = ix * iy
    union = (a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - inter
    return inter / union if union > 0 else 0.0


class IouTracker(Tracker):
    """Greedy IoU association. Dependency-free fallback; weaker than ByteTrack under occlusion."""

    name = "iou"

    def __init__(self, source: EntitySource, iou_threshold: float = 0.3, max_missed: int = 30):
        super().__init__(source)
        self.iou_threshold = iou_threshold
        self.max_missed = max_missed
        self.reset()

    def reset(self) -> None:
        self._next_id = 1
        self._tracks: dict[int, tuple[Detection, int]] = {}

    def update(self, detections: list[Detection], frame: Frame) -> list[Track]:
        pairs = sorted(
            ((_iou(t.bbox, d.bbox), tid, di) for tid, (t, _) in self._tracks.items() for di, d in enumerate(detections) if t.class_name == d.class_name),
            reverse=True,
        )
        used_t: set[int] = set()
        used_d: set[int] = set()
        matched: dict[int, Detection] = {}
        for score, tid, di in pairs:
            if score < self.iou_threshold or tid in used_t or di in used_d:
                continue
            used_t.add(tid)
            used_d.add(di)
            matched[tid] = detections[di]
        for tid in list(self._tracks):
            if tid in matched:
                self._tracks[tid] = (matched[tid], 0)
            else:
                det, missed = self._tracks[tid]
                if missed + 1 > self.max_missed:
                    del self._tracks[tid]
                else:
                    self._tracks[tid] = (det, missed + 1)
        for di, d in enumerate(detections):
            if di not in used_d:
                matched[self._next_id] = d
                self._tracks[self._next_id] = (d, 0)
                self._next_id += 1
        return [
            Track(
                source=self.source,
                track_id=str(tid),
                bbox=d.bbox,
                confidence=d.confidence,
                class_name=d.class_name,
                reference_point=reference_point_for(d.class_name, d.bbox),
            )
            for tid, d in sorted(matched.items())
        ]
