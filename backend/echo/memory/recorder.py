"""Writes tracking updates and events to the database with per-entity observation sampling."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from echo.memory.db import Database, EntityRow, EventRow, ObservationRow, SessionRow, now_ms
from echo.schemas import EntityUpdate, SpatialEvent, TrackingState, TrackingUpdate

log = logging.getLogger("echo.memory")


@dataclass
class _LastWritten:
    t_ms: int
    state: TrackingState
    zone_id: str | None


class SessionRecorder:
    """Not thread-safe; owned by the processing thread of one session."""

    def __init__(self, db: Database, session_id: str, observation_interval_ms: int = 100, commit_interval_s: float = 0.5):
        self.db = db
        self.session_id = session_id
        self.observation_interval_ms = observation_interval_ms
        self.commit_interval_s = commit_interval_s
        self._s = db.Session()
        self._entities: dict[str, EntityRow] = {}
        self._last: dict[str, _LastWritten] = {}
        self._last_commit = time.monotonic()
        self.observation_count = 0
        self.event_count = 0

    def _should_write(self, e: EntityUpdate, t_ms: int) -> bool:
        last = self._last.get(e.entity_id)
        if e.tracking_state == TrackingState.REMOVED:
            return False
        if last is None or last.state != e.tracking_state or last.zone_id != e.zone_id:
            return True
        if e.tracking_state != TrackingState.ACTIVE:
            return False
        return t_ms - last.t_ms >= self.observation_interval_ms

    def record(self, update: TrackingUpdate, events: list[SpatialEvent]) -> None:
        t = update.timestamp_ms
        for e in update.entities:
            row = self._entities.get(e.entity_id)
            if row is None:
                row = EntityRow(
                    session_id=self.session_id,
                    entity_id=e.entity_id,
                    class_name=e.class_name,
                    source=e.source.value,
                    source_track_id=e.source_track_id,
                    label=e.label,
                    object_id=e.object_id,
                    first_seen_ms=e.first_seen_ms,
                    last_seen_ms=e.last_seen_ms,
                )
                self._s.add(row)
                self._entities[e.entity_id] = row
            else:
                row.last_seen_ms = e.last_seen_ms
            if not self._should_write(e, t):
                continue
            active = e.tracking_state == TrackingState.ACTIVE
            self._s.add(
                ObservationRow(
                    session_id=self.session_id,
                    entity_id=e.entity_id,
                    t_ms=t,
                    frame_index=update.frame_index,
                    state=e.tracking_state.value,
                    x=e.position.x if active and e.position else None,
                    y=e.position.y if active and e.position else None,
                    u=e.image_point.u if e.image_point else None,
                    v=e.image_point.v if e.image_point else None,
                    bbox=[e.bbox.x1, e.bbox.y1, e.bbox.x2, e.bbox.y2] if e.bbox else None,
                    confidence=e.confidence,
                    zone_id=e.zone_id,
                )
            )
            self._last[e.entity_id] = _LastWritten(t, e.tracking_state, e.zone_id)
            self.observation_count += 1
        self.add_events(events)
        if time.monotonic() - self._last_commit >= self.commit_interval_s:
            self.commit()

    def add_events(self, events: list[SpatialEvent]) -> None:
        for ev in events:
            self._s.add(EventRow(session_id=self.session_id, t_ms=ev.timestamp_ms, kind=ev.kind.value, entity_id=ev.entity_id, zone_id=ev.zone_id, data=ev.data))
            self.event_count += 1

    def commit(self) -> None:
        try:
            self._s.commit()
        except Exception:
            log.exception("recorder commit failed; rolling back batch", extra={"session_id": self.session_id})
            self._s.rollback()
        self._last_commit = time.monotonic()

    def finish(self, *, duration_ms: int, processed_frames: int, state: str, error: str | None = None) -> None:
        self.commit()
        row = self._s.get(SessionRow, self.session_id)
        if row is not None:
            row.state = state
            row.ended_wall_ms = now_ms()
            row.duration_ms = duration_ms
            row.processed_frames = processed_frames
            row.error = error
        self.commit()
        self._s.close()
