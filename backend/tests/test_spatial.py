import json
from pathlib import Path

import pytest

from echo.schemas import Alignment, WorldPosition, Zone
from echo.spatial.alignment import render_to_world, world_to_render
from echo.spatial.geometry import point_in_polygon, rect
from echo.spatial.zones import ZoneTracker, zone_at

VECTORS = Path(__file__).resolve().parents[2] / "shared" / "alignment_vectors.json"


def test_alignment_matches_shared_vectors():
    cases = json.loads(VECTORS.read_text())["cases"]
    assert cases
    for case in cases:
        al = Alignment.model_validate(case["alignment"])
        got = world_to_render(al, *case["world"])
        assert got == pytest.approx(tuple(case["render"]), abs=1e-7)
        back = render_to_world(al, *got)
        assert back == pytest.approx(tuple(case["world"]), abs=1e-7)


def test_identity_alignment_maps_world_y_to_negative_render_z():
    assert world_to_render(Alignment(), 2.0, 3.0) == pytest.approx((2.0, 0.0, -3.0))


def test_point_in_polygon_including_edges():
    sq = rect(0, 0, 2, 2)
    assert point_in_polygon(1, 1, sq)
    assert point_in_polygon(0, 1, sq)
    assert point_in_polygon(2, 2, sq)
    assert not point_in_polygon(2.01, 1, sq)
    concave = [(0, 0), (4, 0), (4, 4), (2, 1), (0, 4)]
    assert not point_in_polygon(2, 3, concave)
    assert point_in_polygon(1, 1, concave)


def test_kitchen_zones_do_not_overlap(kitchen):
    for i, a in enumerate(kitchen.zones):
        for b in kitchen.zones[i + 1 :]:
            for x, y in a.polygon:
                cx = sum(p[0] for p in a.polygon) / len(a.polygon)
                cy = sum(p[1] for p in a.polygon) / len(a.polygon)
                inner = (x + (cx - x) * 0.01, y + (cy - y) * 0.01)
                assert not point_in_polygon(*inner, b.polygon), (a.zone_id, b.zone_id)


ZONES = [Zone(zone_id="a", name="A", polygon=rect(0, 0, 1, 1)), Zone(zone_id="b", name="B", polygon=rect(2, 0, 3, 1))]


def test_zone_at():
    assert zone_at(WorldPosition(x=0.5, y=0.5), ZONES) == "a"
    assert zone_at(WorldPosition(x=1.5, y=0.5), ZONES) is None
    assert zone_at(None, ZONES) is None


def test_zone_tracker_hysteresis_and_backdated_transitions():
    zt = ZoneTracker(ZONES, min_dwell_ms=300)
    p = lambda x: WorldPosition(x=x, y=0.5)  # noqa: E731
    assert zt.update("e", p(0.5), 0) == []
    assert zt.update("e", p(0.5), 200) == []
    [enter] = zt.update("e", p(0.5), 300)
    assert (enter.exited, enter.entered, enter.timestamp_ms) == (None, "a", 0)
    # brief flicker out of the zone is ignored
    assert zt.update("e", p(1.5), 400) == []
    assert zt.update("e", p(0.5), 500) == []
    # a real move to B
    zt.update("e", p(1.5), 600)
    [leave_a] = zt.update("e", p(1.5), 900)
    assert (leave_a.exited, leave_a.entered, leave_a.timestamp_ms) == ("a", None, 600)
    zt.update("e", p(2.5), 1000)
    [enter_b] = zt.update("e", p(2.5), 1300)
    assert (enter_b.exited, enter_b.entered, enter_b.timestamp_ms) == (None, "b", 1000)
    [release] = zt.release("e", 2000)
    assert (release.exited, release.entered) == ("b", None)
    assert zt.current("e") is None
