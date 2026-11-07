"""Planar ground-plane calibration: image pixels <-> world meters via a homography.

A homography is only valid for points that lie on the calibrated plane. Bounding-box
bottom-centers of people approximate floor contact; anything off the plane (a marker on a
counter top, a raised hand) is projected with parallax error.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence

import cv2
import numpy as np

from echo.schemas import CalibrationPoint, CalibrationRequest, CalibrationResult, WorldPosition
from echo.spatial.geometry import triangle_area

MIN_POINTS = 4
MAX_POINTS = 64
MIN_WORLD_TRIANGLE_M2 = 0.01
MIN_IMAGE_TRIANGLE_PX2 = 25.0


class CalibrationError(ValueError):
    """Raised when calibration input or result is not trustworthy."""


def _has_general_position_quad(points: np.ndarray, min_area: float) -> bool:
    """True if some 4 points exist with no 3 of them (nearly) collinear."""
    n = len(points)
    for quad in itertools.combinations(range(n), 4):
        if all(triangle_area(points[a], points[b], points[c]) > min_area for a, b, c in itertools.combinations(quad, 3)):
            return True
    return False


def _validate_inputs(request: CalibrationRequest) -> tuple[np.ndarray, np.ndarray]:
    pts = request.points
    if len(pts) < MIN_POINTS:
        raise CalibrationError(f"need at least {MIN_POINTS} calibration points, got {len(pts)}")
    if len(pts) > MAX_POINTS:
        raise CalibrationError(f"at most {MAX_POINTS} calibration points are supported")

    image = np.array([p.image for p in pts], dtype=np.float64)
    world = np.array([p.world for p in pts], dtype=np.float64)
    if not (np.isfinite(image).all() and np.isfinite(world).all()):
        raise CalibrationError("calibration points must be finite numbers")

    w, h = request.image_width, request.image_height
    if (image[:, 0] < 0).any() or (image[:, 0] > w).any() or (image[:, 1] < 0).any() or (image[:, 1] > h).any():
        raise CalibrationError("image points must lie inside the image")

    for arr, name, eps in ((image, "image", 1.0), (world, "world", 1e-3)):
        for i, j in itertools.combinations(range(len(arr)), 2):
            if np.linalg.norm(arr[i] - arr[j]) < eps:
                raise CalibrationError(f"duplicate {name} points at index {i} and {j}")

    if not _has_general_position_quad(world, MIN_WORLD_TRIANGLE_M2):
        raise CalibrationError("world points are collinear or degenerate; need 4 points with no 3 on a line")
    if not _has_general_position_quad(image, MIN_IMAGE_TRIANGLE_PX2):
        raise CalibrationError("image points are collinear or degenerate; need 4 points with no 3 on a line")
    return image, world


def project(homography: np.ndarray, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Apply a homography to Nx2 points. Returns (projected Nx2, homogeneous w N)."""
    ones = np.ones((len(points), 1), dtype=np.float64)
    homog = np.hstack([points.astype(np.float64), ones]) @ homography.T
    w = homog[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        projected = homog[:, :2] / w[:, None]
    return projected, w


def compute_calibration(request: CalibrationRequest) -> CalibrationResult:
    image, world = _validate_inputs(request)
    homography, _ = cv2.findHomography(image, world, method=0)
    if homography is None or not np.isfinite(homography).all():
        raise CalibrationError("homography estimation failed")
    if abs(np.linalg.det(homography)) < 1e-12:
        raise CalibrationError("homography is singular")
    homography = homography / homography[2, 2]

    projected, w = project(homography, image)
    if not ((w > 0).all() or (w < 0).all()):
        raise CalibrationError("calibration points straddle the horizon; check point correspondences")

    errors = np.linalg.norm(projected - world, axis=1)
    rmse = float(math.sqrt(float(np.mean(errors**2))))
    max_err = float(errors.max())
    if not math.isfinite(rmse) or rmse > request.max_rmse_m:
        raise CalibrationError(
            f"reprojection RMSE {rmse:.3f} m exceeds limit {request.max_rmse_m:.3f} m; check point correspondences"
        )

    warnings: list[str] = []
    if len(request.points) == MIN_POINTS:
        warnings.append("exactly 4 points: fit is exact, reprojection error cannot detect bad correspondences")

    return CalibrationResult(
        camera_id=request.camera_id,
        image_width=request.image_width,
        image_height=request.image_height,
        homography=homography.tolist(),
        reprojection_rmse_m=rmse,
        max_error_m=max_err,
        point_errors_m=[float(e) for e in errors],
        points=list(request.points),
        warnings=warnings,
        label=request.label,
    )


class GroundPlaneProjector:
    """Converts image points to world meters with a validated homography."""

    def __init__(self, calibration: CalibrationResult, frame_width: int | None = None, frame_height: int | None = None):
        self.calibration = calibration
        h = np.array(calibration.homography, dtype=np.float64)
        if h.shape != (3, 3) or not np.isfinite(h).all():
            raise CalibrationError("stored homography is malformed")
        sx = calibration.image_width / frame_width if frame_width else 1.0
        sy = calibration.image_height / frame_height if frame_height else 1.0
        # Rescale if the live frame resolution differs from the calibration image resolution.
        self._h = h @ np.diag([sx, sy, 1.0])
        self._frame_w = frame_width or calibration.image_width
        self._frame_h = frame_height or calibration.image_height
        sample = np.array([p.image for p in calibration.points], dtype=np.float64) / np.array([sx, sy])
        _, w = project(self._h, sample)
        self._w_sign = 1.0 if (w > 0).all() else -1.0

    def image_to_world(self, u: float, v: float) -> WorldPosition | None:
        """Returns ``None`` for points outside the frame or beyond the plane's horizon."""
        if not (math.isfinite(u) and math.isfinite(v)):
            return None
        if u < 0 or v < 0 or u > self._frame_w or v > self._frame_h:
            return None
        projected, w = project(self._h, np.array([[u, v]]))
        if w[0] * self._w_sign <= 1e-9:
            return None
        x, y = float(projected[0, 0]), float(projected[0, 1])
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        return WorldPosition(x=x, y=y)

    def world_to_image(self, x: float, y: float) -> tuple[float, float] | None:
        inv = np.linalg.inv(self._h)
        projected, w = project(inv, np.array([[x, y]]))
        if not np.isfinite(projected).all():
            return None
        return float(projected[0, 0]), float(projected[0, 1])


def calibration_from_homography(
    homography: np.ndarray,
    image_width: int,
    image_height: int,
    world_points: Sequence[tuple[float, float]],
    *,
    camera_id: str,
    simulated: bool,
    label: str | None = None,
) -> CalibrationResult:
    """Builds a validated calibration from a known world->image homography (used by simulation)."""
    img, _ = project(homography, np.array(world_points, dtype=np.float64))
    request = CalibrationRequest(
        camera_id=camera_id,
        image_width=image_width,
        image_height=image_height,
        points=[CalibrationPoint(image=(float(u), float(v)), world=(float(x), float(y))) for (u, v), (x, y) in zip(img, world_points)],
        label=label,
    )
    result = compute_calibration(request)
    result.simulated = simulated
    return result
