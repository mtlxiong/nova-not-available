"""Analyze a workspace video headlessly and save a debug overlay of what was found.

    python scripts/analyze_workspace.py ../data/samples/vtest.avi [--calibration cal.json] [--hfov 65]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from echo.analysis.analyzer import WorkspaceAnalyzer  # noqa: E402
from echo.config import get_settings  # noqa: E402
from echo.memory import store  # noqa: E402
from echo.memory.db import Database  # noqa: E402
from echo.schemas import CalibrationRequest, CalibrationResult, WorkspaceAnalysisRequest  # noqa: E402
from echo.spatial.calibration import GroundPlaneProjector, compute_calibration  # noqa: E402
from echo.video.sources import validate_video_file  # noqa: E402


def draw_overlay(plate: np.ndarray, model: dict, calibration: CalibrationResult, objects: list[dict]) -> np.ndarray:
    out = plate.copy()
    h, w = out.shape[:2]
    proj = GroundPlaneProjector(calibration, w, h)
    for o in objects:
        x1, y1, x2, y2 = map(int, o["bbox"])
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 200, 255), 2)
        cv2.putText(out, f"{o['label']} {o['confidence']:.2f}", (x1, max(12, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1, cv2.LINE_AA)
    for z in model["zones"]:
        pts = [proj.world_to_image(x, y) for x, y in z["polygon"]]
        if all(p is not None for p in pts):
            arr = np.array(pts, np.int32)
            color = tuple(int(z["color"][i : i + 2], 16) for i in (5, 3, 1))
            cv2.polylines(out, [arr], True, color, 2, cv2.LINE_AA)
            c = arr.mean(axis=0).astype(int)
            cv2.putText(out, z["name"], tuple(c), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--calibration", type=Path, help="CalibrationRequest JSON with measured floor points")
    ap.add_argument("--hfov", type=float, default=65.0)
    ap.add_argument("--person-height", type=float, default=1.70)
    ap.add_argument("--frames", type=int, default=80)
    ap.add_argument("--out", type=Path, default=Path("/tmp/echo-analysis"))
    args = ap.parse_args()

    settings = get_settings()
    settings.ensure_dirs()
    db = Database(settings.resolved_database_url)
    meta = validate_video_file(args.video)
    video_id = store.register_video(db, path=str(args.video.resolve()), original_name=args.video.name, meta=meta)
    cal_id = None
    if args.calibration:
        req = CalibrationRequest.model_validate_json(args.calibration.read_text())
        cal_id = store.save_calibration(db, compute_calibration(req)).calibration_id

    analyzer = WorkspaceAnalyzer(settings, db)
    t0 = time.perf_counter()
    result = analyzer.run_sync(
        WorkspaceAnalysisRequest(video_id=video_id, calibration_id=cal_id, hfov_deg=args.hfov, person_height_m=args.person_height, max_frames=args.frames)
    )
    elapsed = time.perf_counter() - t0

    model = result["model"]
    cal = CalibrationResult.model_validate(result["calibration"])
    args.out.mkdir(parents=True, exist_ok=True)
    plate = cv2.imread(str(settings.workspaces_dir / model["model_id"] / "background.jpg"))
    cv2.imwrite(str(args.out / "overlay.jpg"), draw_overlay(plate, model, cal, result["objects"]))
    tex = cv2.imread(str(settings.workspaces_dir / model["model_id"] / "floor.png"), cv2.IMREAD_UNCHANGED)
    cv2.imwrite(str(args.out / "floor.png"), tex)
    print(json.dumps({"elapsed_s": round(elapsed, 2), "model_id": model["model_id"], **result["stats"]}, indent=1))
    print("calibration:", cal.method, f"rmse~{cal.reprojection_rmse_m:.2f} m", cal.warnings)
    print(f"floor: {model['width_m']:.1f} x {model['depth_m']:.1f} m, origin {model['floor_texture']['origin']}")
    for f in model["fixtures"]:
        print(f"  fixture {f['label']:<22} conf={f['confidence']:.2f} size={f['size']} est={f['estimated_dims']}")
    for z in model["zones"]:
        print(f"  zone    {z['name']:<22} {len(z['polygon'])} vertices")
    for n in result["notes"]:
        print("  note:", n)
    print("debug images:", args.out)


if __name__ == "__main__":
    main()
