from __future__ import annotations

from pathlib import Path

import pytest

from influencer import config as config_module


def test_example_config_loads(cfg):
    assert cfg.persona.name == "Mara Solis"
    assert cfg.persona.handle == "mara.solis.ai"
    assert len(cfg.persona.pillars) == 5
    assert cfg.schedule.timezone == "Europe/Lisbon"


def test_env_interpolation(cfg):
    assert cfg.storage.public_base_url == "https://media.example.test"
    assert cfg.instagram.access_token == "test-token"
    assert cfg.missing_env == ()


def test_unset_env_is_recorded_not_fatal(tmp_path: Path):
    source = tmp_path / "p.toml"
    source.write_text(
        """
[persona]
name = "X"
handle = "x"
[[persona.pillars]]
key = "a"
scenes = ["somewhere"]
[instagram]
access_token = "${DEFINITELY_NOT_SET_12345}"
""",
        encoding="utf-8",
    )
    cfg = config_module.load(source)
    assert cfg.instagram.access_token == ""
    assert "DEFINITELY_NOT_SET_12345" in cfg.missing_env
    with pytest.raises(config_module.ConfigError):
        cfg.require_env("DEFINITELY_NOT_SET_12345")


def test_handle_strips_at_sign(tmp_path: Path):
    source = tmp_path / "p.toml"
    source.write_text(
        """
[persona]
name = "X"
handle = "@someone"
[[persona.pillars]]
key = "a"
scenes = ["somewhere"]
""",
        encoding="utf-8",
    )
    assert config_module.load(source).persona.handle == "someone"


def test_pillar_without_scenes_is_rejected(tmp_path: Path):
    source = tmp_path / "p.toml"
    source.write_text(
        """
[persona]
name = "X"
handle = "x"
[[persona.pillars]]
key = "empty"
scenes = []
""",
        encoding="utf-8",
    )
    with pytest.raises(config_module.ConfigError, match="must not be empty"):
        config_module.load(source)


def test_missing_file(tmp_path: Path):
    with pytest.raises(config_module.ConfigError, match="not found"):
        config_module.load(tmp_path / "nope.toml")
