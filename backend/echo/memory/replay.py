"""Reconstructs movement history from stored observations. Never reruns computer vision."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from echo.memory.db import CalibrationRow, Database, EntityRow, EventRow, ObservationRow, SessionRow


class SessionNotFound(LookupError):
    pass


def session_summary(row: SessionRow) -> dict[str, Any]:
    return {
        "session_id": row.id,
        "created_wall_ms": row.created_wall_ms,
        "ended_wall_ms": row.ended_wall_ms,
        "state": row.state,
        "source_kind": row.source_kind,
        "video_source_id": row.video_source_id,
        "calibration_id": row.calibration_id,
        "scene_id": row.scene_id,
        "spatial_model_id": row.spatial_model_id,
        "simulated": row.simulated,
        "duration_ms": row.duration_ms,
        "processed_frames": row.processed_frames,
        "error": row.error,
    }


def entity_history(db: Database, session_id: str, entity_id: str) -> list[dict[str, Any]]:
    with db.Session() as s:
        rows = s.scalars(
            select(ObservationRow)
            .where(ObservationRow.session_id == session_id, ObservationRow.entity_id == entity_id)
            .order_by(ObservationRow.t_ms, ObservationRow.id)
        ).all()
        return [
            {"t_ms": r.t_ms, "state": r.state, "x": r.x, "y": r.y, "zone_id": r.zone_id, "confidence": r.confidence, "bbox": r.bbox}
            for r in rows
        ]


def build_replay(db: Database, session_id: str) -> dict[str, Any]:
    with db.Session() as s:
        session = s.get(SessionRow, session_id)
        if session is None:
            raise SessionNotFound(session_id)
        entities = s.scalars(select(EntityRow).where(EntityRow.session_id == session_id).order_by(EntityRow.first_seen_ms, EntityRow.entity_id)).all()
        observations = s.scalars(
            select(ObservationRow).where(ObservationRow.session_id == session_id).order_by(ObservationRow.entity_id, ObservationRow.t_ms, ObservationRow.id)
        ).all()
        events = s.scalars(select(EventRow).where(EventRow.session_id == session_id).order_by(EventRow.t_ms, EventRow.id)).all()
        calibration = s.get(CalibrationRow, session.calibration_id) if session.calibration_id else None

        samples: dict[str, list[list[Any]]] = {}
        for o in observations:
            samples.setdefault(o.entity_id, []).append([o.t_ms, o.x, o.y, o.state, o.zone_id])

        duration = session.duration_ms or max([o.t_ms for o in observations] + [e.t_ms for e in events] + [0])
        return {
            "session": session_summary(session) | {"duration_ms": duration},
            "calibration": None
            if calibration is None
            else {"calibration_id": calibration.id, "reprojection_rmse_m": calibration.reprojection_rmse_m, "simulated": calibration.simulated},
            "sample_fields": ["t_ms", "x", "y", "state", "zone_id"],
            "entities": [
                {
                    "entity_id": e.entity_id,
                    "class_name": e.class_name,
                    "source": e.source,
                    "label": e.label,
                    "object_id": e.object_id,
                    "first_seen_ms": e.first_seen_ms,
                    "last_seen_ms": e.last_seen_ms,
                    "samples": samples.get(e.entity_id, []),
                }
                for e in entities
            ],
            "events": [
                {"t_ms": e.t_ms, "kind": e.kind, "entity_id": e.entity_id, "zone_id": e.zone_id, "data": e.data} for e in events
            ],
        }


def position_at(samples: list[list[Any]], t_ms: int) -> tuple[float, float] | None:
    """Linear interpolation between consecutive *active, positioned* samples. ``None`` across gaps."""
    prev = None
    for sample in samples:
        st, x, y, state = sample[0], sample[1], sample[2], sample[3]
        if st > t_ms:
            if prev is None or state != "active" or x is None:
                return None
            pt, px, py = prev
            a = (t_ms - pt) / (st - pt) if st != pt else 0.0
            return (px + (x - px) * a, py + (y - py) * a)
        prev = (st, x, y) if state == "active" and x is not None else None
    if prev is not None and prev[0] == t_ms:
        return (prev[1], prev[2])
    return None
