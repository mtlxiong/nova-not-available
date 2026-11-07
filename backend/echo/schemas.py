"""Typed wire and domain schemas shared by the pipeline, API and persistence layers."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- geometry


class WorldPosition(_Model):
    """Position on the calibrated ground plane of the authoritative spatial model."""

    x: float
    y: float
    unit: Literal["m"] = "m"


class ImagePoint(_Model):
    u: float
    v: float


class BBox(_Model):
    """Axis-aligned bounding box in source-image pixels."""

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def bottom_center(self) -> ImagePoint:
        return ImagePoint(u=(self.x1 + self.x2) / 2.0, v=self.y2)

    @property
    def center(self) -> ImagePoint:
        return ImagePoint(u=(self.x1 + self.x2) / 2.0, v=(self.y1 + self.y2) / 2.0)


# --------------------------------------------------------------------------- perception


class EntitySource(str, Enum):
    YOLO = "yolo"
    ARUCO = "aruco"
    SIMULATION = "simulation"


class TrackingState(str, Enum):
    ACTIVE = "active"
    LOST = "lost"
    REMOVED = "removed"


class Detection(_Model):
    """A single per-frame detection before identity assignment."""

    bbox: BBox
    confidence: float = Field(ge=0.0, le=1.0)
    class_id: int
    class_name: str


class Track(_Model):
    """Tracker output for one frame. ``track_id`` is only unique within ``source``."""

    source: EntitySource
    track_id: str
    bbox: BBox
    confidence: float = Field(ge=0.0, le=1.0)
    class_name: str
    reference_point: ImagePoint
    """Image point used for ground-plane projection (bbox bottom-center for people, marker center for markers)."""
    marker_id: int | None = None


class EntityUpdate(_Model):
    entity_id: str
    class_name: str
    source: EntitySource
    source_track_id: str
    label: str | None = None
    object_id: str | None = None
    confidence: float | None = None
    bbox: BBox | None = None
    image_point: ImagePoint | None = None
    position: WorldPosition | None = None
    """``None`` when no valid calibration exists. Never filled in by guessing."""
    zone_id: str | None = None
    tracking_state: TrackingState
    first_seen_ms: int
    last_seen_ms: int


class PerfStats(_Model):
    capture_fps: float = 0.0
    process_fps: float = 0.0
    inference_ms: float = 0.0
    tracking_ms: float = 0.0
    pipeline_ms: float = 0.0
    dropped_frames: int = 0
    queue_depth: int = 0


class TrackingUpdate(_Model):
    type: Literal["tracking.update"] = "tracking.update"
    session_id: str
    frame_index: int
    timestamp_ms: int
    """Media time: video position for files, time since session start for live/simulation."""
    capture_wall_ms: float
    """Server wall-clock (epoch ms) at frame capture, for end-to-end latency measurement."""
    emitted_wall_ms: float
    frame_width: int
    frame_height: int
    calibrated: bool
    simulated: bool
    entities: list[EntityUpdate]
    perf: PerfStats | None = None


class EventKind(str, Enum):
    SESSION_STARTED = "session.started"
    SESSION_STOPPED = "session.stopped"
    TRACK_CREATED = "track.created"
    TRACK_LOST = "track.lost"
    TRACK_REACQUIRED = "track.reacquired"
    TRACK_REMOVED = "track.removed"
    ZONE_ENTER = "zone.enter"
    ZONE_EXIT = "zone.exit"


class SpatialEvent(_Model):
    session_id: str
    timestamp_ms: int
    kind: EventKind
    entity_id: str | None = None
    zone_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class EventMessage(_Model):
    type: Literal["event"] = "event"
    event: SpatialEvent


class SessionStatusMessage(_Model):
    type: Literal["session.status"] = "session.status"
    session_id: str | None
    state: Literal["idle", "starting", "running", "stopping", "stopped", "error"]
    source_kind: str | None = None
    simulated: bool = False
    calibrated: bool = False
    message: str | None = None
    spatial_model_id: str | None = None


# --------------------------------------------------------------------------- spatial model


Point2 = tuple[float, float]


Provenance = Literal["template", "detected", "proposed", "manual"]


class Zone(_Model):
    zone_id: str
    name: str
    kind: Literal["workstation", "queue", "pickup", "storage", "aisle", "activity", "other"] = "workstation"
    polygon: list[Point2]
    color: str = "#38bdf8"
    provenance: Provenance = "manual"
    """`proposed` zones come from observed activity and should be confirmed/renamed by a person."""

    @field_validator("polygon")
    @classmethod
    def _polygon_has_area(cls, value: list[Point2]) -> list[Point2]:
        if len(value) < 3:
            raise ValueError("zone polygon needs at least 3 vertices")
        return value


class ReferencePoint(_Model):
    point_id: str
    label: str
    x: float
    y: float


class Fixture(_Model):
    """A static object in the workspace (counter, shelf, desk, machine...). Visual + navigability only."""

    fixture_id: str
    kind: str = "counter"
    """Free-form category; detected fixtures use the detector's label."""
    label: str | None = None
    center: Point2
    size: tuple[float, float, float]
    """width (x), depth (y), height (z) in meters."""
    yaw_deg: float = 0.0
    zone_id: str | None = None
    provenance: Provenance = "manual"
    confidence: float | None = None
    estimated_dims: list[Literal["width", "depth", "height"]] = Field(default_factory=list)
    """Dimensions that were assumed rather than measured from the image."""
    image_bbox: tuple[float, float, float, float] | None = None
    """Where the object was detected in the analysis frame (x1, y1, x2, y2), if detected."""


class FloorTexture(_Model):
    """Top-down image of the real floor, warped from the camera's background plate."""

    url: str
    origin: Point2
    """World position of the texture's south-west (bottom-left) corner."""
    size_m: Point2


class SpatialModel(_Model):
    model_id: str
    name: str
    width_m: float = Field(gt=0)
    depth_m: float = Field(gt=0)
    floor_polygon: list[Point2]
    navigable_regions: list[list[Point2]] = Field(default_factory=list)
    zones: list[Zone]
    fixtures: list[Fixture] = Field(default_factory=list)
    reference_points: list[ReferencePoint] = Field(default_factory=list)
    source: Literal["template", "analyzed", "manual", "blank"] = "manual"
    notes: list[str] = Field(default_factory=list)
    camera_id: str | None = None
    calibration_id: str | None = None
    floor_texture: FloorTexture | None = None
    background_url: str | None = None


# --------------------------------------------------------------------------- calibration / alignment


class CalibrationPoint(_Model):
    image: tuple[float, float]
    world: tuple[float, float]


class CalibrationRequest(_Model):
    camera_id: str = "default"
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    points: list[CalibrationPoint]
    max_rmse_m: float = Field(default=0.25, gt=0)
    label: str | None = None


class CalibrationResult(_Model):
    calibration_id: str | None = None
    camera_id: str
    image_width: int
    image_height: int
    homography: list[list[float]]
    """3x3 matrix mapping homogeneous image pixels to world meters on the ground plane."""
    reprojection_rmse_m: float
    max_error_m: float
    point_errors_m: list[float]
    points: list[CalibrationPoint]
    warnings: list[str] = Field(default_factory=list)
    simulated: bool = False
    label: str | None = None
    method: Literal["points", "simulated", "auto-person-height"] = "points"
    """`auto-person-height` is an approximate single-view estimate, not a surveyed calibration."""


class Alignment(_Model):
    """World (meters, z-up, ground plane x/y) to Three.js render (y-up) transform."""

    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    yaw_deg: float = 0.0
    scale: float = Field(default=1.0, gt=0)


class SceneConfig(_Model):
    scene_id: str
    alignment: Alignment = Field(default_factory=Alignment)
    show_collider: bool = False
    splat_visible: bool = True


# --------------------------------------------------------------------------- source configuration


class SourceKind(str, Enum):
    WEBCAM = "webcam"
    FILE = "file"
    SIMULATION = "simulation"


class WorkspaceAnalysisRequest(_Model):
    """Analyze footage of a workspace into a new spatial model. Exactly one source is used:
    ``video_id`` if given, otherwise the running session's buffered frames."""

    video_id: str | None = None
    calibration_id: str | None = None
    """Use this measured calibration. None = approximate auto-calibration from person heights."""
    model_id: str | None = None
    """Re-analyze into an existing workspace id (replaces it)."""
    name: str | None = None
    vocabulary: list[str] | None = None
    """Object names to look for. None = a general workspace vocabulary."""
    person_height_m: float = Field(default=1.70, gt=1.0, lt=2.3)
    hfov_deg: float = Field(default=65.0, gt=20.0, lt=150.0)
    object_confidence: float = Field(default=0.15, gt=0.0, lt=1.0)
    max_frames: int = Field(default=80, ge=10, le=300)


class SessionConfig(_Model):
    source: SourceKind = SourceKind.SIMULATION
    camera_index: int = 0
    file_id: str | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    capture_fps: float | None = Field(default=None, gt=0)
    inference_fps: float = Field(default=10.0, gt=0)
    """Target processing rate. Live sources drop frames above this; files are sampled at this media rate."""
    realtime: bool = True
    """Files only: pace processing to media time. Off = process as fast as possible."""
    detector: Literal["yolo", "none"] = "yolo"
    tracker: Literal["bytetrack", "iou"] = "bytetrack"
    classes: list[str] = Field(default_factory=lambda: ["person"])
    confidence: float = Field(default=0.35, ge=0.0, le=1.0)
    enable_aruco: bool = True
    calibration_id: str | None = None
    spatial_model_id: str | None = None
    """None = the active workspace model. Simulation always uses its own kitchen template."""
    scene_id: str = "procedural"
    simulation_seed: int = 7
    simulation_entities: int = Field(default=4, ge=1, le=32)
    simulation_duration_s: float | None = Field(default=None, gt=0)
    stream_frames: bool = True
    observation_interval_ms: int = Field(default=100, ge=0)
    lost_timeout_ms: int = Field(default=3000, ge=0)
