"""Configuration loading.

Config lives in a TOML file. Secrets are never written there: a value of the
form ``${NAME}`` is replaced with the environment variable ``NAME``. Variables
that are not set resolve to an empty string and are recorded in
``Config.missing_env`` so that dry runs work with no credentials at all and the
components that genuinely need a secret can raise a specific error.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(Exception):
    """Raised when a config file is missing, malformed, or incomplete."""


@dataclass(frozen=True)
class Voice:
    tone: str = "plain, specific, warm"
    emoji: str = "sparing"
    sentence_style: str = "short lines"
    avoid: tuple[str, ...] = ()


@dataclass(frozen=True)
class Pillar:
    """One recurring content theme, e.g. 'studio' or 'city walks'."""

    key: str
    weight: int = 1
    themes: tuple[str, ...] = ()
    scenes: tuple[str, ...] = ()
    hashtags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Persona:
    name: str
    handle: str
    bio: str = ""
    home_base: str = ""
    # Repeated verbatim in every image prompt so the character stays recognisable.
    appearance: str = ""
    style_note: str = ""
    wardrobe: tuple[str, ...] = ()
    base_hashtags: tuple[str, ...] = ()
    seed: int = 0
    voice: Voice = field(default_factory=Voice)
    pillars: tuple[Pillar, ...] = ()


@dataclass(frozen=True)
class ScheduleConfig:
    times: tuple[str, ...] = ("18:30",)
    timezone: str = "UTC"
    max_posts_per_day: int = 2
    # Refuse to publish if the clock is more than this many minutes past a slot.
    slot_grace_minutes: int = 90


@dataclass(frozen=True)
class ImageConfig:
    provider: str = "dryrun"
    aspect: str = "4:5"
    width: int = 1080
    height: int = 1350
    quality_suffix: str = "photographic, natural light, shallow depth of field"
    negative: str = "text, watermark, extra fingers, distorted hands, logo"
    # Provider-specific settings (endpoint, model name, ...).
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CaptionConfig:
    provider: str = "auto"  # auto | claude | template
    model: str = "claude-opus-5"
    effort: str = "low"
    max_chars: int = 900
    max_hashtags: int = 12
    hashtags_in_first_comment: bool = True


@dataclass(frozen=True)
class StorageConfig:
    provider: str = "local"  # local | s3
    directory: str = "out/media"
    public_base_url: str = ""
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InstagramConfig:
    ig_user_id: str = ""
    access_token: str = ""
    api_version: str = "v21.0"
    graph_base: str = "https://graph.facebook.com"


@dataclass(frozen=True)
class SafetyConfig:
    disclosure: str = "AI-generated character. Not a real person."
    disclosure_hashtags: tuple[str, ...] = ("#aigenerated", "#aiart")
    banned_topics: tuple[str, ...] = ()
    require_disclosure: bool = True


@dataclass(frozen=True)
class Config:
    persona: Persona
    schedule: ScheduleConfig
    image: ImageConfig
    caption: CaptionConfig
    storage: StorageConfig
    instagram: InstagramConfig
    safety: SafetyConfig
    state_path: Path
    source: Path
    missing_env: tuple[str, ...] = ()

    def require_env(self, *names: str) -> None:
        absent = [n for n in names if n in self.missing_env]
        if absent:
            raise ConfigError(
                "these environment variables are needed for this command but are "
                "not set: " + ", ".join(sorted(absent))
            )


def _interpolate(value: Any, missing: set[str]) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            found = os.environ.get(name)
            if found is None:
                missing.add(name)
                return ""
            return found

        return _ENV_REF.sub(replace, value)
    if isinstance(value, dict):
        return {k: _interpolate(v, missing) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v, missing) for v in value]
    return value


def _tuple(raw: Any, field_name: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ConfigError(f"{field_name} must be a list of strings")
    return tuple(raw)


def _persona(raw: dict[str, Any]) -> Persona:
    for required in ("name", "handle"):
        if not raw.get(required):
            raise ConfigError(f"persona.{required} is required")

    voice_raw = raw.get("voice") or {}
    voice = Voice(
        tone=voice_raw.get("tone", Voice.tone),
        emoji=voice_raw.get("emoji", Voice.emoji),
        sentence_style=voice_raw.get("sentence_style", Voice.sentence_style),
        avoid=_tuple(voice_raw.get("avoid"), "persona.voice.avoid"),
    )

    pillars_raw = raw.get("pillars") or []
    if not isinstance(pillars_raw, list):
        raise ConfigError("persona.pillars must be an array of tables")
    pillars: list[Pillar] = []
    for index, item in enumerate(pillars_raw):
        key = item.get("key")
        if not key:
            raise ConfigError(f"persona.pillars[{index}].key is required")
        scenes = _tuple(item.get("scenes"), f"persona.pillars[{index}].scenes")
        if not scenes:
            raise ConfigError(f"persona.pillars[{index}].scenes must not be empty")
        weight = item.get("weight", 1)
        if not isinstance(weight, int) or weight < 1:
            raise ConfigError(f"persona.pillars[{index}].weight must be a positive integer")
        pillars.append(
            Pillar(
                key=key,
                weight=weight,
                themes=_tuple(item.get("themes"), f"persona.pillars[{index}].themes"),
                scenes=scenes,
                hashtags=_tuple(item.get("hashtags"), f"persona.pillars[{index}].hashtags"),
            )
        )
    if not pillars:
        raise ConfigError("persona.pillars must define at least one content pillar")

    handle = raw["handle"].lstrip("@")
    return Persona(
        name=raw["name"],
        handle=handle,
        bio=raw.get("bio", ""),
        home_base=raw.get("home_base", ""),
        appearance=raw.get("appearance", ""),
        style_note=raw.get("style_note", ""),
        wardrobe=_tuple(raw.get("wardrobe"), "persona.wardrobe"),
        base_hashtags=_tuple(raw.get("base_hashtags"), "persona.base_hashtags"),
        seed=int(raw.get("seed", 0)),
        voice=voice,
        pillars=tuple(pillars),
    )


def load(path: str | Path) -> Config:
    """Read and validate a persona config file."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise ConfigError(f"config file not found: {source}")

    try:
        raw = tomllib.loads(source.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{source}: invalid TOML: {exc}") from exc

    missing: set[str] = set()
    raw = _interpolate(raw, missing)

    schedule_raw = raw.get("schedule") or {}
    image_raw = dict(raw.get("image") or {})
    caption_raw = raw.get("caption") or {}
    storage_raw = dict(raw.get("storage") or {})
    instagram_raw = raw.get("instagram") or {}
    safety_raw = raw.get("safety") or {}

    image_options = image_raw.pop("options", {}) or {}
    storage_options = storage_raw.pop("options", {}) or {}

    schedule = ScheduleConfig(
        times=_tuple(schedule_raw.get("times"), "schedule.times") or ScheduleConfig.times,
        timezone=schedule_raw.get("timezone", ScheduleConfig.timezone),
        max_posts_per_day=int(
            schedule_raw.get("max_posts_per_day", ScheduleConfig.max_posts_per_day)
        ),
        slot_grace_minutes=int(
            schedule_raw.get("slot_grace_minutes", ScheduleConfig.slot_grace_minutes)
        ),
    )
    if schedule.max_posts_per_day < 1:
        raise ConfigError("schedule.max_posts_per_day must be at least 1")

    image = ImageConfig(
        provider=image_raw.get("provider", ImageConfig.provider),
        aspect=image_raw.get("aspect", ImageConfig.aspect),
        width=int(image_raw.get("width", ImageConfig.width)),
        height=int(image_raw.get("height", ImageConfig.height)),
        quality_suffix=image_raw.get("quality_suffix", ImageConfig.quality_suffix),
        negative=image_raw.get("negative", ImageConfig.negative),
        options=image_options,
    )

    caption = CaptionConfig(
        provider=caption_raw.get("provider", CaptionConfig.provider),
        model=caption_raw.get("model", CaptionConfig.model),
        effort=caption_raw.get("effort", CaptionConfig.effort),
        max_chars=int(caption_raw.get("max_chars", CaptionConfig.max_chars)),
        max_hashtags=int(caption_raw.get("max_hashtags", CaptionConfig.max_hashtags)),
        hashtags_in_first_comment=bool(
            caption_raw.get(
                "hashtags_in_first_comment", CaptionConfig.hashtags_in_first_comment
            )
        ),
    )

    storage = StorageConfig(
        provider=storage_raw.get("provider", StorageConfig.provider),
        directory=storage_raw.get("directory", StorageConfig.directory),
        public_base_url=storage_raw.get("public_base_url", "").rstrip("/"),
        options=storage_options,
    )

    instagram = InstagramConfig(
        ig_user_id=instagram_raw.get("ig_user_id", ""),
        access_token=instagram_raw.get("access_token", ""),
        api_version=instagram_raw.get("api_version", InstagramConfig.api_version),
        graph_base=instagram_raw.get("graph_base", InstagramConfig.graph_base).rstrip("/"),
    )

    safety = SafetyConfig(
        disclosure=safety_raw.get("disclosure", SafetyConfig.disclosure),
        disclosure_hashtags=_tuple(
            safety_raw.get("disclosure_hashtags"), "safety.disclosure_hashtags"
        )
        or SafetyConfig.disclosure_hashtags,
        banned_topics=_tuple(safety_raw.get("banned_topics"), "safety.banned_topics"),
        require_disclosure=bool(
            safety_raw.get("require_disclosure", SafetyConfig.require_disclosure)
        ),
    )

    state_path = Path(raw.get("state_path", "out/state.json"))
    if not state_path.is_absolute():
        state_path = source.parent / state_path
    state_path = state_path.resolve()

    return Config(
        persona=_persona(raw.get("persona") or {}),
        schedule=schedule,
        image=image,
        caption=caption,
        storage=storage,
        instagram=instagram,
        safety=safety,
        state_path=state_path,
        source=source,
        missing_env=tuple(sorted(missing)),
    )
