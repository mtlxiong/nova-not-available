"""Turns analysis observations into a workspace spatial model: floor extent, fixtures, zones, floor texture.

Everything here is derived from the camera's own footage. Measured quantities (floor
positions, fixture front-edge widths) come from the ground-plane calibration; quantities a
single camera cannot see (fixture depth, sometimes height) are filled from category priors
and listed in ``Fixture.estimated_dims``.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

from echo.analysis.objects import StaticObject
from echo.analysis.samples import PersonBox
from echo.schemas import Fixture, Zone
from echo.spatial.calibration import GroundPlaneProjector
from echo.spatial.geometry import polygon_centroid

# (depth m, height m) priors for things a single camera sees mostly from the front.
CATEGORY_PRIORS: dict[str, tuple[float, float]] = {
    "table": (0.8, 0.75), "dining table": (0.9, 0.75), "desk": (0.7, 0.75), "counter": (0.65, 0.9),
    "workbench": (0.75, 0.9), "shelf": (0.45, 1.8), "rack": (0.6, 1.8), "cabinet": (0.5, 1.0),
    "drawer unit": (0.5, 0.7), "chair": (0.5, 0.9), "stool": (0.4, 0.7), "door": (0.08, 2.05),
    "refrigerator": (0.7, 1.8), "freezer": (0.7, 1.0), "oven": (0.7, 0.9), "stove": (0.7, 0.9),
    "fryer": (0.7, 0.9), "sink": (0.6, 0.9), "dishwasher": (0.6, 0.85), "microwave": (0.4, 0.3),
    "conveyor belt": (0.8, 0.8), "pallet": (1.0, 0.15), "cardboard box": (0.4, 0.4), "cart": (0.6, 1.0),
    "trash can": (0.45, 0.8), "computer monitor": (0.2, 0.45), "printer": (0.5, 0.4),
    "cash register": (0.4, 0.3), "machine": (1.0, 1.5), "forklift": (2.0, 2.1), "vending machine": (0.8, 1.8),
    "sofa": (0.9, 0.85), "couch": (0.9, 0.85), "bench": (0.45, 0.45), "locker": (0.5, 1.8),
    "staircase": (2.5, 2.5), "car": (4.5, 1.5), "truck": (7.0, 3.0), "bus": (11.0, 3.2),
    "bicycle": (1.7, 1.0), "motorcycle": (2.0, 1.1), "potted plant": (0.4, 0.8), "bed": (2.0, 0.6),
    "van": (5.0, 2.0), "traffic cone": (0.35, 0.5), "bollard": (0.2, 0.9), "lamp post": (0.15, 4.0),
    "sign": (0.1, 2.0), "planter": (0.6, 0.6), "tripod": (0.6, 1.5), "pillar": (0.4, 3.0), "wall": (0.2, 2.5),
    "reception desk": (0.8, 1.05), "checkout counter": (0.8, 0.95), "display case": (0.7, 1.2),
    "shopping cart": (0.9, 1.0), "hand truck": (0.5, 1.2), "storage bin": (0.5, 0.4), "ladder": (0.6, 2.0),
    "tool cabinet": (0.5, 1.0),
}  # fmt: skip
NOT_ON_FLOOR = {"window", "whiteboard", "clock", "tv", "computer monitor", "microwave", "printer", "cash register", "street sign", "stop sign"}
VEHICLES = {"car": (1.8, 4.5), "van": (2.0, 5.0), "truck": (2.5, 7.0), "bus": (2.55, 11.0), "forklift": (1.2, 2.2)}
"""(width, length) in meters."""
THIN_UPRIGHTS = {"lamp post", "sign", "bollard", "tripod", "pillar"}
"""Narrow vertical objects: the bbox width is mostly a lean/perspective artifact, so width comes from the prior."""
"""Usually wall-mounted or sitting on another object: their bbox bottom is not floor contact."""

ZONE_COLORS = ["#22c55e", "#ef4444", "#f97316", "#eab308", "#a855f7", "#06b6d4", "#ec4899", "#84cc16", "#64748b"]


@dataclass
class FloorBounds:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def depth(self) -> float:
        return self.y1 - self.y0

    def polygon(self) -> list[tuple[float, float]]:
        return [(self.x0, self.y0), (self.x1, self.y0), (self.x1, self.y1), (self.x0, self.y1)]


# --------------------------------------------------------------------------- vertical scale


def vertical_scale_fn(people: list[PersonBox], person_height_m: float) -> Callable[[float], float] | None:
    """px-per-meter (vertical) as a linear function of the foot row, fitted from standing people."""
    rows = [(p.y2, (p.y2 - p.y1) / person_height_m) for p in people if p.conf >= 0.4 and (p.y2 - p.y1) / max(p.x2 - p.x1, 1) >= 1.6]
    if len(rows) < 8:
        return None
    v, ppm = np.array(rows).T
    if np.ptp(v) < 20:
        med = float(np.median(ppm))
        return lambda _v: med
    a, b = np.polyfit(v, ppm, 1)
    lo = float(np.percentile(ppm, 2))
    return lambda vv: max(lo * 0.25, float(a * vv + b))


# --------------------------------------------------------------------------- people on the floor


@dataclass
class FloorPoint:
    t_ms: int
    track: str | None
    x: float
    y: float


def people_on_floor(people: list[PersonBox], projector: GroundPlaneProjector) -> list[FloorPoint]:
    out = []
    for p in people:
        w = projector.image_to_world((p.x1 + p.x2) / 2.0, p.y2)
        if w is not None:
            out.append(FloorPoint(p.t_ms, p.track, w.x, w.y))
    return out


def dwell_weights(points: list[FloorPoint], still_speed_mps: float = 0.35) -> np.ndarray:
    """1.0 for samples where the person is (nearly) still, 0.15 while walking; 1.0 without track ids."""
    weights = np.ones(len(points))
    by_track: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(points):
        if p.track is not None:
            by_track[p.track].append(i)
    for idx in by_track.values():
        idx.sort(key=lambda i: points[i].t_ms)
        for k, i in enumerate(idx):
            j = idx[k - 1] if k > 0 else (idx[k + 1] if k + 1 < len(idx) else None)
            if j is None:
                continue
            dt = abs(points[i].t_ms - points[j].t_ms) / 1000.0
            if dt <= 0 or dt > 2.0:
                continue
            speed = math.hypot(points[i].x - points[j].x, points[i].y - points[j].y) / dt
            weights[i] = 1.0 if speed < still_speed_mps else 0.15
    return weights


def floor_bounds(points: list[FloorPoint], fixtures: list[Fixture], margin_m: float = 1.0, max_size_m: float = 40.0) -> FloorBounds | None:
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    for f in fixtures:
        xs.append(f.center[0])
        ys.append(f.center[1])
    if len(xs) < 3:
        return None
    x0, x1 = np.percentile(xs, [1, 99])
    y0, y1 = np.percentile(ys, [1, 99])
    x0, y0, x1, y1 = x0 - margin_m, y0 - margin_m, x1 + margin_m, y1 + margin_m
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hw, hd = min(max((x1 - x0) / 2, 1.5), max_size_m / 2), min(max((y1 - y0) / 2, 1.5), max_size_m / 2)
    return FloorBounds(float(cx - hw), float(cy - hd), float(cx + hw), float(cy + hd))


# --------------------------------------------------------------------------- fixtures


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-") or "object"


def project_fixtures(
    objects: list[StaticObject],
    projector: GroundPlaneProjector,
    image_size: tuple[int, int],
    vscale: Callable[[float], float] | None,
) -> tuple[list[Fixture], list[str]]:
    w_img, h_img = image_size
    fixtures: list[Fixture] = []
    notes: list[str] = []
    counts: dict[str, int] = defaultdict(int)
    for obj in objects:
        label = obj.label
        x1, y1, x2, y2 = obj.bbox
        if label in NOT_ON_FLOOR:
            notes.append(f"'{label}' ({obj.confidence:.2f}) skipped: usually not standing on the floor")
            continue
        bottom = min(y2, h_img - 1.0)
        pl = projector.image_to_world(max(x1, 0.0), bottom)
        pr = projector.image_to_world(min(x2, w_img - 1.0), bottom)
        if pl is None or pr is None:
            notes.append(f"'{label}' skipped: its base is beyond the calibrated floor")
            continue
        ex, ey = pr.x - pl.x, pr.y - pl.y
        width = math.hypot(ex, ey)
        if width < 0.1 or width > 15.0:
            notes.append(f"'{label}' skipped: implausible width {width:.1f} m")
            continue
        # The side away from the camera: a point just above the base maps farther away.
        back = projector.image_to_world((x1 + x2) / 2.0, max(0.0, bottom - max(4.0, 0.05 * (y2 - y1))))
        mid = ((pl.x + pr.x) / 2.0, (pl.y + pr.y) / 2.0)
        nx, ny = -ey / width, ex / width
        if back is not None and (back.x - mid[0]) * nx + (back.y - mid[1]) * ny < 0:
            nx, ny = -nx, -ny

        prior_depth, prior_height = CATEGORY_PRIORS.get(label, (min(max(0.5 * width, 0.3), 1.0), 1.0))
        estimated: list[str] = ["depth"]
        depth = prior_depth
        if label in THIN_UPRIGHTS:
            width = min(width, max(prior_depth, 0.15))
            estimated.append("width")
        elif label in VEHICLES:
            vehicle_width, vehicle_length = VEHICLES[label]
            # Seen side-on, the visible base is the length and the hidden depth is the width.
            depth = vehicle_width if width > 0.6 * vehicle_length else vehicle_length
        if vscale is not None:
            height = (y2 - y1) / max(vscale(bottom), 1e-6)
            # The visible top of a deep object is its back edge; remove most of that perspective rise.
            height = float(np.clip(height * 0.8, 0.15, 4.0))
            estimated.append("height")
        else:
            height = prior_height
            estimated.append("height")
        if y2 >= h_img - 2:
            estimated.append("width")
            notes.append(f"'{label}' runs off the bottom of the frame; its footprint is partial")
        counts[label] += 1
        n = counts[label]
        fixtures.append(
            Fixture(
                fixture_id=f"{_slug(label)}-{n}",
                kind=label,
                label=f"{label.title()} {n}" if n > 1 else label.title(),
                center=(mid[0] + nx * depth / 2.0, mid[1] + ny * depth / 2.0),
                size=(round(width, 3), round(depth, 3), round(height, 3)),
                yaw_deg=round(math.degrees(math.atan2(ey, ex)), 2),
                provenance="detected",
                confidence=round(obj.confidence, 3),
                estimated_dims=estimated,  # type: ignore[arg-type]
                image_bbox=obj.bbox,
            )
        )
    for label, n in counts.items():
        if n == 1:
            continue
        first = next(f for f in fixtures if f.kind == label)
        first.label = f"{label.title()} 1"
    return fixtures, notes


# --------------------------------------------------------------------------- activity zones


def activity_zones(
    points: list[FloorPoint],
    weights: np.ndarray,
    bounds: FloorBounds,
    fixtures: list[Fixture],
    *,
    cell_m: float = 0.4,
    max_zones: int = 8,
) -> list[Zone]:
    """Proposes zones where people spent time. Names come from the nearest detected object."""
    if len(points) < 10:
        return []
    cols = max(1, int(math.ceil(bounds.width / cell_m)))
    rows = max(1, int(math.ceil(bounds.depth / cell_m)))
    grid = np.zeros((rows, cols), dtype=np.float64)
    for p, w in zip(points, weights):
        c = int((p.x - bounds.x0) / cell_m)
        r = int((p.y - bounds.y0) / cell_m)
        if 0 <= r < rows and 0 <= c < cols:
            grid[r, c] += w
    grid = cv2.GaussianBlur(grid, (3, 3), 0.8)
    nonzero = grid[grid > 0]
    if nonzero.size < 3:
        return []
    threshold = max(float(np.percentile(nonzero, 80)), 0.12 * float(grid.max()))
    mask = (grid >= threshold).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    comps = []
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 2:
            continue
        comps.append((float(grid[labels == i].sum()), i))
    comps.sort(reverse=True)

    zones: list[Zone] = []
    used_names: dict[str, int] = defaultdict(int)
    for k, (_, i) in enumerate(comps[:max_zones]):
        rr, cc = np.nonzero(labels == i)
        corners = np.array(
            [(bounds.x0 + (c + dx) * cell_m, bounds.y0 + (r + dy) * cell_m) for r, c in zip(rr, cc) for dx in (0, 1) for dy in (0, 1)],
            dtype=np.float32,
        )
        hull = cv2.convexHull(corners).reshape(-1, 2)
        polygon = [(round(float(x), 3), round(float(y), 3)) for x, y in hull]
        if len(polygon) < 3:
            continue
        cx, cy = polygon_centroid(polygon)
        name = None
        nearest = min(fixtures, key=lambda f: math.hypot(f.center[0] - cx, f.center[1] - cy), default=None)
        if nearest is not None and math.hypot(nearest.center[0] - cx, nearest.center[1] - cy) <= 2.0:
            name = f"{(nearest.label or nearest.kind).rstrip(' 0123456789')} area"
        if name is None:
            name = f"Activity area {k + 1}"
        used_names[name] += 1
        if used_names[name] > 1:
            name = f"{name} {used_names[name]}"
        zones.append(
            Zone(
                zone_id=f"zone-{k + 1}",
                name=name,
                kind="activity",
                polygon=polygon,
                color=ZONE_COLORS[k % len(ZONE_COLORS)],
                provenance="proposed",
            )
        )
    return zones


# --------------------------------------------------------------------------- floor texture


def floor_texture(plate: np.ndarray, homography_i2w: np.ndarray, bounds: FloorBounds, max_px: int = 2048) -> np.ndarray:
    """Top-down BGRA image of ``bounds`` (row 0 = north edge). Alpha 0 where the camera can't see."""
    ppm = min(100.0, max_px / max(bounds.width, bounds.depth))
    tw, th = max(2, int(round(bounds.width * ppm))), max(2, int(round(bounds.depth * ppm)))
    # texture pixel (i, j) -> world (x0 + i/ppm, y1 - j/ppm)
    tex_to_world = np.array([[1.0 / ppm, 0, bounds.x0], [0, -1.0 / ppm, bounds.y1], [0, 0, 1.0]])
    world_to_img = np.linalg.inv(homography_i2w)
    m = world_to_img @ tex_to_world
    color = cv2.warpPerspective(plate, m, (tw, th), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_CONSTANT)
    ones = np.full(plate.shape[:2], 255, np.uint8)
    alpha = cv2.warpPerspective(ones, m, (tw, th), flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_CONSTANT)
    # Points behind the camera wrap around through the horizon; drop them.
    jj, ii = np.mgrid[0:th, 0:tw]
    pts = np.stack([ii.ravel(), jj.ravel(), np.ones(ii.size)], axis=0)
    w = (m @ pts)[2].reshape(th, tw)
    sign = 1.0 if np.median(w[alpha > 0]) > 0 else -1.0 if (alpha > 0).any() else 1.0
    alpha[w * sign <= 0] = 0
    return np.dstack([color, alpha])
