from __future__ import annotations

from pathlib import Path

import pytest

from echo.config import Settings
from echo.memory.db import Database
from echo.spatial.kitchen import default_kitchen


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(data_dir=tmp_path / "data", worlds_dir=tmp_path / "worlds")
    s.ensure_dirs()
    return s


@pytest.fixture
def db(settings: Settings) -> Database:
    return Database(settings.resolved_database_url)


@pytest.fixture
def kitchen():
    return default_kitchen()
