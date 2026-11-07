"""Scene discovery over Image-Blaster's on-disk world layout, plus one-shot generation hand-off.

Layout (identical to Image-Blaster): ``worlds/<slug>/output/world/N-world.json`` with sibling
``N-world-<lod>.spz`` (or ``.ply``), ``N-world.glb`` collider, ``N-world-pano.<ext>``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from echo.config import REPO_ROOT, Settings

log = logging.getLogger("echo.scenes")

PROCEDURAL_SCENE_ID = "procedural"
_INDEXED = re.compile(r"^(\d+)-(.+?)(\.[^.]+)$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SPLAT_PREFERENCE = ["full_res", "500k", "150k", "100k"]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _indexed_files(directory: Path) -> list[tuple[int, str, str, Path]]:
    if not directory.is_dir():
        return []
    out = []
    for f in directory.iterdir():
        if not f.is_file() or f.name.startswith("."):
            continue
        m = _INDEXED.match(f.name)
        if m:
            out.append((int(m.group(1)), m.group(2), m.group(3).lower(), f))
    return out


def _url(settings: Settings, path: Path) -> str:
    return "/worlds/" + path.relative_to(settings.worlds_dir).as_posix()


def procedural_scene() -> dict[str, Any]:
    return {
        "scene_id": PROCEDURAL_SCENE_ID,
        "kind": "procedural",
        "display_name": "Reconstructed workspace (from spatial model)",
        "splat_url": None,
        "collider_url": None,
        "pano_url": None,
        "semantics": None,
        "synthetic": False,
        "notes": "Floor texture, objects and zones of the active workspace model; metrically aligned by construction.",
    }


def read_world_scene(settings: Settings, slug: str) -> dict[str, Any] | None:
    world_dir = settings.worlds_dir / slug / "output" / "world"
    files = _indexed_files(world_dir)
    manifests = sorted((f for f in files if f[1] == "world" and f[2] == ".json"), key=lambda f: f[0])
    if not manifests:
        return None
    index, _, _, manifest_path = manifests[-1]
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        log.warning("unreadable world manifest %s", manifest_path)
        return None
    same = [f for f in files if f[0] == index]
    splat = None
    for lod in _SPLAT_PREFERENCE:
        splat = next((f[3] for f in same if f[1] == f"world-{lod}" and f[2] in (".spz", ".ply", ".splat", ".ksplat")), None)
        if splat:
            break
    collider = next((f[3] for f in same if f[1] == "world" and f[2] == ".glb"), None)
    pano = next((f[3] for f in same if f[1] == "world-pano" and f[2] in IMAGE_EXTENSIONS), None)
    semantics = {
        "metric_scale_factor": 1.0,
        "ground_plane_offset": 0.0,
        "flip_y": True,
        **((manifest.get("assets") or {}).get("splats") or {}).get("semantics_metadata", {}),
    }
    project = {}
    project_path = settings.worlds_dir / slug / "project.json"
    if project_path.is_file():
        try:
            project = json.loads(project_path.read_text())
        except json.JSONDecodeError:
            project = {}
    return {
        "scene_id": slug,
        "kind": "image-blaster",
        "display_name": project.get("display_name") or manifest.get("display_name") or slug,
        "world_index": index,
        "splat_url": _url(settings, splat) if splat else None,
        "collider_url": _url(settings, collider) if collider else None,
        "pano_url": _url(settings, pano) if pano else None,
        "semantics": semantics,
        "synthetic": bool(manifest.get("echo_synthetic")),
        "notes": manifest.get("echo_notes"),
    }


def list_scenes(settings: Settings) -> list[dict[str, Any]]:
    scenes = [procedural_scene()]
    if settings.worlds_dir.is_dir():
        for d in sorted(settings.worlds_dir.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                scene = read_world_scene(settings, d.name)
                if scene:
                    scenes.append(scene)
    return scenes


# ------------------------------------------------------------------------------- generation


def _world_labs_key(settings: Settings) -> str | None:
    if os.environ.get("WORLD_LABS_API_KEY"):
        return os.environ["WORLD_LABS_API_KEY"]
    env = REPO_ROOT / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "WORLD_LABS_API_KEY" and v.strip():
                return v.strip().strip("'\"")
    return None


@dataclass
class GenerationJob:
    slug: str
    process: subprocess.Popen | None
    started_wall_ms: float
    log_path: Path
    status: str
    reason: str | None = None


class SceneGenerator:
    """Runs Image-Blaster's own ``generate-world.mjs`` once per reference image."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.jobs: dict[str, GenerationJob] = {}

    def stage_reference(self, slug: str, filename: str, data: bytes) -> Path:
        if not _SLUG.match(slug):
            raise ValueError("slug must be lowercase letters, digits and dashes")
        ext = Path(filename).suffix.lower()
        if ext not in IMAGE_EXTENSIONS:
            raise ValueError(f"reference image must be one of {sorted(IMAGE_EXTENSIONS)}")
        source_dir = self.settings.worlds_dir / slug / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        target = source_dir / f"0-{slug}{ext}"
        target.write_bytes(data)
        project = self.settings.worlds_dir / slug / "project.json"
        if not project.exists():
            project.write_text(json.dumps({"slug": slug, "display_name": slug.replace("-", " ").title()}, indent=2) + "\n")
        return target

    def start(self, slug: str, image_path: Path) -> GenerationJob:
        existing = self.jobs.get(slug)
        if existing and existing.process and existing.process.poll() is None:
            return existing
        log_path = self.settings.data_dir / f"generate-{slug}.log"
        script = self.settings.image_blaster_dir / ".claude" / "scripts" / "world" / "generate-world.mjs"
        reason = None
        if not script.is_file():
            reason = f"Image-Blaster not found at {self.settings.image_blaster_dir}"
        elif shutil.which("node") is None:
            reason = "node is not installed"
        elif not _world_labs_key(self.settings):
            reason = "WORLD_LABS_API_KEY is not set (World Labs Marble is a paid API); using the procedural fallback"
        if reason:
            job = GenerationJob(slug, None, time.time() * 1000, log_path, "unavailable", reason)
            self.jobs[slug] = job
            return job
        env = dict(os.environ, WORLD_LABS_API_KEY=_world_labs_key(self.settings) or "")
        cwd = self.settings.worlds_dir.parent
        rel_image = os.path.relpath(image_path, cwd)
        log_file = open(log_path, "w")
        proc = subprocess.Popen(
            ["node", str(script), "--world", slug, "--image", rel_image],
            cwd=cwd,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        job = GenerationJob(slug, proc, time.time() * 1000, log_path, "running")
        self.jobs[slug] = job
        return job

    def status(self, slug: str) -> dict[str, Any]:
        job = self.jobs.get(slug)
        if job is None:
            scene = read_world_scene(self.settings, slug)
            return {"slug": slug, "status": "completed" if scene else "unknown"}
        if job.process is not None and job.status == "running":
            code = job.process.poll()
            if code is not None:
                job.status = "completed" if code == 0 and read_world_scene(self.settings, slug) else "failed"
                if job.status == "failed":
                    job.reason = job.log_path.read_text()[-2000:] if job.log_path.exists() else f"exit code {code}"
        return {"slug": slug, "status": job.status, "reason": job.reason, "started_wall_ms": job.started_wall_ms}
