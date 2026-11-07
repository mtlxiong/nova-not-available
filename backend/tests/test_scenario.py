"""End-to-end synthetic scenario: simulated video -> ByteTrack + ArUco -> calibration -> zones -> DB -> replay -> metrics."""

import json
import math
from collections import defaultdict

import pytest

from echo.analytics.metrics import compute_session_metrics
from echo.memory import store
from echo.memory.replay import build_replay, entity_history, position_at
from echo.pipeline.manager import SessionManager
from echo.schemas import SessionConfig, WorldPosition
from echo.spatial.zones import ZoneTracker
from echo.video.simulation import SimulationSource

DURATION_S = 40.0
FPS = 10.0


def run_session(settings, db, **overrides):
    messages: list[str] = []
    manager = SessionManager(settings, db, messages.append, lambda _b: None, lambda: False)
    config = SessionConfig(source="simulation", realtime=False, inference_fps=FPS, simulation_duration_s=DURATION_S, simulation_entities=4, **overrides)
    manager.start(config)
    assert manager.runner.wait(120), "session did not finish"
    assert manager.runner.result.error is None
    return manager.runner.session_id, [json.loads(m) for m in messages]


def ground_truth_enters(kitchen):
    """Zone entries per actor, using the same hysteresis as the pipeline, on true positions."""
    src = SimulationSource(kitchen, fps=FPS, entities=4, duration_s=DURATION_S)
    zt = ZoneTracker(kitchen.zones, min_dwell_ms=400)
    enters: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for i in range(int(DURATION_S * FPS) + 1):
        t_s = i / FPS
        for truth in src.truth_at(t_s):
            if not truth.visible:
                continue
            for tr in zt.update(truth.actor_id, WorldPosition(x=truth.world[0], y=truth.world[1]), int(round(t_s * 1000))):
                if tr.entered:
                    enters[truth.actor_id].append((tr.timestamp_ms, tr.entered))
    return src, enters


@pytest.fixture(scope="module")
def scenario(tmp_path_factory):
    from echo.config import Settings
    from echo.memory.db import Database

    root = tmp_path_factory.mktemp("scenario")
    settings = Settings(data_dir=root / "data", worlds_dir=root / "worlds")
    settings.ensure_dirs()
    db = Database(settings.resolved_database_url)
    session_id, messages = run_session(settings, db)
    return settings, db, session_id, messages


def test_zone_transitions_match_ground_truth(scenario, kitchen):
    settings, db, session_id, messages = scenario
    src, truth_enters = ground_truth_enters(kitchen)
    replay = build_replay(db, session_id)

    # Associate each tracked entity with the actor nearest to its first positioned sample.
    entity_actor = {}
    for e in replay["entities"]:
        first = next(s for s in e["samples"] if s[1] is not None)
        truths = [t for t in src.truth_at(first[0] / 1000.0) if t.visible]
        nearest = min(truths, key=lambda t: math.hypot(t.world[0] - first[1], t.world[1] - first[2]))
        assert math.hypot(nearest.world[0] - first[1], nearest.world[1] - first[2]) < 0.3
        entity_actor[e["entity_id"]] = nearest.actor_id

    observed: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for ev in replay["events"]:
        if ev["kind"] == "zone.enter":
            observed[entity_actor[ev["entity_id"]]].append((ev["t_ms"], ev["zone_id"]))

    assert set(observed) == set(truth_enters)
    for actor, expected in truth_enters.items():
        got = observed[actor]
        assert [z for _, z in got] == [z for _, z in expected], actor
        for (t_got, _), (t_exp, _) in zip(got, expected):
            assert abs(t_got - t_exp) <= 300, (actor, t_got, t_exp)


def test_order_bag_is_tracked_by_marker_not_tracker(scenario):
    _, db, session_id, _ = scenario
    replay = build_replay(db, session_id)
    bag = next(e for e in replay["entities"] if e["entity_id"] == "order-bag-A")
    assert bag["source"] == "aruco" and bag["object_id"] == "order-bag-A"
    zones = [ev["zone_id"] for ev in replay["events"] if ev["entity_id"] == "order-bag-A" and ev["kind"] == "zone.enter"]
    assert zones[:3] == ["packing", "pickup", "packing"]


def test_websocket_messages_follow_schema(scenario):
    _, _, session_id, messages = scenario
    updates = [m for m in messages if m["type"] == "tracking.update"]
    assert len(updates) == int(DURATION_S * FPS) + 1
    assert all(u["session_id"] == session_id and u["calibrated"] and u["simulated"] for u in updates)
    assert [u["timestamp_ms"] for u in updates] == sorted(u["timestamp_ms"] for u in updates)
    sample = updates[50]["entities"][0]
    assert set(sample["position"]) == {"x", "y", "unit"} and sample["position"]["unit"] == "m"
    statuses = [m for m in messages if m["type"] == "session.status"]
    assert statuses[0]["state"] == "running" and statuses[-1]["state"] == "stopped"


def test_history_is_persisted_and_reconstructable(scenario, kitchen):
    _, db, session_id, _ = scenario
    replay = build_replay(db, session_id)
    assert replay["session"]["simulated"] is True
    assert replay["session"]["state"] == "stopped"
    entity = next(e for e in replay["entities"] if e["entity_id"] == "trk-1")
    history = entity_history(db, session_id, "trk-1")
    assert len(history) == len(entity["samples"]) > 300
    # Interpolated replay position must match the recorded sample at sample times.
    for s in entity["samples"][::25]:
        if s[3] == "active" and s[1] is not None:
            assert position_at(entity["samples"], s[0]) == pytest.approx((s[1], s[2]))
    kinds = {e["kind"] for e in replay["events"]}
    assert {"session.started", "session.stopped", "track.created", "track.removed", "zone.enter", "zone.exit"} <= kinds


def test_occlusion_produces_lost_state_without_invented_positions(scenario):
    _, db, session_id, _ = scenario
    replay = build_replay(db, session_id)
    lost_events = [e for e in replay["events"] if e["kind"] == "track.lost"]
    assert lost_events, "cook-2 is occluded from 14.0 to 15.5 s"
    for e in replay["entities"]:
        for s in e["samples"]:
            if s[3] != "active":
                assert s[1] is None and s[2] is None


def test_replay_is_deterministic(scenario, tmp_path):
    from echo.config import Settings
    from echo.memory.db import Database

    _, db, session_id, _ = scenario
    assert build_replay(db, session_id) == build_replay(db, session_id)

    settings2 = Settings(data_dir=tmp_path / "data2", worlds_dir=tmp_path / "w2")
    settings2.ensure_dirs()
    db2 = Database(settings2.resolved_database_url)
    session2, _ = run_session(settings2, db2)
    strip = lambda r: [(e["t_ms"], e["kind"], e["entity_id"], e["zone_id"]) for e in r["events"] if e["kind"] != "session.started"]  # noqa: E731
    assert strip(build_replay(db, session_id)) == strip(build_replay(db2, session2))


def test_metrics_use_calibrated_distances(scenario, kitchen):
    _, db, session_id, _ = scenario
    metrics = compute_session_metrics(db, session_id, kitchen)
    assert metrics["data_quality"]["calibrated"] and metrics["simulated"]
    src = SimulationSource(kitchen, fps=FPS, entities=4)
    # True path length of dish-1 (never occluded) over the session.
    true_len = 0.0
    prev = None
    for i in range(int(DURATION_S * FPS) + 1):
        p = src.actor_position(next(a for a in src.actors if a.actor_id == "dish-1"), i / FPS)
        if prev:
            true_len += math.hypot(p[0] - prev[0], p[1] - prev[1])
        prev = p
    by_id = {e["entity_id"]: e for e in metrics["entities"]}
    replay = build_replay(db, session_id)
    dish_entity = min(
        (e for e in replay["entities"] if e["class_name"] == "person"),
        key=lambda e: math.hypot(e["samples"][0][1] - 2.7, e["samples"][0][2] - 1.0),
    )["entity_id"]
    measured = by_id[dish_entity]["distance_m"]
    # Box jitter adds a little apparent motion; the measured path must be close to the true one.
    assert true_len * 0.97 <= measured <= true_len * 1.15, (measured, true_len)
    zones = {z["zone_id"]: z for z in metrics["zones"]}
    assert zones["packing"]["visits"] > 0 and zones["packing"]["max_occupancy"] >= 1
    assert metrics["orders"]["available"] is False
    assert [e["entity_id"] for e in metrics["entities"]] == sorted(by_id), "entities are listed, never ranked"


def test_uncalibrated_file_session_has_null_positions(settings, db, tmp_path):
    import cv2
    import numpy as np

    path = tmp_path / "clip.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 240))
    for _ in range(10):
        writer.write(np.zeros((240, 320, 3), np.uint8))
    writer.release()
    from echo.video.sources import validate_video_file

    vid = store.register_video(db, path=str(path), original_name="clip.mp4", meta=validate_video_file(path))
    messages: list[str] = []
    manager = SessionManager(settings, db, messages.append, lambda _b: None, lambda: False)
    manager.start(SessionConfig(source="file", file_id=vid, detector="none", realtime=False))
    assert manager.runner.wait(30)
    updates = [json.loads(m) for m in messages if '"tracking.update"' in m]
    assert updates and all(not u["calibrated"] for u in updates)
    assert [u["timestamp_ms"] for u in updates] == [i * 100 for i in range(10)]
