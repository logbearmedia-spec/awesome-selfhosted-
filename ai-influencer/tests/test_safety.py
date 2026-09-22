from __future__ import annotations

import pytest

from influencer import safety


def test_disclosure_is_appended_when_absent(cfg):
    out = safety.apply_disclosure("a quiet morning", cfg)
    assert cfg.safety.disclosure in out


def test_disclosure_is_not_duplicated(cfg):
    once = safety.apply_disclosure("a quiet morning", cfg)
    twice = safety.apply_disclosure(once, cfg)
    assert once == twice


def test_hashtag_alone_counts_as_disclosure(cfg):
    assert safety.has_disclosure("morning light #aigenerated", cfg)


def test_caption_without_disclosure_is_rejected(cfg):
    result = safety.check_caption("just a caption", (), cfg)
    assert not result.ok
    assert any("disclosure" in p for p in result.problems)
    with pytest.raises(safety.SafetyError):
        result.raise_for_problems()


def test_caption_length_limit(cfg):
    long = "x" * 2300 + " #aigenerated"
    result = safety.check_caption(long, (), cfg)
    assert not result.ok
    assert any("over Instagram's" in p for p in result.problems)


def test_too_many_hashtags(cfg):
    tags = tuple(f"#tag{i}" for i in range(31))
    result = safety.check_caption("hi #aigenerated", tags, cfg)
    assert any("limit of 30" in p for p in result.problems)


def test_malformed_hashtags(cfg):
    result = safety.check_caption("hi #aigenerated", ("#good", "bad", "#also bad"), cfg)
    assert any("malformed" in p for p in result.problems)


def test_banned_topic_in_caption(cfg):
    result = safety.check_caption("my weight loss journey #aigenerated", (), cfg)
    assert any("banned topic" in p for p in result.problems)


def test_real_person_prompt_is_refused(cfg):
    result = safety.check_image_prompt(
        "a woman who looks like Taylor Swift on a balcony", cfg
    )
    assert not result.ok
    assert any("real person" in p for p in result.problems)


def test_ordinary_prompt_passes(cfg):
    result = safety.check_image_prompt(
        "a woman in her late twenties on a balcony at golden hour", cfg
    )
    assert result.ok


def test_rate_limit(cfg):
    assert safety.check_rate_limit(0, cfg).ok
    assert not safety.check_rate_limit(cfg.schedule.max_posts_per_day, cfg).ok
