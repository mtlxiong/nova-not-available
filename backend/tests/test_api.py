import io
import struct
import time

import cv2
import numpy as np
from fastapi.testclient import TestClient

from echo.api.app import create_app


def _client(settings):
    return TestClient(create_app(settings))


def test_health_and_spatial_model(settings):
    with _client(settings) as c:
        assert c.get("/api/health").json()["ok"] is True
        model = c.get("/api/spatial-model").json()
        assert model["source"] == "blank" and model["zones"] == [], "no layout is assumed for a fresh workspace"
        listed = {m["model_id"]: m for m in c.get("/api/spatial-models").json()}
        assert listed["workspace-blank"]["active"] and listed["kitchen-template"]["source"] == "template"
        kitchen = c.get("/api/spatial-models/kitchen-template").json()
        assert c.put("/api/spatial-model", json=kitchen).status_code == 400, "templates are read-only"
        custom = kitchen | {"model_id": "my-shop", "name": "My shop", "source": "manual"}
        assert c.put("/api/spatial-model?activate=true", json=custom).status_code == 200
        assert c.get("/api/spatial-model").json()["model_id"] == "my-shop"
        assert c.delete("/api/spatial-models/kitchen-template").status_code == 400
        assert c.delete("/api/spatial-models/my-shop").status_code == 200
        assert c.delete("/api/spatial-models/my-shop").status_code == 404
        assert c.get("/api/spatial-model").json()["model_id"] == "workspace-blank", "deleting the active workspace falls back to blank"
        scenes = c.get("/api/scenes").json()
        assert scenes[0]["scene_id"] == "procedural"


def test_invalid_calibration_rejected(settings):
    with _client(settings) as c:
        body = {
            "image_width": 640,
            "image_height": 480,
            "points": [{"image": [10 * i, 10 * i], "world": [i, i]} for i in range(4)],
        }
        r = c.post("/api/calibrations", json=body)
        assert r.status_code == 422
        assert "collinear" in r.json()["detail"]


def test_valid_calibration_saved_with_overlay(settings):
    with _client(settings) as c:
        c.post("/api/spatial-models/kitchen-template/activate")
        body = {
            "image_width": 640,
            "image_height": 480,
            "points": [
                {"image": [50, 450], "world": [0, 0]},
                {"image": [590, 450], "world": [9, 0]},
                {"image": [470, 120], "world": [9, 6]},
                {"image": [170, 120], "world": [0, 6]},
            ],
        }
        r = c.post("/api/calibrations", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["calibration"]["calibration_id"].startswith("cal_")
        assert len(data["overlay"]["zones"]) == 7
        assert c.get("/api/calibrations").json()[0]["calibration_id"] == data["calibration"]["calibration_id"]


def test_video_upload_validation(settings, tmp_path):
    with _client(settings) as c:
        r = c.post("/api/videos", files={"file": ("x.txt", b"hello", "text/plain")})
        assert r.status_code == 400
        r = c.post("/api/videos", files={"file": ("bad.mp4", b"\x00" * 2048, "video/mp4")})
        assert r.status_code == 400
        path = tmp_path / "ok.mp4"
        w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
        for _ in range(5):
            w.write(np.zeros((120, 160, 3), np.uint8))
        w.release()
        r = c.post("/api/videos", files={"file": ("ok.mp4", path.read_bytes(), "video/mp4")})
        assert r.status_code == 200, r.text
        assert r.json()["width"] == 160
        assert len(list(settings.uploads_dir.iterdir())) == 1, "rejected uploads must be deleted"


def test_session_over_websocket_then_replay_and_metrics(settings):
    with _client(settings) as c:
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "session.status"
            r = c.post("/api/sessions/start", json={"source": "simulation", "simulation_duration_s": 3, "inference_fps": 20})
            assert r.status_code == 200, r.text
            assert c.post("/api/sessions/start", json={"source": "simulation"}).status_code == 409
            got_update = got_frame = False
            deadline = time.time() + 15
            while time.time() < deadline and not (got_update and got_frame):
                msg = ws.receive()
                if msg.get("bytes"):
                    idx, ts = struct.unpack("<II", msg["bytes"][:8])
                    assert msg["bytes"][8:10] == b"\xff\xd8", "JPEG payload"
                    got_frame = True
                elif msg.get("text"):
                    import json

                    data = json.loads(msg["text"])
                    if data["type"] == "tracking.update":
                        assert data["calibrated"] is True
                        got_update = True
            assert got_update and got_frame
        session_id = c.get("/api/sessions/current").json()["session_id"]
        deadline = time.time() + 15
        while c.get("/api/sessions/current").json()["state"] == "running" and time.time() < deadline:
            time.sleep(0.2)
        assert c.get("/api/sessions/current").json()["state"] == "stopped"
        replay = c.get(f"/api/sessions/{session_id}/replay").json()
        assert replay["entities"] and replay["events"]
        metrics = c.get(f"/api/sessions/{session_id}/metrics").json()
        assert metrics["data_quality"]["calibrated"]
        assert {z["zone_id"] for z in metrics["zones"]} >= {"prep", "pickup"}, "simulation is scored against its kitchen template"
        assert c.get(f"/api/sessions/{session_id}/model").json()["model_id"] == "kitchen-template"
        assert c.get("/api/sessions/nope/replay").status_code == 404


def test_scene_generation_without_key_reports_unavailable(settings, monkeypatch):
    monkeypatch.delenv("WORLD_LABS_API_KEY", raising=False)
    import echo.scenes as scenes

    monkeypatch.setattr(scenes, "_world_labs_key", lambda _s: None)
    png = cv2.imencode(".png", np.zeros((8, 8, 3), np.uint8))[1].tobytes()
    with _client(settings) as c:
        r = c.post("/api/scenes/generate", data={"slug": "my-kitchen"}, files={"image": ("k.png", io.BytesIO(png), "image/png")})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "unavailable" and body["fallback_scene_id"] == "procedural"
        assert (settings.worlds_dir / "my-kitchen" / "source" / "0-my-kitchen.png").is_file()
        assert c.post("/api/scenes/generate", data={"slug": "../bad"}, files={"image": ("k.png", io.BytesIO(png), "image/png")}).status_code == 400
