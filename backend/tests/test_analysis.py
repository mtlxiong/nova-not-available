import math
from pathlib import Path

import numpy as np
import pytest

from echo.analysis import autocal, layout
from echo.analysis.background import BackgroundBuilder
from echo.analysis.objects import StaticObject, _suppress_nested
from echo.analysis.samples import PersonBox, SceneSampleBuffer
from echo.schemas import Fixture
from echo.spatial.calibration import GroundPlaneProjector

W, H = 1280, 720
TRUE = autocal.CameraModel(W, H, focal_px=(W / 2) / math.tan(math.radians(65) / 2), pitch_rad=math.radians(28), height_m=4.0)


def _project(cam: autocal.CameraModel, x, y, z):
    s, c = math.sin(cam.pitch_rad), math.cos(cam.pitch_rad)
    dz = z - cam.height_m
    xc, yc, zc = x, -s * y - c * dz, c * y - s * dz
    return cam.cx + cam.focal_px * xc / zc, cam.cy + cam.focal_px * yc / zc


def _synthetic_people(cam, n=200, seed=0, height_sd=0.07):
    """People standing at random floor positions (camera-foot frame), heights ~ N(1.70, sd)."""
    rng = np.random.default_rng(seed)
    boxes, truth = [], []
    while len(boxes) < n:
        x, y = rng.uniform(-5, 5), rng.uniform(3, 16)
        hgt = rng.normal(1.70, height_sd)
        u_f, v_f = _project(cam, x, y, 0.0)
        _, v_h = _project(cam, x, y, hgt)
        half_w = 0.22 * (v_f - v_h) / 1.7 * 1.0
        x1, x2 = u_f - half_w, u_f + half_w
        if not (5 < x1 and x2 < W - 5 and 5 < v_h and v_f < H - 5):
            continue
        boxes.append(PersonBox(len(boxes) * 100, None, x1, v_h, x2, v_f, 0.9))
        truth.append((x, y))
    return boxes, np.array(truth)


def test_autocal_recovers_camera_from_people():
    boxes, truth = _synthetic_people(TRUE)
    samples = autocal.filter_person_boxes([(b.x1, b.y1, b.x2, b.y2, b.conf) for b in boxes], W, H)
    assert len(samples) == len(boxes)
    cam, quality = autocal.estimate_camera(samples, W, H)
    assert abs(math.degrees(cam.pitch_rad) - 28) < 1.5
    assert abs(cam.height_m - 4.0) / 4.0 < 0.08
    cal = autocal.calibration_from_camera(cam, samples, quality, camera_id="cam")
    assert cal.method == "auto-person-height" and any("APPROXIMATE" in w for w in cal.warnings)
    proj = GroundPlaneProjector(cal, W, H)
    est = np.array([[proj.image_to_world((b.x1 + b.x2) / 2, b.y2).x, proj.image_to_world((b.x1 + b.x2) / 2, b.y2).y] for b in boxes])
    rel = np.linalg.norm(est - truth, axis=1) / np.linalg.norm(truth - [0, 0], axis=1)
    assert np.median(rel) < 0.06, "floor positions within ~6% of distance when assumptions hold"


def test_autocal_offset_moves_world_origin():
    boxes, _ = _synthetic_people(TRUE, n=60)
    samples = autocal.filter_person_boxes([(b.x1, b.y1, b.x2, b.y2, b.conf) for b in boxes], W, H)
    cam, q = autocal.estimate_camera(samples, W, H)
    a = GroundPlaneProjector(autocal.calibration_from_camera(cam, samples, q, camera_id="c"), W, H).image_to_world(640, 600)
    b = GroundPlaneProjector(autocal.calibration_from_camera(cam, samples, q, camera_id="c", world_offset=(3.0, -2.0)), W, H).image_to_world(640, 600)
    assert b.x == pytest.approx(a.x + 3.0) and b.y == pytest.approx(a.y - 2.0)


def test_autocal_refuses_insufficient_evidence():
    boxes, _ = _synthetic_people(TRUE, n=5)
    with pytest.raises(autocal.AutoCalibrationError, match="at least"):
        autocal.estimate_camera(autocal.filter_person_boxes([(b.x1, b.y1, b.x2, b.y2, b.conf) for b in boxes], W, H), W, H)
    same_row = [autocal.PersonSample(100 + 10 * i, 500 + (i % 3), 400) for i in range(40)]
    with pytest.raises(autocal.AutoCalibrationError, match="narrow band"):
        autocal.estimate_camera(same_row, W, H)


def test_person_filter_drops_truncated_and_sitting():
    keep = (100, 100, 140, 300, 0.9)
    assert len(autocal.filter_person_boxes([keep], W, H)) == 1
    assert autocal.filter_person_boxes([(100, 0, 140, 300, 0.9)], W, H) == [], "cut by the top edge"
    assert autocal.filter_person_boxes([(100, 400, 140, H, 0.9)], W, H) == [], "cut by the bottom edge"
    assert autocal.filter_person_boxes([(100, 100, 200, 220, 0.9)], W, H) == [], "squat box = sitting/bending"
    assert autocal.filter_person_boxes([(100, 100, 140, 300, 0.2)], W, H) == [], "low confidence"


def test_background_plate_removes_people():
    rng = np.random.default_rng(1)
    bg = np.tile(np.linspace(40, 200, 320, dtype=np.uint8)[None, :, None], (240, 1, 3))
    builder = BackgroundBuilder(max_frames=30)
    for i in range(30):
        img = bg.copy()
        x = 10 + i * 9
        img[60:200, x : x + 30] = 0  # a "person" walking across
        img = np.clip(img.astype(int) + rng.integers(-3, 4, img.shape), 0, 255).astype(np.uint8)
        builder.add(img, [(x, 60, x + 30, 200)])
    plate, never_clear = builder.build()
    assert never_clear == 0.0
    assert np.abs(plate.astype(int) - bg.astype(int)).max() <= 4


def _true_calibration():
    boxes, _ = _synthetic_people(TRUE, n=80)
    samples = autocal.filter_person_boxes([(b.x1, b.y1, b.x2, b.y2, b.conf) for b in boxes], W, H)
    _, q = autocal.estimate_camera(samples, W, H)
    return autocal.calibration_from_camera(TRUE, samples, q, camera_id="c"), boxes


def test_fixtures_projected_with_measured_width_and_prior_depth():
    cal, people = _true_calibration()
    proj = GroundPlaneProjector(cal, W, H)
    # A 2.0 m wide, 0.9 m tall counter whose front edge spans x=-1..1 at y=8 (facing the camera).
    u1, v_b = _project(TRUE, -1.0, 8.0, 0.0)
    u2, _ = _project(TRUE, 1.0, 8.0, 0.0)
    _, v_t = _project(TRUE, 0.0, 8.0, 0.9)
    objs = [StaticObject("counter", 0.8, (u1, v_t, u2, v_b)), StaticObject("window", 0.9, (10, 10, 50, 50))]
    fixtures, notes = layout.project_fixtures(objs, proj, (W, H), layout.vertical_scale_fn(people, 1.7))
    assert len(fixtures) == 1 and any("window" in n for n in notes)
    f = fixtures[0]
    assert f.kind == "counter" and f.provenance == "detected" and f.confidence == 0.8
    assert f.size[0] == pytest.approx(2.0, abs=0.05)
    assert f.size[1] == pytest.approx(0.65), "depth from the category prior"
    assert "depth" in f.estimated_dims and "width" not in f.estimated_dims
    assert f.center[0] == pytest.approx(0.0, abs=0.05)
    assert f.center[1] == pytest.approx(8.0 + 0.65 / 2, abs=0.1), "box extends away from the camera"
    assert 0.6 < f.size[2] < 1.2
    assert abs(f.yaw_deg) < 2


def test_activity_zones_cluster_dwell_and_take_nearby_object_names():
    rng = np.random.default_rng(3)
    pts = []
    for cx, cy, n in ((3.0, 3.0, 300), (8.0, 5.0, 200)):
        pts += [layout.FloorPoint(i * 100, None, cx + rng.normal(0, 0.25), cy + rng.normal(0, 0.25)) for i in range(n)]
    pts += [layout.FloorPoint(0, None, rng.uniform(0, 10), rng.uniform(0, 8)) for _ in range(40)]
    fixtures = [Fixture(fixture_id="desk-1", kind="desk", label="Desk", center=(3.2, 3.8), size=(1.2, 0.7, 0.75), provenance="detected")]
    bounds = layout.FloorBounds(0, 0, 10, 8)
    zones = layout.activity_zones(pts, layout.dwell_weights(pts), bounds, fixtures)
    assert len(zones) >= 2
    from echo.spatial.geometry import point_in_polygon

    assert point_in_polygon(3.0, 3.0, zones[0].polygon) and zones[0].name == "Desk area"
    assert any(point_in_polygon(8.0, 5.0, z.polygon) and z.name.startswith("Activity area") for z in zones)
    assert all(z.provenance == "proposed" and z.kind == "activity" for z in zones)


def test_dwell_weights_separate_standing_from_walking():
    still = [layout.FloorPoint(i * 200, "a", 1.0, 1.0 + 0.01 * i) for i in range(5)]
    walking = [layout.FloorPoint(i * 200, "b", 0.3 * i, 5.0) for i in range(5)]
    w = layout.dwell_weights(still + walking)
    assert (w[:5] == 1.0).all() and (w[5:] < 0.5).all()


def test_floor_texture_is_top_down_with_visibility_alpha():
    cal, _ = _true_calibration()
    plate = np.full((H, W, 3), 120, np.uint8)
    tex = layout.floor_texture(plate, np.array(cal.homography), layout.FloorBounds(-3, 3, 3, 12), max_px=600)
    assert tex.shape[2] == 4 and tex.shape[1] / tex.shape[0] == pytest.approx(6 / 9, rel=0.02)
    assert tex[..., 3].max() == 255 and (tex[..., 3] == 0).any(), "areas outside the view are transparent"


def test_nested_duplicate_labels_suppressed():
    a = StaticObject("table", 0.6, (0, 0, 100, 100))
    b = StaticObject("desk", 0.4, (2, 2, 98, 99))
    c = StaticObject("chair", 0.5, (200, 0, 240, 60))
    kept = _suppress_nested([b, a, c])
    assert {o.label for o in kept} == {"table", "chair"}


def test_sample_buffer_keeps_frames_at_interval_and_resets_on_resolution_change():
    buf = SceneSampleBuffer(frame_interval_ms=1000, max_frames=5, max_width=320)
    for t in range(0, 4000, 100):
        buf.add(t, np.zeros((480, 640, 3), np.uint8), [PersonBox(t, "1", 0, 0, 10, 30, 0.9)])
    frames, boxes, size = buf.snapshot()
    assert len(frames) == 4 and frames[0][1].shape[1] == 320 and frames[0][2] == 0.5
    assert len(boxes) == 40 and size == (640, 480)
    buf.add(5000, np.zeros((240, 320, 3), np.uint8), [])
    frames, boxes, size = buf.snapshot()
    assert len(frames) == 1 and boxes == [] and size == (320, 240)


VTEST = Path(__file__).resolve().parents[2] / "data" / "samples" / "vtest.avi"


@pytest.mark.skipif(not VTEST.is_file(), reason="sample video not present")
def test_analyze_real_video_end_to_end(settings, db):
    from echo.analysis.analyzer import WorkspaceAnalyzer
    from echo.memory import store
    from echo.schemas import WorkspaceAnalysisRequest
    from echo.video.sources import validate_video_file

    if not settings.yolo_weights.is_file():
        pytest.skip("YOLO weights not present")
    vid = store.register_video(db, path=str(VTEST), original_name="vtest.avi", meta=validate_video_file(VTEST))
    result = WorkspaceAnalyzer(settings, db).run_sync(WorkspaceAnalysisRequest(video_id=vid, max_frames=30))
    model = result["model"]
    assert model["source"] == "analyzed" and model["calibration_id"]
    assert result["calibration"]["method"] == "auto-person-height"
    assert 15 < float(result["notes"][0].split("pitched ")[1].split("°")[0]) < 40, "elevated camera looking down"
    assert model["zones"], "activity zones proposed from where people walked"
    assert (settings.workspaces_dir / model["model_id"] / "floor.png").is_file()
    assert store.get_spatial_model(db, model["model_id"]).model_id == model["model_id"]
    assert store.get_spatial_model(db).source == "blank", "analysis does not activate a workspace by itself"
    if result["stats"]["object_backend"] == "yolo-world":
        labels = {f["kind"] for f in model["fixtures"]}
        assert labels & {"van", "car", "traffic cone", "tripod"}, labels
