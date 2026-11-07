"""Observations collected for workspace analysis: occasional frames + every person detection."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PersonBox:
    t_ms: int
    track: str | None
    """Tracker identity when available (enables dwell vs. walking separation)."""
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float

    @property
    def xyxy(self) -> tuple[float, float, float, float]:
        return (self.x1, self.y1, self.x2, self.y2)


class SceneSampleBuffer:
    """Thread-safe ring buffers filled by a running session, read by the analyzer."""

    def __init__(self, frame_interval_ms: int = 1000, max_frames: int = 60, max_boxes: int = 20000, max_width: int = 960):
        self.frame_interval_ms = frame_interval_ms
        self.max_width = max_width
        self._lock = threading.Lock()
        self._frames: deque[tuple[int, np.ndarray, float]] = deque(maxlen=max_frames)
        self._boxes: deque[PersonBox] = deque(maxlen=max_boxes)
        self._last_frame_t: int | None = None
        self.image_size: tuple[int, int] | None = None

    def add(self, t_ms: int, image: np.ndarray, people: list[PersonBox]) -> None:
        h, w = image.shape[:2]
        with self._lock:
            if self.image_size != (w, h):
                self._frames.clear()
                self._boxes.clear()
                self._last_frame_t = None
                self.image_size = (w, h)
            self._boxes.extend(people)
            if self._last_frame_t is None or t_ms - self._last_frame_t >= self.frame_interval_ms:
                self._last_frame_t = t_ms
                scale = min(1.0, self.max_width / w)
                if scale < 1.0:
                    import cv2

                    image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
                else:
                    image = image.copy()
                self._frames.append((t_ms, image, scale))

    def snapshot(self) -> tuple[list[tuple[int, np.ndarray, float]], list[PersonBox], tuple[int, int] | None]:
        with self._lock:
            return list(self._frames), list(self._boxes), self.image_size
