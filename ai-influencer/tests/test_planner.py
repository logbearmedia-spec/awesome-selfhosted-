from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from influencer import planner
from influencer.state import PostRecord, State


def _state(tmp_path):
    return State(path=tmp_path / "state.json")


def test_plan_is_deterministic(cfg, tmp_path):
    day = date(2026, 3, 4)
    first = planner.plan_for(cfg, _state(tmp_path), day, 0)
    second = planner.plan_for(cfg, _state(tmp_path), day, 0)
    assert first == second


def test_different_days_differ(cfg, tmp_path):
    state = _state(tmp_path)
    a = planner.plan_for(cfg, state, date(2026, 3, 4), 0)
    b = planner.plan_for(cfg, state, date(2026, 3, 5), 0)
    assert (a.pillar, a.scene) != (b.pillar, b.scene)


def test_prompt_contains_locked_appearance(cfg, tmp_path):
    plan = planner.plan_for(cfg, _state(tmp_path), date(2026, 3, 4), 0)
    assert "long dark curly hair" in plan.image_prompt
    assert plan.scene.split(",")[0] in plan.image_prompt


def test_recent_pillar_is_avoided(cfg, tmp_path):
    state = _state(tmp_path)
    baseline = planner.plan_for(cfg, state, date(2026, 3, 4), 0)
    state.record(
        PostRecord(
            date="2026-03-03",
            slot=0,
            pillar=baseline.pillar,
            scene="unrelated",
            theme="t",
            caption="",
        )
    )
    after = planner.plan_for(cfg, state, date(2026, 3, 4), 0)
    assert after.pillar != baseline.pillar


def test_scene_rotation_across_a_month(cfg, tmp_path):
    plans = planner.plan_range(cfg, _state(tmp_path), date(2026, 3, 1), 30)
    scenes = [p.scene for p in plans]
    # No scene repeats back to back, and the month draws on a wide pool.
    assert all(a != b for a, b in zip(scenes, scenes[1:]))
    assert len(set(scenes)) >= 20


def test_hashtags_always_carry_the_ai_label(cfg, tmp_path):
    for plan in planner.plan_range(cfg, _state(tmp_path), date(2026, 3, 1), 14):
        assert set(cfg.safety.disclosure_hashtags) <= set(plan.hashtags)
        assert len(plan.hashtags) <= cfg.caption.max_hashtags


def test_out_of_range_slot(cfg, tmp_path):
    with pytest.raises(planner.PlannerError, match="out of range"):
        planner.plan_for(cfg, _state(tmp_path), date(2026, 3, 4), 7)


def test_slots_due_within_grace_window(cfg):
    tz = ZoneInfo(cfg.schedule.timezone)
    scheduled = datetime(2026, 3, 4, 18, 30, tzinfo=tz)

    assert planner.slots_for(cfg, scheduled) == [0]
    assert planner.slots_for(cfg, scheduled + timedelta(minutes=45)) == [0]
    # Past the grace window, and before the slot, nothing is due.
    assert planner.slots_for(cfg, scheduled + timedelta(minutes=120)) == []
    assert planner.slots_for(cfg, scheduled - timedelta(minutes=5)) == []
