from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: _env_path("ECHO_DATA_DIR", REPO_ROOT / "data"))
    worlds_dir: Path = field(default_factory=lambda: _env_path("ECHO_WORLDS_DIR", REPO_ROOT / "worlds"))
    image_blaster_dir: Path = field(
        default_factory=lambda: _env_path("ECHO_IMAGE_BLASTER_DIR", REPO_ROOT / "third_party" / "image-blaster")
    )
    yolo_weights: Path = field(default_factory=lambda: _env_path("ECHO_YOLO_WEIGHTS", BACKEND_DIR / "models" / "yolo11n.pt"))
    yolo_world_weights: Path = field(
        default_factory=lambda: _env_path("ECHO_YOLO_WORLD_WEIGHTS", BACKEND_DIR / "models" / "yolov8s-worldv2.pt")
    )
    database_url: str = field(default_factory=lambda: os.environ.get("ECHO_DATABASE_URL", ""))
    max_upload_mb: int = int(os.environ.get("ECHO_MAX_UPLOAD_MB", "500"))

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'echo.sqlite3'}"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        self.worlds_dir.mkdir(parents=True, exist_ok=True)


def load_repo_env() -> None:
    """Load KEY=VALUE lines from the repo `.env`. Existing environment variables win."""
    path = REPO_ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def get_settings() -> Settings:
    load_repo_env()
    return Settings()
