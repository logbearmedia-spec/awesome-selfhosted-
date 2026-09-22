"""The end-to-end run: plan, render, caption, publish, record."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timezone
from pathlib import Path

from . import captions, imagegen, safety, storage
from .config import Config
from .planner import PostPlan, plan_for
from .publisher import InstagramPublisher
from .state import PostRecord, State


@dataclass(frozen=True)
class RunResult:
    status: str  # published | dry-run | skipped
    reason: str = ""
    plan: PostPlan | None = None
    record: PostRecord | None = None
    media_url: str = ""
    local_path: Path | None = None
    caption: captions.Caption | None = None


def _slug(text: str, limit: int = 40) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:limit] or "post"


def run_slot(
    cfg: Config,
    state: State,
    day: Date,
    slot: int,
    *,
    dry_run: bool = False,
    force: bool = False,
    skip_publish: bool = False,
) -> RunResult:
    """Produce and publish the post for one slot.

    `dry_run` renders and captions but never calls Instagram. `skip_publish`
    does the same but is meant for previewing a real render. `force` re-runs a
    slot that has already been posted.
    """
    plan = plan_for(cfg, state, day, slot)

    if state.has_slot(plan.date, plan.slot) and not force:
        return RunResult(
            status="skipped",
            reason=f"{plan.date} slot {plan.slot} has already been posted",
            plan=plan,
        )

    live = not (dry_run or skip_publish)
    if live:
        published_today = [p for p in state.posts_on(plan.date) if not p.dry_run]
        safety.check_rate_limit(len(published_today), cfg).raise_for_problems()

    safety.check_image_prompt(plan.image_prompt, cfg).raise_for_problems()

    request = imagegen.ImageRequest(
        prompt=plan.image_prompt,
        negative_prompt=plan.negative_prompt,
        width=cfg.image.width,
        height=cfg.image.height,
        seed=plan.seed,
        reference_image=imagegen.reference_path(cfg),
    )
    image = imagegen.generate(request, cfg)

    name = f"{plan.date}-{plan.slot}-{_slug(plan.theme)}{image.extension}"
    stored = storage.store(name, image.data, image.content_type, cfg)

    caption = captions.write(plan, cfg)

    publisher = InstagramPublisher(cfg, dry_run=not live)
    result = publisher.publish_image(stored.url, caption.text, caption.alt_text)
    if live and cfg.caption.hashtags_in_first_comment and plan.hashtags:
        publisher.comment(result.media_id, " ".join(plan.hashtags))

    record = PostRecord(
        date=plan.date,
        slot=plan.slot,
        pillar=plan.pillar,
        scene=plan.scene,
        theme=plan.theme,
        caption=caption.text,
        media_id=result.media_id,
        permalink=result.permalink,
        media_url=stored.url,
        published_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        dry_run=not live,
    )
    state.record(record)
    state.save()

    return RunResult(
        status="published" if live else "dry-run",
        plan=plan,
        record=record,
        media_url=stored.url,
        local_path=stored.local_path,
        caption=caption,
    )
