import pytest

from echo.perception.entities import EntityRegistry
from echo.perception.markers import MarkerRegistry, RegisteredObject
from echo.schemas import BBox, CalibrationPoint, CalibrationRequest, EntitySource, EventKind, ImagePoint, Track, TrackingState, Zone
from echo.spatial.calibration import GroundPlaneProjector, compute_calibration
from echo.spatial.geometry import rect

ZONES = [Zone(zone_id="z", name="Z", polygon=rect(0, 0, 5, 5))]


def _projector():
    # identity-like mapping: 100 px per meter
    pts = [((0, 0), (0, 0)), ((500, 0), (5, 0)), ((500, 500), (5, 5)), ((0, 500), (0, 5)), ((250, 100), (2.5, 1))]
    req = CalibrationRequest(image_width=600, image_height=600, points=[CalibrationPoint(image=i, world=w) for i, w in pts])
    return GroundPlaneProjector(compute_calibration(req))


def _track(source, tid, u=100.0, v=100.0, marker_id=None):
    return Track(
        source=source,
        track_id=tid,
        bbox=BBox(x1=u - 10, y1=v - 40, x2=u + 10, y2=v),
        confidence=0.9,
        class_name="marker" if source == EntitySource.ARUCO else "person",
        reference_point=ImagePoint(u=u, v=v),
        marker_id=marker_id,
    )


def kinds(events):
    return [e.kind for e in events]


def test_lifecycle_created_lost_reacquired_removed():
    reg = EntityRegistry("s", ZONES, lost_timeout_ms=1000, zone_min_dwell_ms=0)
    ups, ev = reg.update([_track(EntitySource.YOLO, "5")], 0, _projector())
    assert ups[0].entity_id == "trk-1" and ups[0].tracking_state == TrackingState.ACTIVE
    assert EventKind.TRACK_CREATED in kinds(ev)
    assert ups[0].position.x == pytest.approx(1.0) and ups[0].position.y == pytest.approx(1.0)

    ups, ev = reg.update([], 100, _projector())
    assert ups[0].tracking_state == TrackingState.LOST
    assert ups[0].bbox is None, "lost entities must not report a fresh bbox"
    assert ups[0].position.x == pytest.approx(1.0), "lost entities keep the last observed position, not an extrapolation"
    assert kinds(ev) == [EventKind.TRACK_LOST]

    ups, ev = reg.update([_track(EntitySource.YOLO, "5", u=200)], 500, _projector())
    assert ups[0].entity_id == "trk-1" and ups[0].tracking_state == TrackingState.ACTIVE
    assert kinds(ev) == [EventKind.TRACK_REACQUIRED]

    reg.update([], 600, _projector())
    ups, ev = reg.update([], 1500, _projector())
    assert ups[0].tracking_state == TrackingState.REMOVED
    assert EventKind.TRACK_REMOVED in kinds(ev)
    assert EventKind.ZONE_EXIT in kinds(ev)
    ups, _ = reg.update([], 1600, _projector())
    assert ups == []


def test_namespaces_never_collide():
    markers = MarkerRegistry([RegisteredObject(7, "order-bag-A", "order_bag", "Bag A")])
    reg = EntityRegistry("s", ZONES, markers)
    ups, _ = reg.update(
        [_track(EntitySource.YOLO, "7"), _track(EntitySource.ARUCO, "7", marker_id=7), _track(EntitySource.ARUCO, "9", marker_id=9)],
        0,
        None,
    )
    ids = {u.entity_id: u for u in ups}
    assert set(ids) == {"trk-1", "order-bag-A", "marker-9"}
    assert ids["order-bag-A"].object_id == "order-bag-A" and ids["order-bag-A"].class_name == "order_bag"
    assert ids["marker-9"].object_id is None


def test_uncalibrated_positions_are_null_and_no_zone_events():
    reg = EntityRegistry("s", ZONES, zone_min_dwell_ms=0)
    ups, ev = reg.update([_track(EntitySource.YOLO, "1")], 0, None)
    assert ups[0].position is None and ups[0].zone_id is None
    assert EventKind.ZONE_ENTER not in kinds(ev)


def test_session_ids_are_independent_of_tracker_ids():
    reg = EntityRegistry("s", ZONES)
    ups, _ = reg.update([_track(EntitySource.YOLO, "42"), _track(EntitySource.YOLO, "3", u=300)], 0, None)
    assert sorted(u.entity_id for u in ups) == ["trk-1", "trk-2"]


def test_marker_registry_rejects_rebinding_object():
    reg = MarkerRegistry([RegisteredObject(1, "box", "delivery_container", "Box")])
    try:
        reg.register(RegisteredObject(2, "box", "delivery_container", "Box"))
    except ValueError:
        return
    raise AssertionError("expected ValueError")
