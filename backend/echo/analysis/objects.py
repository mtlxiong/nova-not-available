"""Static-object labeling on the background plate (people already removed).

Open-vocabulary detection (YOLO-World) so any workspace can be described with its own words;
falls back to the closed-set COCO detector when YOLO-World weights are unavailable. Labels are
detector outputs with confidences, meant to be reviewed and renamed by a person.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("echo.analysis")

DEFAULT_VOCABULARY = [
    # furniture / work surfaces
    "table", "desk", "counter", "workbench", "shelf", "rack", "cabinet", "drawer unit", "chair",
    "stool", "sofa", "bench", "locker", "reception desk", "checkout counter",
    # food service / retail
    "refrigerator", "freezer", "oven", "stove", "fryer", "sink", "dishwasher", "microwave",
    "display case", "cash register", "vending machine", "shopping cart",
    # logistics / industrial
    "conveyor belt", "pallet", "cardboard box", "cart", "hand truck", "forklift", "machine",
    "storage bin", "ladder", "tool cabinet",
    # building
    "door", "window", "staircase", "pillar", "wall", "whiteboard",
    # office / electronics
    "computer monitor", "printer", "trash can",
    # outdoor / vehicles
    "car", "van", "truck", "bicycle", "motorcycle", "traffic cone", "bollard", "lamp post",
    "sign", "planter", "tripod",
]  # fmt: skip

COCO_STATIC = {
    "bench", "chair", "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop",
    "microwave", "oven", "toaster", "sink", "refrigerator", "clock", "suitcase", "bicycle",
    "motorcycle", "car", "truck", "bus", "fire hydrant", "stop sign", "parking meter",
}  # fmt: skip


@dataclass(frozen=True)
class StaticObject:
    label: str
    confidence: float
    bbox: tuple[float, float, float, float]


class StaticObjectDetector:
    def __init__(self, world_weights: Path | None, coco_weights: Path, device: str = "cpu"):
        self.world_weights = world_weights
        self.coco_weights = coco_weights
        self.device = device
        self._lock = threading.Lock()
        self._world = None
        self._world_vocab: tuple[str, ...] | None = None
        self.backend: str | None = None

    def _load_world(self, vocabulary: list[str]):
        if self.world_weights is None or not Path(self.world_weights).exists():
            return None
        try:
            from ultralytics import YOLOWorld

            if self._world is None:
                self._world = YOLOWorld(str(self.world_weights))
            if self._world_vocab != tuple(vocabulary):
                self._world.set_classes(list(vocabulary))
                self._world_vocab = tuple(vocabulary)
            return self._world
        except Exception:  # missing CLIP package, corrupt weights, ...
            log.exception("YOLO-World unavailable; falling back to COCO classes")
            return None

    def detect(self, image: np.ndarray, vocabulary: list[str] | None = None, confidence: float = 0.12) -> list[StaticObject]:
        vocabulary = [v.strip() for v in (vocabulary or DEFAULT_VOCABULARY) if v.strip()]
        with self._lock:
            model = self._load_world(vocabulary)
            if model is not None:
                self.backend = "yolo-world"
                res = model.predict(image, conf=confidence, iou=0.5, device=self.device, verbose=False, imgsz=1280)[0]
                names = model.names
                allowed = None
            else:
                from ultralytics import YOLO

                self.backend = "yolo-coco"
                coco = YOLO(str(self.coco_weights))
                res = coco.predict(image, conf=max(confidence, 0.25), device=self.device, verbose=False)[0]
                names = coco.names
                allowed = COCO_STATIC
        out = []
        boxes = res.boxes
        for xyxy, cls, conf in zip(boxes.xyxy.cpu().numpy(), boxes.cls.cpu().numpy(), boxes.conf.cpu().numpy()):
            label = names[int(cls)]
            if label == "person" or (allowed is not None and label not in allowed):
                continue
            out.append(StaticObject(label, float(conf), tuple(float(v) for v in xyxy)))
        return _suppress_nested(out)


def _suppress_nested(objs: list[StaticObject], contain: float = 0.85) -> list[StaticObject]:
    """Open-vocab detectors often label one object twice ("table" + "desk"); keep the most confident."""
    objs = sorted(objs, key=lambda o: -o.confidence)
    kept: list[StaticObject] = []
    for o in objs:
        x1, y1, x2, y2 = o.bbox
        area = max(1e-6, (x2 - x1) * (y2 - y1))
        dup = False
        for k in kept:
            ix = max(0.0, min(x2, k.bbox[2]) - max(x1, k.bbox[0]))
            iy = max(0.0, min(y2, k.bbox[3]) - max(y1, k.bbox[1]))
            karea = max(1e-6, (k.bbox[2] - k.bbox[0]) * (k.bbox[3] - k.bbox[1]))
            inter = ix * iy
            if inter / min(area, karea) > contain and (inter / max(area, karea) > 0.5 or o.label == k.label):
                dup = True
                break
        if not dup:
            kept.append(o)
    return kept
