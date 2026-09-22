"""Decides what the account posts, and when.

Planning is deterministic: the same date, slot and persona seed always produce
the same plan. That makes the whole pipeline reproducible and lets you preview
a week of content before anything goes live. Variety comes from rotation
against the posted history rather than from randomness alone, so the account
does not land on the same pillar or scene twice in a row.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Config, Pillar
from .state import State


@dataclass(frozen=True)
class PostPlan:
    date: str
    slot: int
    pillar: str
    theme: str
    scene: str
    wardrobe: str
    image_prompt: str
    negative_prompt: str
    caption_brief: str
    hashtags: tuple[str, ...]
    seed: int


class PlannerError(Exception):
    """Raised when no valid plan can be produced."""


def _rng(persona_seed: int, date: str, slot: int) -> random.Random:
    digest = hashlib.sha256(f"{persona_seed}:{date}:{slot}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _weighted_choice(rng: random.Random, pillars: tuple[Pillar, ...]) -> Pillar:
    total = sum(p.weight for p in pillars)
    cut = rng.uniform(0, total)
    upto = 0.0
    for pillar in pillars:
        upto += pillar.weight
        if cut <= upto:
            return pillar
    return pillars[-1]


def _pick_pillar(rng: random.Random, cfg: Config, state: State) -> Pillar:
    pillars = cfg.persona.pillars
    # Avoid repeating the pillars used most recently, but never exclude so many
    # that nothing is left to choose from.
    lookback = min(len(pillars) - 1, 2)
    recent = set(state.recent_pillars(lookback))
    candidates = tuple(p for p in pillars if p.key not in recent) or pillars
    return _weighted_choice(rng, candidates)


def _pick_scene(rng: random.Random, pillar: Pillar, state: State) -> str:
    lookback = max(len(pillar.scenes) - 1, 0)
    recent = set(state.recent_scenes(min(lookback, 20)))
    candidates = tuple(s for s in pillar.scenes if s not in recent) or pillar.scenes
    return rng.choice(candidates)


def slots_for(cfg: Config, when: datetime) -> list[int]:
    """Indexes of the scheduled slots that are due at `when`, most recent first.

    A slot is due once its configured time has passed on that day, and stays due
    for `schedule.slot_grace_minutes` afterwards, so a scheduler that fires a
    little late still posts rather than silently skipping the day.
    """
    tz = ZoneInfo(cfg.schedule.timezone)
    local = when.astimezone(tz)
    due: list[int] = []
    for index, raw in enumerate(cfg.schedule.times):
        try:
            hour, minute = (int(part) for part in raw.split(":", 1))
        except ValueError as exc:
            raise PlannerError(
                f"schedule.times[{index}] is not HH:MM: {raw!r}"
            ) from exc
        slot_time = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        age = local - slot_time
        if timedelta(0) <= age <= timedelta(minutes=cfg.schedule.slot_grace_minutes):
            due.append(index)
    return sorted(due, reverse=True)


def build_prompt(cfg: Config, scene: str, wardrobe: str) -> str:
    parts = [
        cfg.persona.appearance.strip().rstrip("."),
        scene.strip().rstrip("."),
    ]
    if wardrobe:
        parts.append(f"wearing {wardrobe.strip().rstrip('.')}")
    if cfg.persona.style_note:
        parts.append(cfg.persona.style_note.strip().rstrip("."))
    if cfg.image.quality_suffix:
        parts.append(cfg.image.quality_suffix.strip().rstrip("."))
    return ", ".join(part for part in parts if part) + "."


def plan_for(cfg: Config, state: State, day: Date, slot: int) -> PostPlan:
    """Build the plan for one specific slot on one specific day."""
    if slot < 0 or slot >= len(cfg.schedule.times):
        raise PlannerError(
            f"slot {slot} is out of range; schedule.times defines "
            f"{len(cfg.schedule.times)} slot(s)"
        )

    date_str = day.isoformat()
    rng = _rng(cfg.persona.seed, date_str, slot)

    pillar = _pick_pillar(rng, cfg, state)
    scene = _pick_scene(rng, pillar, state)
    theme = rng.choice(pillar.themes) if pillar.themes else pillar.key
    wardrobe = rng.choice(cfg.persona.wardrobe) if cfg.persona.wardrobe else ""

    hashtags: list[str] = []
    for tag in (*pillar.hashtags, *cfg.persona.base_hashtags):
        normalised = tag if tag.startswith("#") else f"#{tag}"
        if normalised not in hashtags:
            hashtags.append(normalised)
    rng.shuffle(hashtags)
    room = max(cfg.caption.max_hashtags - len(cfg.safety.disclosure_hashtags), 0)
    hashtags = hashtags[:room]
    for tag in cfg.safety.disclosure_hashtags:
        if tag not in hashtags:
            hashtags.append(tag)

    caption_brief = (
        f"Pillar: {pillar.key}. Theme: {theme}. "
        f"What the photo shows: {scene}."
    )

    return PostPlan(
        date=date_str,
        slot=slot,
        pillar=pillar.key,
        theme=theme,
        scene=scene,
        wardrobe=wardrobe,
        image_prompt=build_prompt(cfg, scene, wardrobe),
        negative_prompt=cfg.image.negative,
        caption_brief=caption_brief,
        hashtags=tuple(hashtags),
        seed=cfg.persona.seed,
    )


def plan_range(cfg: Config, state: State, start: Date, days: int) -> list[PostPlan]:
    """Plan every slot across `days` days starting at `start`.

    Each plan is folded into a scratch copy of the state as it is produced, so
    the rotation looks ahead the same way a real run would.
    """
    from .state import PostRecord

    scratch = State(path=state.path, posts=list(state.posts))
    plans: list[PostPlan] = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        for slot in range(len(cfg.schedule.times)):
            plan = plan_for(cfg, scratch, day, slot)
            plans.append(plan)
            scratch.record(
                PostRecord(
                    date=plan.date,
                    slot=plan.slot,
                    pillar=plan.pillar,
                    scene=plan.scene,
                    theme=plan.theme,
                    caption="",
                )
            )
    return plans
