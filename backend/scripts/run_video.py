"""Headless run of a video file through the full ECHO pipeline, with measured performance.

    python scripts/run_video.py ../data/samples/vtest.avi --calibration ../data/samples/vtest_calibration.json

Writes the session to the ECHO database so it can be replayed in the UI.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from echo.analytics.metrics import compute_session_metrics  # noqa: E402
from echo.config import get_settings  # noqa: E402
from echo.memory import store  # noqa: E402
from echo.memory.db import Database  # noqa: E402
from echo.pipeline.manager import SessionManager  # noqa: E402
from echo.schemas import CalibrationRequest, SessionConfig  # noqa: E402
from echo.spatial.calibration import compute_calibration  # noqa: E402
from echo.video.sources import validate_video_file  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--calibration", type=Path, help="CalibrationRequest JSON")
    ap.add_argument("--fps", type=float, default=10.0, help="inference rate (media time)")
    ap.add_argument("--realtime", action="store_true")
    ap.add_argument("--max-seconds", type=float, default=None)
    args = ap.parse_args()

    settings = get_settings()
    settings.ensure_dirs()
    db = Database(settings.resolved_database_url)
    meta = validate_video_file(args.video)
    video_id = store.register_video(db, path=str(args.video.resolve()), original_name=args.video.name, meta=meta)

    calibration_id = None
    if args.calibration:
        req = CalibrationRequest.model_validate_json(args.calibration.read_text())
        cal = store.save_calibration(db, compute_calibration(req))
        calibration_id = cal.calibration_id
        print(f"calibration {calibration_id}: rmse={cal.reprojection_rmse_m:.4f} m warnings={cal.warnings}")

    updates: list[dict] = []
    events: list[dict] = []

    def on_json(text: str) -> None:
        msg = json.loads(text)
        if msg["type"] == "tracking.update":
            updates.append(msg)
        elif msg["type"] == "event":
            events.append(msg["event"])

    manager = SessionManager(settings, db, on_json, lambda _b: None, lambda: False)
    t0 = time.time()
    manager.start(SessionConfig(source="file", file_id=video_id, inference_fps=args.fps, realtime=args.realtime, calibration_id=calibration_id))
    runner = manager.runner
    while not runner.wait(0.5):
        if args.max_seconds and updates and updates[-1]["timestamp_ms"] >= args.max_seconds * 1000:
            manager.stop()
    wall = time.time() - t0

    inf = [u["perf"]["inference_ms"] for u in updates[5:]]
    pipe = [u["perf"]["pipeline_ms"] for u in updates[5:]]
    ids = {e["entity_id"] for u in updates for e in u["entities"]}
    positioned = sum(1 for u in updates for e in u["entities"] if e["position"] and e["tracking_state"] == "active")
    print(
        json.dumps(
            {
                "session_id": runner.session_id,
                "video": meta,
                "processed_frames": len(updates),
                "wall_s": round(wall, 2),
                "processing_fps": round(len(updates) / wall, 2),
                "inference_ms_median": round(statistics.median(inf), 1) if inf else None,
                "pipeline_ms_median": round(statistics.median(pipe), 1) if pipe else None,
                "unique_entities": len(ids),
                "positioned_active_observations": positioned,
                "events": {k: sum(1 for e in events if e["kind"] == k) for k in sorted({e["kind"] for e in events})},
                "result": runner.result.state,
                "error": runner.result.error,
            },
            indent=2,
        )
    )
    metrics = compute_session_metrics(db, runner.session_id, store.get_spatial_model(db))
    print("zones:", {z["zone_id"]: (z["visits"], z["total_dwell_s"]) for z in metrics["zones"]})


if __name__ == "__main__":
    main()
