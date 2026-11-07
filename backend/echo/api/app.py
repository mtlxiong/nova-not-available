"""ECHO HTTP + WebSocket API."""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from echo.analysis.analyzer import AnalysisError, WorkspaceAnalyzer
from echo.analytics.metrics import compute_session_metrics
from echo.api.hub import Hub
from echo.config import REPO_ROOT, Settings, get_settings
from echo.memory import store
from echo.memory.db import Database, SessionRow, new_id
from echo.memory.replay import SessionNotFound, build_replay, entity_history
from echo.pipeline.manager import SessionManager, SessionStartError
from echo.scenes import PROCEDURAL_SCENE_ID, SceneGenerator, list_scenes
from echo.schemas import CalibrationRequest, SceneConfig, SessionConfig, SpatialModel, WorkspaceAnalysisRequest
from echo.spatial.calibration import CalibrationError, GroundPlaneProjector, compute_calibration
from echo.spatial.kitchen import KITCHEN_TEMPLATE_ID, default_kitchen
from echo.video.sources import VIDEO_EXTENSIONS, VideoSourceError, validate_video_file

log = logging.getLogger("echo.api")


def _configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stdout)


def create_app(settings: Settings | None = None) -> FastAPI:
    _configure_logging()
    settings = settings or get_settings()
    settings.ensure_dirs()
    db = Database(settings.resolved_database_url)
    hub = Hub()
    manager = SessionManager(settings, db, hub.publish_json, hub.publish_frame, hub.any_wants_frames)
    generator = SceneGenerator(settings)
    analyzer = WorkspaceAnalyzer(settings, db, manager)
    store.get_spatial_model(db)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        hub.bind(asyncio.get_running_loop())
        yield
        await asyncio.to_thread(manager.shutdown)

    app = FastAPI(title="ECHO", version="0.1.0", lifespan=lifespan)
    app.state.settings, app.state.db, app.state.hub, app.state.manager = settings, db, hub, manager
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    # ------------------------------------------------------------------------- health
    @app.get("/api/health")
    def health():
        return {
            "ok": True,
            "yolo_weights": settings.yolo_weights.is_file(),
            "image_blaster": (settings.image_blaster_dir / ".claude" / "scripts" / "world" / "generate-world.mjs").is_file(),
            "session": manager.status().model_dump(),
            "ws_clients": hub.client_count,
        }

    # ------------------------------------------------------------------------- spatial model / workspaces
    @app.get("/api/spatial-model", response_model=SpatialModel)
    def get_model():
        """The model the 3D view should show: the running session's workspace, else the active one."""
        return manager.current_model()

    @app.put("/api/spatial-model", response_model=SpatialModel)
    def put_model(model: SpatialModel, activate: bool = False):
        if model.source == "template":
            raise HTTPException(400, "templates are read-only; save a copy with a new model_id")
        saved = store.put_spatial_model(db, model, activate=activate)
        if manager.model is not None and manager.model.model_id == saved.model_id:
            manager.apply_workspace(saved)
        return saved

    @app.get("/api/spatial-models")
    def spatial_models():
        return store.list_spatial_models(db)

    @app.get("/api/spatial-models/{model_id}", response_model=SpatialModel)
    def spatial_model(model_id: str):
        try:
            return store.get_spatial_model(db, model_id)
        except KeyError as exc:
            raise HTTPException(404, "workspace not found") from exc

    @app.post("/api/spatial-models/{model_id}/activate")
    def activate_model(model_id: str, apply_to_session: bool = True):
        try:
            model = store.activate_spatial_model(db, model_id)
        except KeyError as exc:
            raise HTTPException(404, "workspace not found") from exc
        status = manager.apply_workspace(model) if apply_to_session else None
        return {"model": model.model_dump(mode="json"), "session": status.model_dump() if status else None}

    @app.delete("/api/spatial-models/{model_id}")
    def delete_model(model_id: str):
        if model_id in (store.BLANK_WORKSPACE_ID, KITCHEN_TEMPLATE_ID):
            raise HTTPException(400, "built-in workspaces cannot be deleted")
        if manager.model is not None and manager.model.model_id == model_id and manager.running():
            raise HTTPException(409, "workspace is in use by the running session")
        if not store.delete_spatial_model(db, model_id):
            raise HTTPException(404, "workspace not found")
        shutil.rmtree(settings.workspaces_dir / model_id, ignore_errors=True)
        return {"deleted": model_id}

    @app.post("/api/workspaces/analyze")
    def analyze_workspace(request: WorkspaceAnalysisRequest):
        try:
            return analyzer.start(request).public()
        except AnalysisError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/workspaces/analyze/{job_id}")
    def analysis_status(job_id: str):
        job = analyzer.get(job_id)
        if job is None:
            raise HTTPException(404, "analysis job not found")
        return job.public()

    @app.post("/api/workspaces/{model_id}/generate-world")
    def generate_world_from_workspace(model_id: str):
        """Send the workspace's people-free background plate to Image-Blaster as the reference image."""
        plate = settings.workspaces_dir / model_id / "background.jpg"
        if not plate.is_file():
            raise HTTPException(404, "workspace has no background plate; analyze it first")
        slug = "".join(ch if ch.isalnum() else "-" for ch in model_id.lower()).strip("-")[:60]
        try:
            path = generator.stage_reference(slug, "reference.jpg", plate.read_bytes())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        job = generator.start(slug, path)
        return generator.status(job.slug) | {"fallback_scene_id": PROCEDURAL_SCENE_ID}

    # ------------------------------------------------------------------------- scenes
    @app.get("/api/scenes")
    def scenes():
        return [s | {"config": store.get_scene_config(db, s["scene_id"]).model_dump()} for s in list_scenes(settings)]

    @app.get("/api/scenes/{scene_id}/config", response_model=SceneConfig)
    def get_scene_config(scene_id: str):
        return store.get_scene_config(db, scene_id)

    @app.put("/api/scenes/{scene_id}/config", response_model=SceneConfig)
    def put_scene_config(scene_id: str, config: SceneConfig):
        if config.scene_id != scene_id:
            raise HTTPException(400, "scene_id mismatch")
        return store.put_scene_config(db, config)

    @app.post("/api/scenes/generate")
    async def generate_scene(slug: str = Form(...), image: UploadFile = File(...)):
        data = await image.read()
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(413, "reference image too large (25 MB max)")
        try:
            path = generator.stage_reference(slug, image.filename or "reference.png", data)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        job = generator.start(slug, path)
        return generator.status(job.slug) | {"fallback_scene_id": PROCEDURAL_SCENE_ID}

    @app.get("/api/scenes/generate/{slug}")
    def generation_status(slug: str):
        return generator.status(slug)

    # ------------------------------------------------------------------------- video sources
    @app.post("/api/videos")
    async def upload_video(file: UploadFile = File(...)):
        name = Path(file.filename or "upload.mp4").name
        ext = Path(name).suffix.lower()
        if ext not in VIDEO_EXTENSIONS:
            raise HTTPException(400, f"unsupported video type {ext!r}")
        vid_name = f"{new_id('upload')}{ext}"
        target = settings.uploads_dir / vid_name
        limit = settings.max_upload_mb * 1024 * 1024
        written = 0
        with target.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    out.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(413, f"video exceeds {settings.max_upload_mb} MB")
                out.write(chunk)
        try:
            meta = await asyncio.to_thread(validate_video_file, target)
        except VideoSourceError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(400, str(exc)) from exc
        video_id = store.register_video(db, path=str(target), original_name=name, meta=meta)
        return {"video_id": video_id, "name": name, **meta}

    @app.get("/api/videos")
    def videos():
        return store.list_videos(db)

    @app.get("/api/cameras/probe")
    def probe_cameras(max_index: int = 3):
        found = []
        backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
        for idx in range(max(0, min(max_index, 8)) + 1):
            if manager.runner is not None and not manager.runner.wait(0) and manager.config and manager.config.camera_index == idx:
                found.append({"index": idx, "available": True, "in_use": True})
                continue
            cap = cv2.VideoCapture(idx, backend)
            ok = cap.isOpened() and cap.read()[0]
            if ok:
                found.append({"index": idx, "available": True, "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))})
            cap.release()
        return found

    # ------------------------------------------------------------------------- calibration
    def _calibrate(request: CalibrationRequest):
        try:
            return compute_calibration(request)
        except CalibrationError as exc:
            raise HTTPException(422, str(exc)) from exc

    def _overlay(result) -> dict:
        model = manager.current_model()
        projector = GroundPlaneProjector(result)
        to_img = lambda pts: [projector.world_to_image(x, y) for x, y in pts]  # noqa: E731
        return {
            "zones": [{"zone_id": z.zone_id, "color": z.color, "image_polygon": to_img(z.polygon)} for z in model.zones],
            "floor": to_img(model.floor_polygon),
            "reference_points": [{"point_id": p.point_id, "image": projector.world_to_image(p.x, p.y)} for p in model.reference_points],
        }

    @app.post("/api/calibrations/preview")
    def preview_calibration(request: CalibrationRequest):
        result = _calibrate(request)
        return {"calibration": result.model_dump(), "overlay": _overlay(result)}

    @app.post("/api/calibrations")
    def create_calibration(request: CalibrationRequest):
        result = store.save_calibration(db, _calibrate(request))
        return {"calibration": result.model_dump(), "overlay": _overlay(result)}

    @app.get("/api/calibrations")
    def calibrations():
        return store.list_calibrations(db)

    @app.get("/api/calibrations/{calibration_id}")
    def calibration(calibration_id: str):
        result = store.get_calibration(db, calibration_id)
        if result is None:
            raise HTTPException(404, "calibration not found")
        return {"calibration": result.model_dump(), "overlay": _overlay(result)}

    # ------------------------------------------------------------------------- sessions
    @app.post("/api/sessions/start")
    async def start_session(config: SessionConfig):
        try:
            status = await asyncio.to_thread(manager.start, config)
        except SessionStartError as exc:
            raise HTTPException(409 if "already running" in str(exc) else 400, str(exc)) from exc
        return status.model_dump()

    @app.post("/api/sessions/stop")
    async def stop_session():
        return (await asyncio.to_thread(manager.stop)).model_dump()

    @app.get("/api/sessions/current")
    def current_session():
        status = manager.status().model_dump()
        runner = manager.runner
        cal = runner.calibration if runner else None
        return status | {
            "calibration": cal.model_dump() if cal else None,
            "overlay": _overlay(cal) if cal else None,
            "config": manager.config.model_dump() if manager.config else None,
        }

    @app.post("/api/sessions/current/calibration")
    def apply_calibration(body: dict):
        try:
            return manager.set_calibration(body.get("calibration_id")).model_dump()
        except SessionStartError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/sessions")
    def sessions(limit: int = 50):
        return store.list_sessions(db, limit)

    @app.get("/api/sessions/{session_id}/replay")
    def replay(session_id: str):
        try:
            return build_replay(db, session_id)
        except SessionNotFound as exc:
            raise HTTPException(404, "session not found") from exc

    @app.get("/api/sessions/{session_id}/entities/{entity_id}/history")
    def history(session_id: str, entity_id: str):
        return entity_history(db, session_id, entity_id)

    def _session_model(session_id: str) -> SpatialModel:
        """Metrics use the workspace the session was recorded against, not whatever is active now."""
        with db.Session() as s:
            row = s.get(SessionRow, session_id)
            if row is None:
                raise SessionNotFound(session_id)
            model_id, simulated = row.spatial_model_id, row.simulated
        if simulated:
            return default_kitchen()
        try:
            return store.get_spatial_model(db, model_id or store.BLANK_WORKSPACE_ID)
        except KeyError:
            return store.blank_workspace()

    @app.get("/api/sessions/{session_id}/model", response_model=SpatialModel)
    def session_model(session_id: str):
        try:
            return _session_model(session_id)
        except SessionNotFound as exc:
            raise HTTPException(404, "session not found") from exc

    @app.get("/api/sessions/{session_id}/metrics")
    def metrics(session_id: str):
        try:
            return compute_session_metrics(db, session_id, _session_model(session_id))
        except SessionNotFound as exc:
            raise HTTPException(404, "session not found") from exc

    # ------------------------------------------------------------------------- websocket
    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await hub.serve(websocket, [manager.status().model_dump_json()])

    # ------------------------------------------------------------------------- static
    app.mount("/worlds", StaticFiles(directory=settings.worlds_dir, check_dir=False), name="worlds")
    app.mount("/workspace-assets", StaticFiles(directory=settings.workspaces_dir, check_dir=False), name="workspace-assets")
    dist = REPO_ROOT / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            candidate = (dist / path).resolve()
            if path and candidate.is_relative_to(dist.resolve()) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


def main() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run("echo.api.app:create_app", factory=True, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
