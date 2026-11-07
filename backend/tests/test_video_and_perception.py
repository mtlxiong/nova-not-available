import hashlib
from pathlib import Path

import cv2
import numpy as np
import pytest

from echo.config import Settings
from echo.perception.detectors import SimulationDetector, YoloDetector
from echo.perception.markers import ArucoMarkerDetector
from echo.perception.trackers import ByteTrackTracker, IouTracker
from echo.schemas import EntitySource, TrackingUpdate
from echo.video.simulation import SimulationSource
from echo.video.sources import FileSource, VideoSourceError, WebcamSource, validate_video_file


def _write_video(path: Path, frames: int = 30, fps: float = 30.0, size=(320, 240)) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for i in range(frames):
        img = np.zeros((size[1], size[0], 3), np.uint8)
        cv2.putText(img, str(i), (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 3)
        writer.write(img)
    writer.release()
    return path


def test_file_source_preserves_timestamps_and_samples(tmp_path):
    path = _write_video(tmp_path / "clip.mp4", frames=30, fps=30)
    with FileSource(path, sample_fps=10) as src:
        frames = []
        while (f := src.read()) is not None:
            frames.append(f)
    assert [f.index for f in frames] == list(range(0, 30, 3))
    assert [f.timestamp_ms for f in frames] == [round(i / 30 * 1000) for i in range(0, 30, 3)]
    assert frames[0].image.shape == (240, 320, 3)


def test_file_source_downscales(tmp_path):
    path = _write_video(tmp_path / "clip.mp4", frames=3)
    with FileSource(path, max_width=160) as src:
        assert src.read().image.shape[:2] == (120, 160)


def test_validate_rejects_bad_files(tmp_path):
    with pytest.raises(VideoSourceError, match="not found"):
        validate_video_file(tmp_path / "missing.mp4")
    (tmp_path / "notes.txt").write_text("x")
    with pytest.raises(VideoSourceError, match="unsupported"):
        validate_video_file(tmp_path / "notes.txt")
    (tmp_path / "garbage.mp4").write_bytes(b"\x00" * 1024)
    with pytest.raises(VideoSourceError):
        validate_video_file(tmp_path / "garbage.mp4")


def test_missing_webcam_raises_clean_error():
    src = WebcamSource(camera_index=97)
    with pytest.raises(VideoSourceError):
        src.open()
    src.close()


def _sim_digest(kitchen, n=40):
    src = SimulationSource(kitchen, fps=10, entities=4, seed=3)
    src.open()
    h = hashlib.sha256()
    truths = []
    for _ in range(n):
        f = src.read()
        h.update(f.image.tobytes())
        truths.append([(t.actor_id, round(t.world[0], 6), round(t.world[1], 6), t.visible) for t in f.truth])
    src.close()
    return h.hexdigest(), truths


def test_simulation_is_deterministic(kitchen):
    assert _sim_digest(kitchen) == _sim_digest(kitchen)


def test_simulation_duration_ends_stream(kitchen):
    src = SimulationSource(kitchen, fps=10, duration_s=1.0)
    src.open()
    count = 0
    while src.read() is not None:
        count += 1
    assert count == 11


def test_aruco_detected_in_simulated_frame(kitchen):
    src = SimulationSource(kitchen, fps=10, entities=3)
    src.open()
    frame = src.read()
    tracks = ArucoMarkerDetector().detect(frame)
    assert [t.marker_id for t in tracks] == [7]
    bag = next(t for t in frame.truth if t.actor_id == "order-bag-A")
    u, v = src.to_image(*bag.world)
    assert abs(tracks[0].reference_point.u - u) < 3 and abs(tracks[0].reference_point.v - v) < 3
    assert tracks[0].source == EntitySource.ARUCO


@pytest.mark.parametrize("tracker_cls", [ByteTrackTracker, IouTracker])
def test_trackers_keep_ids_on_simulated_motion(kitchen, tracker_cls):
    src = SimulationSource(kitchen, fps=10, entities=3)
    src.open()
    det = SimulationDetector()
    tracker = tracker_cls(EntitySource.SIMULATION)
    ids_per_frame = []
    for _ in range(50):
        f = src.read()
        tracks = tracker.update(det.detect(f), f)
        ids_per_frame.append(sorted(t.track_id for t in tracks))
    stable = ids_per_frame[5:]
    assert all(ids == stable[0] for ids in stable), "IDs should be stable for well-separated walkers"
    assert len(stable[0]) == 3


def test_yolo_detects_people_on_real_image():
    weights = Settings().yolo_weights
    if not weights.is_file():
        pytest.skip("YOLO weights not downloaded")
    import ultralytics

    image_path = Path(ultralytics.__file__).parent / "assets" / "bus.jpg"
    image = cv2.imread(str(image_path))
    from echo.video.sources import Frame

    detector = YoloDetector(weights, ["person"], confidence=0.4)
    dets = detector.detect(Frame(index=0, timestamp_ms=0, capture_wall_ms=0, image=image))
    assert len(dets) >= 3
    assert all(d.class_name == "person" for d in dets)


def test_yolo_rejects_unknown_classes():
    weights = Settings().yolo_weights
    if not weights.is_file():
        pytest.skip("YOLO weights not downloaded")
    from echo.perception.detectors import DetectorError

    with pytest.raises(DetectorError, match="not supported"):
        YoloDetector(weights, ["doordash_order_1234"])


def test_tracking_update_roundtrip_and_nullable_position():
    raw = {
        "type": "tracking.update",
        "session_id": "session_001",
        "frame_index": 3,
        "timestamp_ms": 12500,
        "capture_wall_ms": 1.0,
        "emitted_wall_ms": 2.0,
        "frame_width": 1280,
        "frame_height": 720,
        "calibrated": False,
        "simulated": False,
        "entities": [
            {
                "entity_id": "trk-12",
                "class_name": "person",
                "source": "yolo",
                "source_track_id": "12",
                "position": None,
                "zone_id": None,
                "tracking_state": "active",
                "first_seen_ms": 0,
                "last_seen_ms": 12500,
            }
        ],
    }
    msg = TrackingUpdate.model_validate(raw)
    again = TrackingUpdate.model_validate_json(msg.model_dump_json())
    assert again == msg
    assert again.entities[0].position is None
