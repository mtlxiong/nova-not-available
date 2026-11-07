"""Empty-workspace background plate: per-pixel median over frames, ignoring pixels covered by people."""

from __future__ import annotations

import numpy as np

BoxList = list[tuple[float, float, float, float]]


class BackgroundBuilder:
    def __init__(self, max_frames: int = 60, max_width: int = 960, pad_px: int = 6):
        self.max_frames = max_frames
        self.max_width = max_width
        self.pad_px = pad_px
        self._frames: list[np.ndarray] = []
        self._masks: list[np.ndarray] = []
        self.size: tuple[int, int] | None = None
        """(width, height) of the frames as given (before internal downscale)."""

    def __len__(self) -> int:
        return len(self._frames)

    def add(self, image: np.ndarray, people: BoxList) -> None:
        if len(self._frames) >= self.max_frames:
            return
        h, w = image.shape[:2]
        if self.size is None:
            self.size = (w, h)
        elif self.size != (w, h):
            raise ValueError("all frames must share one resolution")
        scale = min(1.0, self.max_width / w)
        if scale < 1.0:
            import cv2

            image = cv2.resize(image, (int(round(w * scale)), int(round(h * scale))), interpolation=cv2.INTER_AREA)
        mask = np.zeros(image.shape[:2], dtype=bool)
        p = self.pad_px
        for x1, y1, x2, y2 in people:
            mask[max(0, int(y1 * scale) - p) : int(y2 * scale) + p, max(0, int(x1 * scale) - p) : int(x2 * scale) + p] = True
        self._frames.append(image)
        self._masks.append(mask)

    def build(self) -> tuple[np.ndarray, float]:
        """Returns (plate at original resolution, fraction of pixels never seen unoccupied)."""
        if not self._frames or self.size is None:
            raise ValueError("no frames")
        stack = np.stack(self._frames).astype(np.float32)
        masks = np.stack(self._masks)
        stack[masks] = np.nan
        with np.errstate(all="ignore"):
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                plate = np.nanmedian(stack, axis=0)
        never_clear = np.isnan(plate[..., 0])
        if never_clear.any():
            # Pixels always covered by someone: fall back to the plain median (may contain a ghost).
            fallback = np.median(np.stack(self._frames).astype(np.float32), axis=0)
            plate[never_clear] = fallback[never_clear]
        plate = np.clip(plate, 0, 255).astype(np.uint8)
        w, h = self.size
        if plate.shape[1] != w:
            import cv2

            plate = cv2.resize(plate, (w, h), interpolation=cv2.INTER_LINEAR)
        return plate, float(never_clear.mean())
