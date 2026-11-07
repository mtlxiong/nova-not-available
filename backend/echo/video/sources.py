"""Unified video sources. Every source yields ``Frame`` objects with media timestamps."""

from __future__ import annotations

import logging
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

log = logging.getLogger("echo.video")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


class VideoSourceError(RuntimeError):
    pass


@dataclass
class Frame:
    index: int
    """Index in the source's native frame sequence (not the processed-frame count)."""
    timestamp_ms: int
    capture_wall_ms: float
    image: np.ndarray
    truth: list[Any] | None = field(default=None, repr=False)
    """Simulation ground truth. Always ``None`` for real sources."""


class VideoSource(ABC):
    kind: str
    is_live: bool
    simulated: bool = False

    def __init__(self) -> None:
        self.width = 0
        self.height = 0
        self.fps = 0.0
        self._opened = False

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def read(self) -> Frame | None:
        """Next frame, or ``None`` at end of stream."""

    @abstractmethod
    def close(self) -> None: ...

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "width": self.width, "height": self.height, "fps": self.fps, "live": self.is_live}

    def __enter__(self) -> "VideoSource":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _now_ms() -> float:
    return time.time() * 1000.0


class WebcamSource(VideoSource):
    kind = "webcam"
    is_live = True

    def __init__(self, camera_index: int = 0, width: int | None = None, height: int | None = None, fps: float | None = None):
        super().__init__()
        self.camera_index = camera_index
        self._req = (width, height, fps)
        self._cap: cv2.VideoCapture | None = None
        self._t0 = 0.0
        self._index = 0

    def open(self) -> None:
        backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.camera_index, backend)
        if not cap.isOpened():
            cap.release()
            raise VideoSourceError(
                f"camera {self.camera_index} is not available (missing device or camera permission denied for this process)"
            )
        w, h, fps = self._req
        if w:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        if h:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        if fps:
            cap.set(cv2.CAP_PROP_FPS, fps)
        ok, image = cap.read()
        if not ok or image is None:
            cap.release()
            raise VideoSourceError(f"camera {self.camera_index} opened but returned no frames")
        self._cap = cap
        self.height, self.width = image.shape[:2]
        self.fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        self._t0 = time.monotonic()
        self._index = 0
        self._opened = True

    def read(self) -> Frame | None:
        if self._cap is None:
            raise VideoSourceError("webcam source is not open")
        ok, image = self._cap.read()
        if not ok or image is None:
            return None
        frame = Frame(
            index=self._index,
            timestamp_ms=int((time.monotonic() - self._t0) * 1000),
            capture_wall_ms=_now_ms(),
            image=image,
        )
        self._index += 1
        return frame

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._opened = False


def validate_video_file(path: Path) -> dict[str, Any]:
    """Opens the file and checks it decodes. Returns basic metadata."""
    if not path.is_file():
        raise VideoSourceError(f"video file not found: {path.name}")
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise VideoSourceError(f"unsupported video extension {path.suffix!r}; expected one of {sorted(VIDEO_EXTENSIONS)}")
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise VideoSourceError("file could not be opened as video")
        ok, image = cap.read()
        if not ok or image is None:
            raise VideoSourceError("video contains no decodable frames")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        h, w = image.shape[:2]
        return {
            "width": w,
            "height": h,
            "fps": fps,
            "frame_count": count,
            "duration_ms": int(count / fps * 1000) if fps > 0 and count > 0 else None,
        }
    finally:
        cap.release()


class FileSource(VideoSource):
    """Prerecorded video. Samples frames at ``sample_fps`` (media time) and preserves video timestamps."""

    kind = "file"
    is_live = False

    def __init__(self, path: Path, sample_fps: float | None = None, max_width: int | None = None):
        super().__init__()
        self.path = Path(path)
        self.sample_fps = sample_fps
        self.max_width = max_width
        self._cap: cv2.VideoCapture | None = None
        self._next_index = 0
        self._stride = 1
        self.frame_count = 0

    def open(self) -> None:
        meta = validate_video_file(self.path)
        cap = cv2.VideoCapture(str(self.path))
        if not cap.isOpened():
            raise VideoSourceError("file could not be opened as video")
        self._cap = cap
        self.fps = meta["fps"] if meta["fps"] > 0 else 30.0
        self.frame_count = meta["frame_count"]
        self.width, self.height = meta["width"], meta["height"]
        if self.max_width and self.width > self.max_width:
            scale = self.max_width / self.width
            self.width, self.height = self.max_width, int(round(self.height * scale))
        self._stride = max(1, int(round(self.fps / self.sample_fps))) if self.sample_fps else 1
        self._next_index = 0
        self._opened = True

    def read(self) -> Frame | None:
        if self._cap is None:
            raise VideoSourceError("file source is not open")
        index = self._next_index
        ok, image = self._cap.read()
        if not ok or image is None:
            return None
        for _ in range(self._stride - 1):
            if not self._cap.grab():
                break
        self._next_index = index + self._stride
        if image.shape[1] != self.width:
            image = cv2.resize(image, (self.width, self.height), interpolation=cv2.INTER_AREA)
        return Frame(
            index=index,
            timestamp_ms=int(round(index / self.fps * 1000)),
            capture_wall_ms=_now_ms(),
            image=image,
        )

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._opened = False
