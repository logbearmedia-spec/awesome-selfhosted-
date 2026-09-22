from __future__ import annotations

import struct
from datetime import date
from pathlib import Path

import pytest

from influencer import captions, imagegen, pipeline, storage
from influencer.state import State


@pytest.fixture()
def dry_cfg(cfg):
    """The example config, forced onto the offline providers."""
    from dataclasses import replace

    return replace(
        cfg,
        caption=replace(cfg.caption, provider="template"),
        image=replace(cfg.image, provider="dryrun", options={}),
    )


def test_placeholder_image_is_a_valid_png(dry_cfg):
    request = imagegen.ImageRequest(
        prompt="a test scene", negative_prompt="", width=64, height=80, seed=1
    )
    image = imagegen.generate(request, dry_cfg)

    assert image.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert image.extension == ".png"
    width, height = struct.unpack(">II", image.data[16:24])
    assert (width, height) == (64, 80)


def test_placeholder_varies_with_prompt(dry_cfg):
    def render(prompt: str) -> bytes:
        return imagegen.generate(
            imagegen.ImageRequest(prompt=prompt, negative_prompt="", width=32, height=32, seed=1),
            dry_cfg,
        ).data

    assert render("a morning scene") != render("an evening scene")
    assert render("a morning scene") == render("a morning scene")


def test_local_storage_writes_and_builds_a_url(dry_cfg):
    stored = storage.store("x.png", b"bytes", "image/png", dry_cfg)
    assert stored.local_path is not None
    assert stored.local_path.read_bytes() == b"bytes"
    assert stored.url == "https://media.example.test/x.png"


def test_unknown_providers_are_reported(dry_cfg):
    from dataclasses import replace

    bad_image = replace(dry_cfg, image=replace(dry_cfg.image, provider="nope"))
    with pytest.raises(imagegen.ImageError, match="unknown image provider"):
        imagegen.generate(
            imagegen.ImageRequest("p", "", 8, 8, 1), bad_image
        )

    bad_store = replace(dry_cfg, storage=replace(dry_cfg.storage, provider="nope"))
    with pytest.raises(storage.StorageError, match="unknown storage provider"):
        storage.store("x.png", b"", "image/png", bad_store)


def test_dry_run_produces_a_complete_post(dry_cfg):
    state = State(path=dry_cfg.state_path)
    result = pipeline.run_slot(dry_cfg, state, date(2026, 3, 4), 0, dry_run=True)

    assert result.status == "dry-run"
    assert result.record is not None and result.record.dry_run
    assert result.local_path is not None and result.local_path.is_file()
    assert dry_cfg.safety.disclosure in result.caption.text
    assert result.caption.writer == "template"
    # The run is recorded, so the next one will not repeat the slot.
    assert State.load(dry_cfg.state_path).has_slot("2026-03-04", 0)


def test_a_posted_slot_is_skipped_then_forced(dry_cfg):
    state = State(path=dry_cfg.state_path)
    pipeline.run_slot(dry_cfg, state, date(2026, 3, 4), 0, dry_run=True)

    again = pipeline.run_slot(dry_cfg, state, date(2026, 3, 4), 0, dry_run=True)
    assert again.status == "skipped"
    assert "already been posted" in again.reason

    forced = pipeline.run_slot(dry_cfg, state, date(2026, 3, 4), 0, dry_run=True, force=True)
    assert forced.status == "dry-run"
    assert len(state.posts) == 1


def test_two_weeks_of_dry_runs_stay_varied(dry_cfg):
    state = State(path=dry_cfg.state_path)
    results = [
        pipeline.run_slot(dry_cfg, state, date(2026, 3, day), 0, dry_run=True)
        for day in range(1, 15)
    ]
    assert all(r.status == "dry-run" for r in results)
    scenes = [r.plan.scene for r in results]
    assert len(set(scenes)) == len(scenes)
    assert all(dry_cfg.safety.disclosure in r.caption.text for r in results)


def test_template_caption_respects_length_cap(dry_cfg):
    from influencer.planner import plan_for

    state = State(path=dry_cfg.state_path)
    plan = plan_for(dry_cfg, state, date(2026, 3, 4), 0)
    caption = captions.write(plan, dry_cfg)
    assert 0 < len(caption.text) <= dry_cfg.caption.max_chars + len(dry_cfg.safety.disclosure) + 2
    assert caption.alt_text


def test_publishing_refuses_a_file_url(cfg):
    from dataclasses import replace

    from influencer.publisher import InstagramPublisher, PublishError

    live = replace(cfg, storage=replace(cfg.storage, public_base_url=""))
    publisher = InstagramPublisher(live, dry_run=False)
    with pytest.raises(PublishError, match="fetches media over HTTP"):
        publisher.publish_image("file:///tmp/x.png", "caption #aigenerated")
