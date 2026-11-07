"""Deterministic kitchen simulation. Clearly labeled as simulated everywhere it surfaces.

Actors follow fixed workstation routes in world meters. Each frame is rendered through a known
virtual-camera homography, so the rest of the pipeline (tracker, ArUco, calibration, zones,
persistence) runs exactly as it would on real video. Only the detector is replaced: rendered
people are not realistic enough for YOLO, so ``SimulationDetector`` reads jittered ground-truth
boxes instead. ArUco markers are drawn into the frame and detected for real.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from echo.schemas import BBox, SpatialModel
from echo.spatial.calibration import project
from echo.spatial.geometry import polygon_centroid
from echo.video.sources import Frame, VideoSource

Waypoint = tuple[tuple[float, float], float]
"""((x, y) meters, dwell seconds)."""


@dataclass
class SimActor:
    actor_id: str
    kind: str
    route: list[Waypoint] = field(default_factory=list)
    speed_mps: float = 1.1
    start_offset_s: float = 0.0
    occlusions: list[tuple[float, float]] = field(default_factory=list)
    attached_to: str | None = None
    attach_offset: tuple[float, float] = (0.0, 0.0)
    marker_id: int | None = None
    color: tuple[int, int, int] = (90, 160, 240)
    _segments: list[tuple[float, float, tuple[float, float], tuple[float, float]]] = field(default_factory=list, repr=False)
    _cycle_s: float = 0.0

    def build(self) -> None:
        if not self.route:
            return
        t = 0.0
        segments = []
        n = len(self.route)
        for i in range(n):
            (p, dwell) = self.route[i]
            q = self.route[(i + 1) % n][0]
            if dwell > 0:
                segments.append((t, t + dwell, p, p))
                t += dwell
            dist = math.hypot(q[0] - p[0], q[1] - p[1])
            if dist > 0:
                dur = dist / self.speed_mps
                segments.append((t, t + dur, p, q))
                t += dur
        self._segments = segments
        self._cycle_s = t

    def position(self, t_s: float) -> tuple[float, float]:
        if not self._segments:
            raise ValueError(f"actor {self.actor_id} has no route")
        local = (t_s + self.start_offset_s) % self._cycle_s
        for t0, t1, p, q in self._segments:
            if local < t1:
                a = 0.0 if t1 == t0 else (local - t0) / (t1 - t0)
                return (p[0] + (q[0] - p[0]) * a, p[1] + (q[1] - p[1]) * a)
        return self._segments[-1][3]

    def visible(self, t_s: float) -> bool:
        return not any(a <= t_s < b for a, b in self.occlusions)


@dataclass
class SimTruth:
    actor_id: str
    kind: str
    world: tuple[float, float]
    bbox: BBox | None
    visible: bool
    marker_id: int | None = None


def zone_center(model: SpatialModel, zone_id: str) -> tuple[float, float]:
    zone = next(z for z in model.zones if z.zone_id == zone_id)
    return polygon_centroid(zone.polygon)


def default_actors(model: SpatialModel, count: int = 4) -> list[SimActor]:
    """Kitchen workflow routes. Aisle waypoints keep paths out of fixtures."""
    def c(zid: str, dx: float = 0.0, dy: float = 0.0) -> tuple[float, float]:
        x, y = zone_center(model, zid)
        return (x + dx, y + dy)

    # Actors sharing a station stand at distinct spots (as people do); fully overlapping boxes
    # would make any tracker swap IDs.
    actors = [
        SimActor("cook-1", "person", [(c("prep", -0.6), 4.0), ((3.2, 3.8), 0.0), (c("grill", -0.4), 5.0), ((4.5, 3.8), 0.0), ((5.9, 3.0), 0.0), ((5.9, 2.2), 0.0), (c("expo", 0.4), 3.0), ((3.2, 2.2), 0.0), ((2.8, 3.6), 0.0)], speed_mps=1.0, color=(70, 200, 90)),
        SimActor("cook-2", "person", [(c("fry"), 5.0), ((6.4, 3.4), 0.0), ((5.9, 2.2), 0.0), (c("expo", -0.5), 3.0), ((2.6, 2.4), 0.0), (c("packing", 0.1, 0.6), 4.0), ((3.0, 3.6), 0.0), ((6.0, 3.8), 0.0)], speed_mps=1.2, start_offset_s=3.0, occlusions=[(14.0, 15.5)], color=(60, 140, 240)),
        SimActor("packer-1", "person", [(c("packing", 0.0, -0.4), 5.0), ((2.6, 1.55), 0.0), ((6.6, 1.55), 0.0), (c("pickup", 0.0, -0.3), 3.0), ((6.6, 1.55), 0.0), ((2.6, 1.55), 0.0)], speed_mps=1.1, start_offset_s=1.0, color=(220, 90, 200)),
        SimActor("dish-1", "person", [(c("dish"), 6.0), ((2.0, 2.6), 0.0), ((1.9, 3.9), 0.0), (c("prep", 0.7), 3.0), ((2.0, 2.6), 0.0)], speed_mps=0.9, start_offset_s=2.0, color=(150, 150, 150)),
        SimActor("cook-3", "person", [(c("grill", 0.5), 4.0), ((5.8, 3.8), 0.0), (c("fry", 0.5), 4.0), ((5.8, 3.8), 0.0)], speed_mps=1.0, color=(40, 120, 230)),
        SimActor("courier-1", "person", [((8.0, 0.2), 6.0), (c("pickup", 0.0, 0.5), 4.0), ((8.0, 0.2), 0.0)], speed_mps=1.3, start_offset_s=5.0, color=(240, 200, 60)),
    ]
    people = actors[: max(1, min(count, len(actors)))]
    bag = SimActor(
        "order-bag-A",
        "order_bag",
        attached_to="packer-1",
        attach_offset=(0.3, 0.2),
        marker_id=7,
    )
    result = people + ([bag] if any(a.actor_id == "packer-1" for a in people) else [])
    for actor in result:
        actor.build()
    return result


def default_sim_homography(width: int, height: int, model: SpatialModel) -> np.ndarray:
    """World->image homography of an elevated camera on the south wall looking north."""
    w, h = model.width_m, model.depth_m
    world = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
    image = np.array(
        [[0.06 * width, 0.97 * height], [0.94 * width, 0.97 * height], [0.76 * width, 0.30 * height], [0.24 * width, 0.30 * height]],
        dtype=np.float32,
    )
    return cv2.getPerspectiveTransform(world, image).astype(np.float64)


class SimulationSource(VideoSource):
    kind = "simulation"
    is_live = False
    simulated = True

    def __init__(
        self,
        model: SpatialModel,
        *,
        fps: float = 10.0,
        width: int = 1280,
        height: int = 720,
        entities: int = 4,
        duration_s: float | None = None,
        seed: int = 7,
    ):
        super().__init__()
        self.model = model
        self.fps = fps
        self.width = width
        self.height = height
        self.duration_s = duration_s
        self.seed = seed
        self.actors = default_actors(model, entities)
        self.homography = default_sim_homography(width, height, model)
        self._index = 0
        self._background: np.ndarray | None = None
        self._aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        self._marker_cache: dict[tuple[int, int], np.ndarray] = {}

    # -- geometry helpers ------------------------------------------------------
    def to_image(self, x: float, y: float) -> tuple[float, float]:
        pts, _ = project(self.homography, np.array([[x, y]], dtype=np.float64))
        return float(pts[0, 0]), float(pts[0, 1])

    def _pixels_per_meter(self, x: float, y: float) -> float:
        a = self.to_image(x - 0.5, y)
        b = self.to_image(x + 0.5, y)
        return math.hypot(b[0] - a[0], b[1] - a[1])

    def actor_position(self, actor: SimActor, t_s: float) -> tuple[float, float]:
        if actor.attached_to:
            parent = next(a for a in self.actors if a.actor_id == actor.attached_to)
            px, py = parent.position(t_s)
            return (px + actor.attach_offset[0], py + actor.attach_offset[1])
        return actor.position(t_s)

    def truth_at(self, t_s: float) -> list[SimTruth]:
        out = []
        for actor in self.actors:
            x, y = self.actor_position(actor, t_s)
            parent = next((a for a in self.actors if a.actor_id == actor.attached_to), None)
            visible = actor.visible(t_s) and (parent.visible(t_s) if parent else True)
            bbox = None
            if actor.kind == "person":
                u, v = self.to_image(x, y)
                ppm = self._pixels_per_meter(x, y)
                bh, bw = 1.55 * ppm, 0.5 * ppm
                bbox = BBox(x1=u - bw / 2, y1=v - bh, x2=u + bw / 2, y2=v)
            out.append(SimTruth(actor.actor_id, actor.kind, (x, y), bbox, visible, actor.marker_id))
        return out

    # -- rendering -------------------------------------------------------------
    def _poly(self, polygon) -> np.ndarray:
        pts, _ = project(self.homography, np.array(polygon, dtype=np.float64))
        return pts.astype(np.int32)

    def _render_background(self) -> np.ndarray:
        img = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        img[:] = (38, 32, 28)
        cv2.fillPoly(img, [self._poly(self.model.floor_polygon)], (72, 70, 66))
        for zone in self.model.zones:
            color = tuple(int(zone.color[i : i + 2], 16) for i in (5, 3, 1))
            cv2.polylines(img, [self._poly(zone.polygon)], True, color, 2, cv2.LINE_AA)
            cu, cv_ = self.to_image(*polygon_centroid(zone.polygon))
            cv2.putText(img, zone.name, (int(cu) - 30, int(cv_)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
        for fx in self.model.fixtures:
            (cx, cy), (sx, sy, _) = fx.center, fx.size
            footprint = [(cx - sx / 2, cy - sy / 2), (cx + sx / 2, cy - sy / 2), (cx + sx / 2, cy + sy / 2), (cx - sx / 2, cy + sy / 2)]
            cv2.fillPoly(img, [self._poly(footprint)], (120, 118, 112))
        cv2.putText(img, "SIMULATED FEED", (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (60, 200, 255), 2, cv2.LINE_AA)
        return img

    def _marker(self, marker_id: int, size: int) -> np.ndarray:
        key = (marker_id, size)
        if key not in self._marker_cache:
            inner = cv2.aruco.generateImageMarker(self._aruco_dict, marker_id, size)
            pad = max(4, size // 6)
            tile = cv2.copyMakeBorder(inner, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
            self._marker_cache[key] = cv2.cvtColor(tile, cv2.COLOR_GRAY2BGR)
        return self._marker_cache[key]

    def render(self, t_s: float, truth: list[SimTruth]) -> np.ndarray:
        if self._background is None:
            self._background = self._render_background()
        img = self._background.copy()
        people = sorted((t for t in truth if t.kind == "person" and t.visible), key=lambda t: t.bbox.y2 if t.bbox else 0)
        for item in people:
            actor = next(a for a in self.actors if a.actor_id == item.actor_id)
            b = item.bbox
            assert b is not None
            x1, y1, x2, y2 = int(b.x1), int(b.y1), int(b.x2), int(b.y2)
            head_r = max(3, int((x2 - x1) * 0.32))
            cv2.rectangle(img, (x1, y1 + 2 * head_r), (x2, y2), actor.color, -1)
            cv2.circle(img, ((x1 + x2) // 2, y1 + head_r), head_r, (180, 200, 230), -1)
        for item in truth:
            if item.marker_id is None or not item.visible:
                continue
            u, v = self.to_image(*item.world)
            size = max(36, int(self._pixels_per_meter(*item.world) * 0.28))
            tile = self._marker(item.marker_id, size)
            th, tw = tile.shape[:2]
            x0, y0 = int(round(u - tw / 2)), int(round(v - th / 2))
            if x0 < 0 or y0 < 0 or x0 + tw > self.width or y0 + th > self.height:
                continue
            img[y0 : y0 + th, x0 : x0 + tw] = tile
        cv2.putText(img, f"t={t_s:6.1f}s", (self.width - 150, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1, cv2.LINE_AA)
        return img

    # -- VideoSource -----------------------------------------------------------
    def open(self) -> None:
        self._index = 0
        self._opened = True

    def read(self) -> Frame | None:
        t_s = self._index / self.fps
        if self.duration_s is not None and t_s > self.duration_s:
            return None
        truth = self.truth_at(t_s)
        frame = Frame(
            index=self._index,
            timestamp_ms=int(round(t_s * 1000)),
            capture_wall_ms=time.time() * 1000.0,
            image=self.render(t_s, truth),
            truth=truth,
        )
        self._index += 1
        return frame

    def close(self) -> None:
        self._opened = False
