"""SQLite / SQLAlchemy schema for ECHO's spatial event memory (GhostTrace)."""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now_ms() -> float:
    return time.time() * 1000.0


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class SessionRow(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_wall_ms: Mapped[float] = mapped_column(Float, default=now_ms)
    ended_wall_ms: Mapped[float | None] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String, default="running")
    source_kind: Mapped[str] = mapped_column(String)
    video_source_id: Mapped[str | None] = mapped_column(ForeignKey("video_sources.id"))
    camera_id: Mapped[str | None] = mapped_column(ForeignKey("cameras.id"))
    calibration_id: Mapped[str | None] = mapped_column(ForeignKey("calibrations.id"))
    scene_id: Mapped[str | None] = mapped_column(String)
    spatial_model_id: Mapped[str] = mapped_column(String)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    processed_frames: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String)


class VideoSourceRow(Base):
    __tablename__ = "video_sources"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    path: Mapped[str | None] = mapped_column(String)
    original_name: Mapped[str | None] = mapped_column(String)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    frame_count: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_wall_ms: Mapped[float] = mapped_column(Float, default=now_ms)


class CameraRow(Base):
    __tablename__ = "cameras"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    device_index: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)


class CalibrationRow(Base):
    __tablename__ = "calibrations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    camera_id: Mapped[str] = mapped_column(String, index=True)
    created_wall_ms: Mapped[float] = mapped_column(Float, default=now_ms)
    image_width: Mapped[int] = mapped_column(Integer)
    image_height: Mapped[int] = mapped_column(Integer)
    homography: Mapped[list[Any]] = mapped_column(JSON)
    points: Mapped[list[Any]] = mapped_column(JSON)
    reprojection_rmse_m: Mapped[float] = mapped_column(Float)
    max_error_m: Mapped[float] = mapped_column(Float)
    warnings: Mapped[list[Any]] = mapped_column(JSON, default=list)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    label: Mapped[str | None] = mapped_column(String)
    method: Mapped[str] = mapped_column(String, default="points")


class SpatialModelRow(Base):
    __tablename__ = "spatial_models"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    model: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_wall_ms: Mapped[float] = mapped_column(Float, default=now_ms)
    source: Mapped[str] = mapped_column(String, default="manual")
    active: Mapped[bool] = mapped_column(Boolean, default=False)


class ZoneRow(Base):
    __tablename__ = "zones"
    spatial_model_id: Mapped[str] = mapped_column(ForeignKey("spatial_models.id"), primary_key=True)
    zone_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    polygon: Mapped[list[Any]] = mapped_column(JSON)
    color: Mapped[str] = mapped_column(String)
    provenance: Mapped[str] = mapped_column(String, default="manual")


class SceneConfigRow(Base):
    __tablename__ = "scene_configs"
    scene_id: Mapped[str] = mapped_column(String, primary_key=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_wall_ms: Mapped[float] = mapped_column(Float, default=now_ms)


class EntityRow(Base):
    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("session_id", "entity_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    entity_id: Mapped[str] = mapped_column(String)
    class_name: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)
    source_track_id: Mapped[str] = mapped_column(String)
    label: Mapped[str | None] = mapped_column(String)
    object_id: Mapped[str | None] = mapped_column(String)
    first_seen_ms: Mapped[int] = mapped_column(Integer)
    last_seen_ms: Mapped[int] = mapped_column(Integer)


class ObservationRow(Base):
    __tablename__ = "observations"
    __table_args__ = (
        Index("ix_obs_session_entity_t", "session_id", "entity_id", "t_ms"),
        Index("ix_obs_session_t", "session_id", "t_ms"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    entity_id: Mapped[str] = mapped_column(String)
    t_ms: Mapped[int] = mapped_column(Integer)
    frame_index: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String)
    x: Mapped[float | None] = mapped_column(Float)
    y: Mapped[float | None] = mapped_column(Float)
    u: Mapped[float | None] = mapped_column(Float)
    v: Mapped[float | None] = mapped_column(Float)
    bbox: Mapped[list[Any] | None] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Float)
    zone_id: Mapped[str | None] = mapped_column(String)


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_session_t", "session_id", "t_ms"),
        Index("ix_events_session_entity", "session_id", "entity_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    t_ms: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String)
    entity_id: Mapped[str | None] = mapped_column(String)
    zone_id: Mapped[str | None] = mapped_column(String)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


# ---- order management extension points (DoorDash integration lands here) ----------------------


class OrderRow(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String, index=True)
    platform: Mapped[str] = mapped_column(String, default="doordash")
    status: Mapped[str] = mapped_column(String, default="received")
    """received | preparing | ready | packed | picked_up | cancelled"""
    created_wall_ms: Mapped[float] = mapped_column(Float, default=now_ms)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class OrderEventRow(Base):
    __tablename__ = "order_events"
    __table_args__ = (Index("ix_order_events_order_t", "order_id", "wall_ms"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id"))
    wall_ms: Mapped[float] = mapped_column(Float)
    t_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)
    """'platform' (from DoorDash API), 'manual', or 'spatial' (derived from a tracked object event)."""


class OrderEntityLinkRow(Base):
    """Explicit association between an order and a tracked object. Detection never implies this."""

    __tablename__ = "order_entity_links"
    __table_args__ = (Index("ix_order_links_session_entity", "session_id", "entity_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id"))
    entity_id: Mapped[str | None] = mapped_column(String)
    object_id: Mapped[str | None] = mapped_column(String)
    linked_wall_ms: Mapped[float] = mapped_column(Float, default=now_ms)
    unlinked_wall_ms: Mapped[float | None] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String)
    """e.g. 'marker-scan', 'manual'."""


def make_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    in_memory = url in ("sqlite://", "sqlite:///:memory:")
    engine = create_engine(url, connect_args=connect_args, **({"poolclass": StaticPool} if in_memory else {}))
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            if not in_memory:
                cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def _add_missing_columns(engine: Engine) -> None:
    """Additive-only migration: create_all() does not add columns to tables that already exist."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}"
                if default is not None:
                    ddl += f" DEFAULT {int(default) if isinstance(default, bool) else repr(default)}"
                conn.execute(text(ddl))


class Database:
    def __init__(self, url: str):
        self.url = url
        self.engine = make_engine(url)
        Base.metadata.create_all(self.engine)
        _add_missing_columns(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
