from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from influencer import config as config_module

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "persona.toml"


@pytest.fixture()
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> config_module.Config:
    """The shipped config, copied into a temp dir so tests never touch real state."""
    monkeypatch.setenv("MEDIA_BASE_URL", "https://media.example.test")
    monkeypatch.setenv("IG_USER_ID", "1784000000000")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "test-token")

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    target = config_dir / "persona.toml"
    shutil.copy(EXAMPLE, target)
    return config_module.load(target)
