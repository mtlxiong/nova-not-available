"""Approximate single-view ground-plane calibration from people of (assumed) average height.

Model: pinhole camera, principal point at the image center, zero roll, flat floor, horizontal
field of view assumed (default 65 deg; user-adjustable). Every standing person gives one
constraint: their feet are on the floor and their head is ~``person_height_m`` above it. We fit
camera pitch and camera height so predicted head rows match detected bbox tops.

This is an *estimate*, not a survey. Errors come from the height assumption (people vary
~±7%), the FOV assumption (scales lateral distances), bbox looseness, and non-flat floors.
The result is labeled ``method="auto-person-height"`` and carries its own uncertainty
estimate; four measured floor points (``compute_calibration``) always supersede it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from echo.schemas import CalibrationPoint, CalibrationResult

DEFAULT_PERSON_HEIGHT_M = 1.70
DEFAULT_HFOV_DEG = 65.0
MIN_SAMPLES = 15
MIN_ROW_SPREAD = 0.12
"""Feet must span at least this fraction of the image height, or depth is unobservable."""


class AutoCalibrationError(ValueError):
    pass


@dataclass(frozen=True)
class PersonSample:
    foot_u: float
    foot_v: float
    head_v: float


@dataclass(frozen=True)
class CameraModel:
    image_width: int
    image_height: int
    focal_px: float
    pitch_rad: float
    """Downward tilt of the optical axis from horizontal."""
    height_m: float

    @property
    def cx(self) -> float:
        return self.image_width / 2.0

    @property
    def cy(self) -> float:
        return self.image_height / 2.0

    @property
    def horizon_v(self) -> float:
        return self.cy - self.focal_px * math.tan(self.pitch_rad)

    def world_to_image_h(self) -> np.ndarray:
        """Ground plane (camera foot at origin, +y forward) -> image homography."""
        s, c = math.sin(self.pitch_rad), math.cos(self.pitch_rad)
        k = np.array([[self.focal_px, 0, self.cx], [0, self.focal_px, self.cy], [0, 0, 1.0]])
        m = np.array([[1.0, 0, 0], [0, -s, c * self.height_m], [0, c, s * self.height_m]])
        return k @ m

    def head_row(self, foot_v: np.ndarray, person_height_m: float) -> np.ndarray:
        return _predict_head_v(foot_v, self.cy, self.focal_px, self.pitch_rad, self.height_m, person_height_m)

    def implied_heights(self, foot_v: np.ndarray, head_v: np.ndarray) -> np.ndarray:
        """Per-sample person height that would explain the observed head row exactly."""
        s, c = math.sin(self.pitch_rad), math.cos(self.pitch_rad)
        b = (foot_v - self.cy) / self.focal_px
        den = b * c + s
        forward = np.where(den > 1e-6, (c - b * s) * self.height_m / np.maximum(den, 1e-6), np.nan)
        q = (head_v - self.cy) / self.focal_px
        dh = -forward * (s + q * c) / (c - q * s)
        return self.height_m + dh


def _predict_head_v(foot_v, cy, f, pitch, cam_h, person_h):
    """Vectorized over broadcastable pitch / cam_h grids. Returns NaN where undefined."""
    s, c = np.sin(pitch), np.cos(pitch)
    b = (foot_v - cy) / f
    den = b * c + s
    with np.errstate(divide="ignore", invalid="ignore"):
        forward = np.where(den > 1e-4, (c - b * s) * cam_h / den, np.nan)
        dz = person_h - cam_h
        yc = -s * forward - c * dz
        zc = c * forward - s * dz
        v = np.where(zc > 1e-3, cy + f * yc / zc, np.nan)
    return v


def _loss(pred, foot_v, head_v, trim: float = 0.8) -> np.ndarray:
    """Trimmed mean squared *relative* head-row error (robust to sitting/partial people)."""
    rel = (pred - head_v) / np.maximum(foot_v - head_v, 1.0)
    sq = np.where(np.isfinite(rel), rel**2, 4.0)
    k = max(1, int(sq.shape[-1] * trim))
    return np.mean(np.sort(sq, axis=-1)[..., :k], axis=-1)


def filter_person_boxes(boxes: list[tuple[float, float, float, float, float]], image_width: int, image_height: int) -> list[PersonSample]:
    """(x1, y1, x2, y2, conf) person boxes -> samples likely to be whole, standing people."""
    out = []
    for x1, y1, x2, y2, conf in boxes:
        w, h = x2 - x1, y2 - y1
        if conf < 0.4 or h < 24 or w <= 0:
            continue
        if h / w < 1.6:  # sitting, bending, or merged boxes
            continue
        if y1 <= 2 or y2 >= image_height - 2 or x1 <= 1 or x2 >= image_width - 1:
            continue  # truncated by the frame edge
        out.append(PersonSample((x1 + x2) / 2.0, y2, y1))
    return out


def estimate_camera(
    samples: list[PersonSample],
    image_width: int,
    image_height: int,
    *,
    person_height_m: float = DEFAULT_PERSON_HEIGHT_M,
    hfov_deg: float = DEFAULT_HFOV_DEG,
) -> tuple[CameraModel, dict]:
    if len(samples) < MIN_SAMPLES:
        raise AutoCalibrationError(f"need at least {MIN_SAMPLES} clear standing-person detections, got {len(samples)}")
    foot_v = np.array([s.foot_v for s in samples], dtype=np.float64)
    head_v = np.array([s.head_v for s in samples], dtype=np.float64)
    spread = (np.percentile(foot_v, 95) - np.percentile(foot_v, 5)) / image_height
    if spread < MIN_ROW_SPREAD:
        raise AutoCalibrationError(
            f"people were only seen in a narrow band of the image ({spread:.0%} of its height); depth cannot be estimated"
        )
    if len(samples) > 600:
        idx = np.random.default_rng(0).choice(len(samples), 600, replace=False)
        foot_v, head_v = foot_v[idx], head_v[idx]

    f = (image_width / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
    cy = image_height / 2.0

    def search(pitches, heights):
        p = pitches[:, None, None]
        hh = heights[None, :, None]
        loss = _loss(_predict_head_v(foot_v[None, None, :], cy, f, p, hh, person_height_m), foot_v, head_v)
        i, j = np.unravel_index(np.nanargmin(loss), loss.shape)
        return pitches[i], heights[j], float(loss[i, j])

    pitch, cam_h, _ = search(np.radians(np.linspace(0.0, 80.0, 81)), np.geomspace(0.8, 15.0, 70))
    pitch, cam_h, loss = search(
        np.linspace(pitch - math.radians(1.5), pitch + math.radians(1.5), 31),
        np.geomspace(cam_h * 0.9, cam_h * 1.1, 31),
    )
    camera = CameraModel(image_width, image_height, f, float(pitch), float(cam_h))

    implied = camera.implied_heights(foot_v, head_v)
    implied = implied[np.isfinite(implied)]
    med = float(np.median(implied)) if len(implied) else float("nan")
    mad = float(np.median(np.abs(implied - med)) * 1.4826) if len(implied) else float("nan")
    rel_spread = mad / med if med > 0 else float("nan")
    quality = {
        "samples": int(len(foot_v)),
        "foot_row_spread": round(float(spread), 3),
        "hfov_deg_assumed": hfov_deg,
        "person_height_m_assumed": person_height_m,
        "pitch_deg": round(math.degrees(pitch), 2),
        "camera_height_m": round(float(cam_h), 3),
        "horizon_v": round(camera.horizon_v, 1),
        "trimmed_rel_head_error": round(math.sqrt(loss), 4),
        "implied_height_rel_spread": round(rel_spread, 4) if math.isfinite(rel_spread) else None,
    }
    return camera, quality


def calibration_from_camera(
    camera: CameraModel,
    samples: list[PersonSample],
    quality: dict,
    *,
    camera_id: str,
    world_offset: tuple[float, float] = (0.0, 0.0),
    label: str | None = None,
) -> CalibrationResult:
    """Image->world homography. World = camera-foot frame shifted by ``world_offset`` (meters)."""
    h_w2i = camera.world_to_image_h()
    t = np.array([[1.0, 0, world_offset[0]], [0, 1.0, world_offset[1]], [0, 0, 1.0]])
    h_i2w = t @ np.linalg.inv(h_w2i)
    foot = np.array([[s.foot_u, s.foot_v] for s in samples], dtype=np.float64)
    homog = np.hstack([foot, np.ones((len(foot), 1))]) @ h_i2w.T
    if np.median(homog[:, 2]) < 0:
        h_i2w = -h_i2w
    h_i2w = h_i2w / abs(h_i2w[2, 2]) if abs(h_i2w[2, 2]) > 1e-12 else h_i2w

    # Four model-derived correspondences spanning where people stood (used for display and
    # horizon-side checks; they are outputs of the fit, not measurements).
    us = np.percentile(foot[:, 0], [10, 90])
    vs = np.percentile(foot[:, 1], [15, 90])
    corners = np.array([[us[0], vs[1]], [us[1], vs[1]], [us[1], vs[0]], [us[0], vs[0]]])
    hc = np.hstack([corners, np.ones((4, 1))]) @ h_i2w.T
    world = hc[:, :2] / hc[:, 2:3]

    homog = np.hstack([foot, np.ones((len(foot), 1))]) @ h_i2w.T
    dist = np.hypot(homog[:, 0] / homog[:, 2] - world_offset[0], homog[:, 1] / homog[:, 2] - world_offset[1])
    rel = quality.get("implied_height_rel_spread") or 0.1
    rel = max(rel, 0.05)  # people really do vary by at least this much
    est_err = rel * dist
    rmse = float(np.sqrt(np.mean(est_err**2)))
    p90 = float(np.percentile(est_err, 90))

    warnings = [
        "APPROXIMATE: estimated from people's apparent heights (assumed "
        f"{quality['person_height_m_assumed']:.2f} m) and an assumed {quality['hfov_deg_assumed']:.0f}° horizontal FOV; not a surveyed calibration",
        f"estimated position error ~{rmse:.2f} m RMS (~{rel:.0%} of distance from camera); measure 4+ floor points to replace it",
    ]
    return CalibrationResult(
        camera_id=camera_id,
        image_width=camera.image_width,
        image_height=camera.image_height,
        homography=h_i2w.tolist(),
        reprojection_rmse_m=rmse,
        max_error_m=p90,
        point_errors_m=[],
        points=[CalibrationPoint(image=(float(u), float(v)), world=(float(x), float(y))) for (u, v), (x, y) in zip(corners, world)],
        warnings=warnings,
        label=label or "Auto (person-height estimate)",
        method="auto-person-height",
    )
