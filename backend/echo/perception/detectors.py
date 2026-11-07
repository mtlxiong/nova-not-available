"""Per-frame object detectors behind a common interface."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from echo.schemas import BBox, Detection
from echo.video.sources import Frame

log = logging.getLogger("echo.perception")


class DetectorError(RuntimeError):
    pass


class Detector(ABC):
    name: str

    @abstractmethod
    def detect(self, frame: Frame) -> list[Detection]: ...

    def close(self) -> None:
        return None


class NullDetector(Detector):
    """No class detection (e.g. marker-only tracking)."""

    name = "none"

    def detect(self, frame: Frame) -> list[Detection]:
        return []


class YoloDetector(Detector):
    """Ultralytics YOLO on CPU by default. Loaded lazily so the rest of ECHO runs without it."""

    name = "yolo"

    def __init__(self, weights: Path, classes: list[str], confidence: float = 0.35, imgsz: int = 640, device: str | None = None):
        try:
            from ultralytics import YOLO
        except ImportError as exc:  # pragma: no cover - depends on optional install
            raise DetectorError("ultralytics is not installed; install backend extras 'yolo' or use detector='none'") from exc
        if not Path(weights).is_file():
            raise DetectorError(f"YOLO weights not found at {weights}")
        self._model = YOLO(str(weights))
        names: dict[int, str] = self._model.names
        unknown = [c for c in classes if c not in names.values()]
        if unknown:
            raise DetectorError(f"classes not supported by this model: {unknown}")
        self._class_ids = [i for i, n in names.items() if n in classes] if classes else None
        self._names = names
        self.confidence = confidence
        self.imgsz = imgsz
        self.device = device or "cpu"
        self.supported_classes = sorted(names.values())

    def detect(self, frame: Frame) -> list[Detection]:
        results = self._model.predict(
            frame.image,
            conf=self.confidence,
            classes=self._class_ids,
            imgsz=self.imgsz,
            device=self.device,
            verbose=False,
        )
        out: list[Detection] = []
        if not results:
            return out
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return out
        xyxy = boxes.xyxy.cpu().numpy()
        conf = boxes.conf.cpu().numpy()
        cls = boxes.cls.cpu().numpy().astype(int)
        for (x1, y1, x2, y2), c, k in zip(xyxy, conf, cls):
            out.append(
                Detection(
                    bbox=BBox(x1=float(x1), y1=float(y1), x2=float(x2), y2=float(y2)),
                    confidence=float(np.clip(c, 0.0, 1.0)),
                    class_id=int(k),
                    class_name=self._names[int(k)],
                )
            )
        return out


class SimulationDetector(Detector):
    """Reads simulation ground truth with deterministic box jitter. Only valid for SimulationSource."""

    name = "simulation"

    def __init__(self, seed: int = 7, jitter_px: float = 2.0):
        self.seed = seed
        self.jitter_px = jitter_px

    def detect(self, frame: Frame) -> list[Detection]:
        if frame.truth is None:
            raise DetectorError("SimulationDetector requires simulated frames")
        rng = np.random.default_rng(self.seed * 1_000_003 + frame.index)
        out = []
        for item in frame.truth:
            if item.kind != "person" or not item.visible or item.bbox is None:
                continue
            j = rng.normal(0.0, self.jitter_px, size=4)
            b = item.bbox
            out.append(
                Detection(
                    bbox=BBox(x1=b.x1 + j[0], y1=b.y1 + j[1], x2=b.x2 + j[2], y2=b.y2 + j[3]),
                    confidence=float(np.clip(0.9 + rng.normal(0, 0.03), 0.5, 0.99)),
                    class_id=0,
                    class_name="person",
                )
            )
        return out
