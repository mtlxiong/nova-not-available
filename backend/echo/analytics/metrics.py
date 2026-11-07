"""Operational metrics computed only from calibrated stored observations and zone events.

Entities are anonymous session-scoped tracks. Output is ordered by entity ID, never ranked.
Metrics that need data ECHO does not have (orders, queues without a queue zone) report
``available: false`` with a reason instead of a number.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from sqlalchemy import select

from echo.memory.db import CalibrationRow, Database, EntityRow, EventRow, ObservationRow, OrderEntityLinkRow, SessionRow
from echo.memory.replay import SessionNotFound
from echo.schemas import SpatialModel

MAX_SEGMENT_GAP_MS = 1000
"""Consecutive samples further apart than this (e.g. across a lost period) are not joined."""
MAX_PLAUSIBLE_SPEED_MPS = 4.0
"""Faster implied motion is treated as a tracking glitch (ID switch, projection jump) and excluded."""
SMOOTHING_HALF_WINDOW_MS = 300
"""Distance is summed over a centered moving average; raw per-frame detection jitter otherwise inflates path length."""
HEATMAP_CELL_M = 0.25
OCCUPANCY_BIN_MS = 1000


def _contiguous_segments(samples: list[ObservationRow]) -> list[list[ObservationRow]]:
    """Splits at gaps and implausible jumps so smoothing never bridges a tracking discontinuity."""
    segments: list[list[ObservationRow]] = []
    current: list[ObservationRow] = []
    for s in samples:
        if current:
            prev = current[-1]
            dt = s.t_ms - prev.t_ms
            jump = dt <= 0 or dt > MAX_SEGMENT_GAP_MS or math.hypot(s.x - prev.x, s.y - prev.y) / (dt / 1000.0) > MAX_PLAUSIBLE_SPEED_MPS
            if jump:
                segments.append(current)
                current = []
        current.append(s)
    if current:
        segments.append(current)
    return segments


def _smooth(segment: list[ObservationRow]) -> list[tuple[int, float, float]]:
    out = []
    lo = 0
    hi = 0
    n = len(segment)
    sx = sy = 0.0
    for i, s in enumerate(segment):
        while hi < n and segment[hi].t_ms <= s.t_ms + SMOOTHING_HALF_WINDOW_MS:
            sx += segment[hi].x
            sy += segment[hi].y
            hi += 1
        while segment[lo].t_ms < s.t_ms - SMOOTHING_HALF_WINDOW_MS:
            sx -= segment[lo].x
            sy -= segment[lo].y
            lo += 1
        k = hi - lo
        out.append((s.t_ms, sx / k, sy / k))
    return out


def _floor_bounds(model: SpatialModel) -> tuple[float, float, float, float]:
    xs = [p[0] for p in model.floor_polygon]
    ys = [p[1] for p in model.floor_polygon]
    return min(xs), min(ys), max(xs), max(ys)


def compute_session_metrics(db: Database, session_id: str, model: SpatialModel) -> dict[str, Any]:
    with db.Session() as s:
        session = s.get(SessionRow, session_id)
        if session is None:
            raise SessionNotFound(session_id)
        entities = {e.entity_id: e for e in s.scalars(select(EntityRow).where(EntityRow.session_id == session_id)).all()}
        obs = s.scalars(
            select(ObservationRow).where(ObservationRow.session_id == session_id).order_by(ObservationRow.entity_id, ObservationRow.t_ms, ObservationRow.id)
        ).all()
        events = s.scalars(select(EventRow).where(EventRow.session_id == session_id).order_by(EventRow.t_ms, EventRow.id)).all()
        calibration = s.get(CalibrationRow, session.calibration_id) if session.calibration_id else None
        order_links = s.scalars(select(OrderEntityLinkRow).where(OrderEntityLinkRow.session_id == session_id)).all()

    duration_ms = session.duration_ms or max([o.t_ms for o in obs] + [0])
    positioned = [o for o in obs if o.state == "active" and o.x is not None]
    calibrated = bool(positioned)

    by_entity: dict[str, list[ObservationRow]] = defaultdict(list)
    for o in positioned:
        by_entity[o.entity_id].append(o)

    # -- movement --------------------------------------------------------------------------
    movement: dict[str, dict[str, Any]] = {}
    heat_x0, heat_y0, heat_x1, heat_y1 = _floor_bounds(model)
    nx = max(1, math.ceil((heat_x1 - heat_x0) / HEATMAP_CELL_M))
    ny = max(1, math.ceil((heat_y1 - heat_y0) / HEATMAP_CELL_M))
    heat = [[0.0] * nx for _ in range(ny)]

    for eid, samples in by_entity.items():
        dist = 0.0
        tracked_ms = 0
        segments = _contiguous_segments(samples)
        excluded = sum(
            1
            for a, b in zip(segments, segments[1:])
            if 0 < b[0].t_ms - a[-1].t_ms <= MAX_SEGMENT_GAP_MS
        )
        for segment in segments:
            smoothed = _smooth(segment)
            for (ta, xa, ya), (tb, xb, yb) in zip(smoothed, smoothed[1:]):
                dt = tb - ta
                dist += math.hypot(xb - xa, yb - ya)
                tracked_ms += dt
                cx = int((xa - heat_x0) / HEATMAP_CELL_M)
                cy = int((ya - heat_y0) / HEATMAP_CELL_M)
                if 0 <= cx < nx and 0 <= cy < ny:
                    heat[cy][cx] += dt / 1000.0
        movement[eid] = {
            "distance_m": round(dist, 3),
            "tracked_time_s": round(tracked_ms / 1000.0, 2),
            "mean_speed_mps": round(dist / (tracked_ms / 1000.0), 3) if tracked_ms > 0 else None,
            "excluded_segments": excluded,
            "positioned_samples": len(samples),
        }

    # -- zone visits from events ---------------------------------------------------------------
    zone_ids = [z.zone_id for z in model.zones]
    open_visits: dict[tuple[str, str], int] = {}
    visits: list[dict[str, Any]] = []
    for ev in events:
        if ev.kind == "zone.enter" and ev.entity_id and ev.zone_id:
            open_visits[(ev.entity_id, ev.zone_id)] = ev.t_ms
        elif ev.kind == "zone.exit" and ev.entity_id and ev.zone_id:
            start = open_visits.pop((ev.entity_id, ev.zone_id), None)
            if start is not None:
                visits.append({"entity_id": ev.entity_id, "zone_id": ev.zone_id, "start_ms": start, "end_ms": ev.t_ms, "closed": True})
    for (eid, zid), start in open_visits.items():
        end = entities[eid].last_seen_ms if eid in entities else duration_ms
        visits.append({"entity_id": eid, "zone_id": zid, "start_ms": start, "end_ms": max(start, end), "closed": False})

    dwell_by_entity: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    zone_stats: dict[str, dict[str, Any]] = {zid: {"visits": 0, "total_dwell_s": 0.0, "entities": set(), "dwells": []} for zid in zone_ids}
    for v in visits:
        dwell_s = (v["end_ms"] - v["start_ms"]) / 1000.0
        dwell_by_entity[v["entity_id"]][v["zone_id"]] += dwell_s
        zs = zone_stats.setdefault(v["zone_id"], {"visits": 0, "total_dwell_s": 0.0, "entities": set(), "dwells": []})
        zs["visits"] += 1
        zs["total_dwell_s"] += dwell_s
        zs["entities"].add(v["entity_id"])
        zs["dwells"].append(dwell_s)

    # -- occupancy over time -------------------------------------------------------------------
    bins = max(1, math.ceil(duration_ms / OCCUPANCY_BIN_MS)) if duration_ms else 0
    occupancy: dict[str, list[int]] = {zid: [0] * bins for zid in zone_ids}
    half = OCCUPANCY_BIN_MS // 2
    for v in visits:
        series = occupancy.get(v["zone_id"])
        if series is None:
            continue
        # bin b is occupied when start <= b*BIN + half < end
        first = max(0, math.ceil((v["start_ms"] - half) / OCCUPANCY_BIN_MS))
        last = min(bins - 1, math.ceil((v["end_ms"] - half) / OCCUPANCY_BIN_MS) - 1)
        for b in range(first, last + 1):
            series[b] += 1

    zones_out = []
    for z in model.zones:
        zs = zone_stats.get(z.zone_id, {"visits": 0, "total_dwell_s": 0.0, "entities": set(), "dwells": []})
        occ = occupancy.get(z.zone_id, [])
        zones_out.append(
            {
                "zone_id": z.zone_id,
                "name": z.name,
                "kind": z.kind,
                "visits": zs["visits"],
                "unique_entities": len(zs["entities"]),
                "total_dwell_s": round(zs["total_dwell_s"], 2),
                "mean_dwell_s": round(sum(zs["dwells"]) / len(zs["dwells"]), 2) if zs["dwells"] else None,
                "max_occupancy": max(occ) if occ else 0,
                "mean_occupancy": round(sum(occ) / len(occ), 3) if occ else 0.0,
                "occupancy_series": occ,
            }
        )

    entities_out = []
    for eid in sorted(entities):
        e = entities[eid]
        entities_out.append(
            {
                "entity_id": eid,
                "class_name": e.class_name,
                "label": e.label,
                "first_seen_ms": e.first_seen_ms,
                "last_seen_ms": e.last_seen_ms,
                **movement.get(eid, {"distance_m": None, "tracked_time_s": 0.0, "mean_speed_mps": None, "excluded_segments": 0, "positioned_samples": 0}),
                "zone_dwell_s": {k: round(v, 2) for k, v in sorted(dwell_by_entity.get(eid, {}).items())},
            }
        )

    queue_zones = [z.zone_id for z in model.zones if z.kind == "queue"]
    return {
        "session_id": session_id,
        "simulated": session.simulated,
        "duration_ms": duration_ms,
        "data_quality": {
            "calibrated": calibrated,
            "calibration_id": session.calibration_id,
            "calibration_rmse_m": calibration.reprojection_rmse_m if calibration else None,
            "observations": len(obs),
            "positioned_observations": len(positioned),
            "notes": [] if calibrated else ["no calibrated positions recorded: metric distances and zone metrics are unavailable"],
        },
        "entities": entities_out,
        "zones": zones_out,
        "zone_visits": sorted(visits, key=lambda v: (v["start_ms"], v["entity_id"])),
        "heatmap": {
            "origin": [heat_x0, heat_y0],
            "cell_m": HEATMAP_CELL_M,
            "cols": nx,
            "rows": ny,
            "seconds": [[round(c, 2) for c in row] for row in heat],
        },
        "occupancy_bin_ms": OCCUPANCY_BIN_MS,
        "queues": {"available": bool(queue_zones), "zone_ids": queue_zones}
        if queue_zones
        else {"available": False, "reason": "no zones of kind 'queue' in the spatial model"},
        "orders": {"available": bool(order_links), "linked_entities": len(order_links)}
        if order_links
        else {"available": False, "reason": "no orders linked to tracked objects in this session"},
    }
