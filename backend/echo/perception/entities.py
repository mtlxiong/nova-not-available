"""Session-scoped entity identity and track lifecycle.

Entities are keyed by ``(source, source_track_id)`` so tracker IDs and ArUco marker IDs can
never collide. Tracker entities get ``trk-<n>`` IDs from a session counter (independent of the
tracker's own numbering). Registered markers use their internal object ID.

A lost entity keeps its *last observed* position, flagged ``lost``; no position is extrapolated.
"""

from __future__ import annotations

from dataclasses import dataclass

from echo.perception.markers import MarkerRegistry
from echo.schemas import EntitySource, EntityUpdate, EventKind, SpatialEvent, Track, TrackingState, WorldPosition, Zone
from echo.spatial.calibration import GroundPlaneProjector
from echo.spatial.zones import ZoneTracker


@dataclass
class _Entity:
    entity_id: str
    class_name: str
    source: EntitySource
    source_track_id: str
    label: str | None
    object_id: str | None
    first_seen_ms: int
    last_seen_ms: int
    state: TrackingState = TrackingState.ACTIVE
    last_track: Track | None = None
    last_position: WorldPosition | None = None


class EntityRegistry:
    def __init__(
        self,
        session_id: str,
        zones: list[Zone],
        marker_registry: MarkerRegistry | None = None,
        lost_timeout_ms: int = 3000,
        zone_min_dwell_ms: int = 400,
    ):
        self.session_id = session_id
        self.marker_registry = marker_registry or MarkerRegistry()
        self.lost_timeout_ms = lost_timeout_ms
        self.zone_tracker = ZoneTracker(zones, min_dwell_ms=zone_min_dwell_ms)
        self._by_key: dict[tuple[EntitySource, str], _Entity] = {}
        self._next_track_number = 1

    def _new_entity(self, track: Track, t_ms: int) -> _Entity:
        label = object_id = None
        class_name = track.class_name
        if track.source == EntitySource.ARUCO and track.marker_id is not None:
            registered = self.marker_registry.lookup(track.marker_id)
            if registered:
                entity_id, object_id, label, class_name = registered.object_id, registered.object_id, registered.label, registered.class_name
            else:
                entity_id, label = f"marker-{track.marker_id}", f"Unregistered marker {track.marker_id}"
        else:
            entity_id = f"trk-{self._next_track_number}"
            self._next_track_number += 1
        return _Entity(entity_id, class_name, track.source, track.track_id, label, object_id, t_ms, t_ms)

    def _event(self, t_ms: int, kind: EventKind, entity: _Entity, zone_id: str | None = None, **data) -> SpatialEvent:
        return SpatialEvent(session_id=self.session_id, timestamp_ms=t_ms, kind=kind, entity_id=entity.entity_id, zone_id=zone_id, data=data)

    def _zone_events(self, entity: _Entity, transitions) -> list[SpatialEvent]:
        events = []
        for tr in transitions:
            if tr.exited:
                events.append(self._event(tr.timestamp_ms, EventKind.ZONE_EXIT, entity, tr.exited))
            if tr.entered:
                events.append(self._event(tr.timestamp_ms, EventKind.ZONE_ENTER, entity, tr.entered))
        return events

    def update(
        self, tracks: list[Track], timestamp_ms: int, projector: GroundPlaneProjector | None
    ) -> tuple[list[EntityUpdate], list[SpatialEvent]]:
        events: list[SpatialEvent] = []
        seen: set[tuple[EntitySource, str]] = set()

        for track in tracks:
            key = (track.source, track.track_id)
            if key in seen:
                continue
            seen.add(key)
            entity = self._by_key.get(key)
            if entity is None:
                entity = self._new_entity(track, timestamp_ms)
                self._by_key[key] = entity
                events.append(self._event(timestamp_ms, EventKind.TRACK_CREATED, entity, class_name=entity.class_name, source=entity.source.value))
            elif entity.state == TrackingState.LOST:
                events.append(self._event(timestamp_ms, EventKind.TRACK_REACQUIRED, entity, lost_for_ms=timestamp_ms - entity.last_seen_ms))
            entity.state = TrackingState.ACTIVE
            entity.last_seen_ms = timestamp_ms
            entity.last_track = track
            rp = track.reference_point
            entity.last_position = projector.image_to_world(rp.u, rp.v) if projector else None
            events.extend(self._zone_events(entity, self.zone_tracker.update(entity.entity_id, entity.last_position, timestamp_ms)))

        removed: list[_Entity] = []
        for key, entity in self._by_key.items():
            if key in seen:
                continue
            if entity.state == TrackingState.ACTIVE:
                entity.state = TrackingState.LOST
                events.append(self._event(timestamp_ms, EventKind.TRACK_LOST, entity, last_seen_ms=entity.last_seen_ms))
            if entity.state == TrackingState.LOST and timestamp_ms - entity.last_seen_ms >= self.lost_timeout_ms:
                entity.state = TrackingState.REMOVED
                events.extend(self._zone_events(entity, self.zone_tracker.release(entity.entity_id, entity.last_seen_ms)))
                events.append(self._event(timestamp_ms, EventKind.TRACK_REMOVED, entity, last_seen_ms=entity.last_seen_ms))
                removed.append(entity)

        updates = [self._to_update(e) for e in self._by_key.values()]
        for entity in removed:
            del self._by_key[(entity.source, entity.source_track_id)]
        return updates, events

    def set_zones(self, zones: list[Zone], timestamp_ms: int) -> list[SpatialEvent]:
        """Replaces the zone set (workspace re-mapped). Open zone visits are closed first."""
        events: list[SpatialEvent] = []
        for entity in self._by_key.values():
            events.extend(self._zone_events(entity, self.zone_tracker.release(entity.entity_id, timestamp_ms)))
        self.zone_tracker = ZoneTracker(zones, min_dwell_ms=self.zone_tracker.min_dwell_ms)
        return events

    def close_all(self, timestamp_ms: int) -> list[SpatialEvent]:
        """Ends every open track (session stop)."""
        events: list[SpatialEvent] = []
        for entity in self._by_key.values():
            events.extend(self._zone_events(entity, self.zone_tracker.release(entity.entity_id, min(entity.last_seen_ms, timestamp_ms))))
            events.append(self._event(timestamp_ms, EventKind.TRACK_REMOVED, entity, last_seen_ms=entity.last_seen_ms, reason="session_stopped"))
        self._by_key.clear()
        return events

    def _to_update(self, e: _Entity) -> EntityUpdate:
        t = e.last_track
        return EntityUpdate(
            entity_id=e.entity_id,
            class_name=e.class_name,
            source=e.source,
            source_track_id=e.source_track_id,
            label=e.label,
            object_id=e.object_id,
            confidence=t.confidence if t and e.state == TrackingState.ACTIVE else None,
            bbox=t.bbox if t and e.state == TrackingState.ACTIVE else None,
            image_point=t.reference_point if t and e.state == TrackingState.ACTIVE else None,
            position=e.last_position,
            zone_id=self.zone_tracker.current(e.entity_id),
            tracking_state=e.state,
            first_seen_ms=e.first_seen_ms,
            last_seen_ms=e.last_seen_ms,
        )
