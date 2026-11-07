"""Persistence helpers for configuration-like records."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update

from echo.memory.db import (
    CalibrationRow,
    CameraRow,
    Database,
    SceneConfigRow,
    SessionRow,
    SpatialModelRow,
    VideoSourceRow,
    ZoneRow,
    new_id,
    now_ms,
)
from echo.memory.replay import session_summary
from echo.schemas import CalibrationResult, SceneConfig, SessionConfig, SpatialModel
from echo.spatial.kitchen import KITCHEN_TEMPLATE_ID, default_kitchen


def save_calibration(db: Database, result: CalibrationResult) -> CalibrationResult:
    cid = new_id("cal")
    with db.Session() as s:
        if s.get(CameraRow, result.camera_id) is None:
            s.add(CameraRow(id=result.camera_id, name=result.camera_id, kind="simulation" if result.simulated else "physical", width=result.image_width, height=result.image_height))
        s.add(
            CalibrationRow(
                id=cid,
                camera_id=result.camera_id,
                image_width=result.image_width,
                image_height=result.image_height,
                homography=result.homography,
                points=[p.model_dump(mode="json") for p in result.points],
                reprojection_rmse_m=result.reprojection_rmse_m,
                max_error_m=result.max_error_m,
                warnings=result.warnings,
                simulated=result.simulated,
                label=result.label,
                method=result.method,
            )
        )
        s.commit()
    return result.model_copy(update={"calibration_id": cid})


def _calibration_from_row(row: CalibrationRow) -> CalibrationResult:
    return CalibrationResult(
        calibration_id=row.id,
        camera_id=row.camera_id,
        image_width=row.image_width,
        image_height=row.image_height,
        homography=row.homography,
        reprojection_rmse_m=row.reprojection_rmse_m,
        max_error_m=row.max_error_m,
        point_errors_m=[],
        points=row.points,
        warnings=row.warnings or [],
        simulated=row.simulated,
        label=row.label,
        method=row.method or "points",
    )


def get_calibration(db: Database, calibration_id: str) -> CalibrationResult | None:
    with db.Session() as s:
        row = s.get(CalibrationRow, calibration_id)
        return _calibration_from_row(row) if row else None


def list_calibrations(db: Database) -> list[dict[str, Any]]:
    with db.Session() as s:
        rows = s.scalars(select(CalibrationRow).order_by(CalibrationRow.created_wall_ms.desc())).all()
        return [
            {
                "calibration_id": r.id,
                "camera_id": r.camera_id,
                "created_wall_ms": r.created_wall_ms,
                "image_width": r.image_width,
                "image_height": r.image_height,
                "reprojection_rmse_m": r.reprojection_rmse_m,
                "max_error_m": r.max_error_m,
                "points": len(r.points),
                "warnings": r.warnings or [],
                "simulated": r.simulated,
                "label": r.label,
                "method": r.method or "points",
            }
            for r in rows
        ]


BLANK_WORKSPACE_ID = "workspace-blank"


def blank_workspace() -> SpatialModel:
    """Placeholder until a workspace is analyzed or drawn: no zones, no fixtures, nominal floor."""
    return SpatialModel(
        model_id=BLANK_WORKSPACE_ID,
        name="Unmapped workspace",
        width_m=10.0,
        depth_m=10.0,
        floor_polygon=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
        zones=[],
        source="blank",
        notes=["No layout yet. Run 'Analyze workspace' on a video or live camera, or calibrate and draw zones."],
    )


def model_for_video(db: Database, video_id: str) -> SpatialModel | None:
    """Newest analyzed layout measured from this file, preferring one that has stations."""
    video = get_video(db, video_id)
    if video is None:
        return None
    filename = video.original_name or ""
    ranked: list[tuple[bool, int, float, SpatialModel]] = []
    with db.Session() as s:
        rows = s.scalars(select(SpatialModelRow).order_by(SpatialModelRow.updated_wall_ms.desc())).all()
        stamped = [(row.updated_wall_ms, SpatialModel.model_validate(row.model)) for row in rows]
    for updated, model in stamped:
        if model.source != "analyzed":
            continue
        if model.video_id == video_id or (filename and filename in model.name):
            ranked.append((bool(model.fixtures), len(model.fixtures), updated, model))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    return ranked[0][3]


def get_spatial_model(db: Database, model_id: str | None = None) -> SpatialModel:
    """A specific model, or the active workspace. Seeds the blank workspace + kitchen template."""
    _seed_models(db)
    with db.Session() as s:
        if model_id is not None:
            row = s.get(SpatialModelRow, model_id)
            if row is None:
                raise KeyError(model_id)
        else:
            row = s.scalars(select(SpatialModelRow).where(SpatialModelRow.active.is_(True))).first()
            if row is None:
                row = s.get(SpatialModelRow, BLANK_WORKSPACE_ID)
        return SpatialModel.model_validate(row.model)


def _seed_models(db: Database) -> None:
    with db.Session() as s:
        has_blank = s.get(SpatialModelRow, BLANK_WORKSPACE_ID) is not None
        has_template = s.get(SpatialModelRow, KITCHEN_TEMPLATE_ID) is not None
        has_active = s.scalars(select(SpatialModelRow.id).where(SpatialModelRow.active.is_(True))).first() is not None
    if not has_template:
        put_spatial_model(db, default_kitchen())
    if not has_blank:
        put_spatial_model(db, blank_workspace(), activate=not has_active)


def put_spatial_model(db: Database, model: SpatialModel, *, activate: bool = False) -> SpatialModel:
    with db.Session() as s:
        row = s.get(SpatialModelRow, model.model_id)
        payload = model.model_dump(mode="json")
        if row is None:
            row = SpatialModelRow(id=model.model_id, name=model.name, model=payload, source=model.source)
            s.add(row)
        else:
            row.name, row.model, row.source, row.updated_wall_ms = model.name, payload, model.source, now_ms()
        if activate:
            s.execute(update(SpatialModelRow).where(SpatialModelRow.id != model.model_id).values(active=False))
            row.active = True
        s.flush()
        s.execute(delete(ZoneRow).where(ZoneRow.spatial_model_id == model.model_id))
        for z in model.zones:
            s.add(
                ZoneRow(
                    spatial_model_id=model.model_id,
                    zone_id=z.zone_id,
                    name=z.name,
                    kind=z.kind,
                    polygon=[list(p) for p in z.polygon],
                    color=z.color,
                    provenance=z.provenance,
                )
            )
        s.commit()
    return model


def activate_spatial_model(db: Database, model_id: str) -> SpatialModel:
    model = get_spatial_model(db, model_id)
    return put_spatial_model(db, model, activate=True)


def delete_spatial_model(db: Database, model_id: str) -> bool:
    """Remove a workspace; if it was active, the blank workspace becomes active."""
    with db.Session() as s:
        row = s.get(SpatialModelRow, model_id)
        if row is None:
            return False
        was_active = bool(row.active)
        s.execute(delete(ZoneRow).where(ZoneRow.spatial_model_id == model_id))
        s.delete(row)
        if was_active:
            s.execute(update(SpatialModelRow).where(SpatialModelRow.id == BLANK_WORKSPACE_ID).values(active=True))
        s.commit()
    return True


def list_spatial_models(db: Database) -> list[dict[str, Any]]:
    _seed_models(db)
    with db.Session() as s:
        rows = s.scalars(select(SpatialModelRow).order_by(SpatialModelRow.updated_wall_ms.desc())).all()
        return [
            {
                "model_id": r.id,
                "name": r.name,
                "source": r.source,
                "active": bool(r.active),
                "updated_wall_ms": r.updated_wall_ms,
                "zones": len(r.model.get("zones", [])),
                "fixtures": len(r.model.get("fixtures", [])),
            }
            for r in rows
        ]


def get_scene_config(db: Database, scene_id: str) -> SceneConfig:
    with db.Session() as s:
        row = s.get(SceneConfigRow, scene_id)
        return SceneConfig.model_validate(row.config) if row else SceneConfig(scene_id=scene_id)


def put_scene_config(db: Database, config: SceneConfig) -> SceneConfig:
    with db.Session() as s:
        row = s.get(SceneConfigRow, config.scene_id)
        payload = config.model_dump(mode="json")
        if row is None:
            s.add(SceneConfigRow(scene_id=config.scene_id, config=payload))
        else:
            row.config, row.updated_wall_ms = payload, now_ms()
        s.commit()
    return config


def register_video(db: Database, *, path: str, original_name: str, meta: dict[str, Any]) -> str:
    vid = new_id("vid")
    with db.Session() as s:
        s.add(VideoSourceRow(id=vid, kind="file", path=path, original_name=original_name, **meta))
        s.commit()
    return vid


def get_video(db: Database, video_id: str) -> VideoSourceRow | None:
    with db.Session() as s:
        return s.get(VideoSourceRow, video_id)


def list_videos(db: Database) -> list[dict[str, Any]]:
    with db.Session() as s:
        rows = s.scalars(select(VideoSourceRow).where(VideoSourceRow.kind == "file").order_by(VideoSourceRow.created_wall_ms.desc())).all()
        return [
            {"video_id": r.id, "name": r.original_name, "width": r.width, "height": r.height, "fps": r.fps, "frame_count": r.frame_count, "duration_ms": r.duration_ms}
            for r in rows
        ]


def ensure_camera(db: Database, camera_id: str, *, kind: str, device_index: int | None, width: int, height: int) -> None:
    with db.Session() as s:
        row = s.get(CameraRow, camera_id)
        if row is None:
            s.add(CameraRow(id=camera_id, name=camera_id, kind=kind, device_index=device_index, width=width, height=height))
        else:
            row.width, row.height = width, height
        s.commit()


def create_session(
    db: Database,
    *,
    session_id: str,
    config: SessionConfig,
    source_kind: str,
    simulated: bool,
    spatial_model_id: str,
    calibration_id: str | None,
    video_source_id: str | None,
    camera_id: str | None,
) -> None:
    with db.Session() as s:
        s.add(
            SessionRow(
                id=session_id,
                state="running",
                source_kind=source_kind,
                video_source_id=video_source_id,
                camera_id=camera_id,
                calibration_id=calibration_id,
                scene_id=config.scene_id,
                spatial_model_id=spatial_model_id,
                simulated=simulated,
                config=config.model_dump(mode="json"),
            )
        )
        s.commit()


def set_session_calibration(db: Database, session_id: str, calibration_id: str | None) -> None:
    with db.Session() as s:
        row = s.get(SessionRow, session_id)
        if row is not None:
            row.calibration_id = calibration_id
            s.commit()


def list_sessions(db: Database, limit: int = 50) -> list[dict[str, Any]]:
    with db.Session() as s:
        rows = s.scalars(select(SessionRow).order_by(SessionRow.created_wall_ms.desc()).limit(limit)).all()
        return [session_summary(r) for r in rows]
