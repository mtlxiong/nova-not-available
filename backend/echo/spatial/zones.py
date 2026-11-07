"""Zone membership with temporal hysteresis, producing zone enter/exit transitions."""

from __future__ import annotations

from dataclasses import dataclass

from echo.schemas import WorldPosition, Zone
from echo.spatial.geometry import point_in_polygon


def zone_at(position: WorldPosition | None, zones: list[Zone]) -> str | None:
    """First zone containing the point, in spatial-model order. Zones are expected not to overlap."""
    if position is None:
        return None
    for zone in zones:
        if point_in_polygon(position.x, position.y, zone.polygon):
            return zone.zone_id
    return None


@dataclass
class ZoneTransition:
    entity_id: str
    timestamp_ms: int
    exited: str | None
    entered: str | None


@dataclass
class _ZoneState:
    confirmed: str | None = None
    candidate: str | None = None
    candidate_since_ms: int = 0


class ZoneTracker:
    """Confirms a zone change only after the raw membership has been stable for ``min_dwell_ms``.

    Transition timestamps are the time the new membership was first observed, not when it was
    confirmed, so dwell times are not shortened by the hysteresis delay.
    """

    def __init__(self, zones: list[Zone], min_dwell_ms: int = 400):
        self.zones = zones
        self.min_dwell_ms = min_dwell_ms
        self._state: dict[str, _ZoneState] = {}

    def current(self, entity_id: str) -> str | None:
        state = self._state.get(entity_id)
        return state.confirmed if state else None

    def update(self, entity_id: str, position: WorldPosition | None, timestamp_ms: int) -> list[ZoneTransition]:
        if position is None:
            return []
        raw = zone_at(position, self.zones)
        state = self._state.setdefault(entity_id, _ZoneState(confirmed=None, candidate=None, candidate_since_ms=timestamp_ms))
        if raw == state.confirmed:
            state.candidate = raw
            state.candidate_since_ms = timestamp_ms
            return []
        if raw != state.candidate:
            state.candidate = raw
            state.candidate_since_ms = timestamp_ms
        if timestamp_ms - state.candidate_since_ms < self.min_dwell_ms:
            return []
        transition = ZoneTransition(entity_id, state.candidate_since_ms, exited=state.confirmed, entered=raw)
        state.confirmed = raw
        return [transition]

    def release(self, entity_id: str, timestamp_ms: int) -> list[ZoneTransition]:
        """Closes any open zone visit when an entity's track is removed."""
        state = self._state.pop(entity_id, None)
        if state and state.confirmed is not None:
            return [ZoneTransition(entity_id, timestamp_ms, exited=state.confirmed, entered=None)]
        return []
