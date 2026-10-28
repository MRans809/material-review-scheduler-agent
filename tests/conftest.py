from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / "data"
    target = tmp_path / "data"
    shutil.copytree(source, target)
    return target
