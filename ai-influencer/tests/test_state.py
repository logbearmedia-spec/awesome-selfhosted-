from __future__ import annotations

from pathlib import Path

import pytest

from influencer.state import PostRecord, State


def _post(date: str, slot: int = 0, pillar: str = "mornings") -> PostRecord:
    return PostRecord(
        date=date, slot=slot, pillar=pillar, scene=f"scene-{date}", theme="t", caption="c"
    )


def test_round_trip(tmp_path: Path):
    state = State(path=tmp_path / "state.json")
    state.record(_post("2026-03-01"))
    state.save()

    reloaded = State.load(tmp_path / "state.json")
    assert len(reloaded.posts) == 1
    assert reloaded.posts[0].date == "2026-03-01"


def test_missing_file_starts_empty(tmp_path: Path):
    assert State.load(tmp_path / "absent.json").posts == []


def test_recording_a_slot_twice_replaces_it(tmp_path: Path):
    state = State(path=tmp_path / "state.json")
    state.record(_post("2026-03-01", pillar="mornings"))
    state.record(_post("2026-03-01", pillar="city"))
    assert len(state.posts) == 1
    assert state.posts[0].pillar == "city"


def test_posts_stay_ordered(tmp_path: Path):
    state = State(path=tmp_path / "state.json")
    for day in ("2026-03-03", "2026-03-01", "2026-03-02"):
        state.record(_post(day))
    assert [p.date for p in state.posts] == ["2026-03-01", "2026-03-02", "2026-03-03"]


def test_recent_returns_newest_last(tmp_path: Path):
    state = State(path=tmp_path / "state.json")
    for day in ("2026-03-01", "2026-03-02", "2026-03-03"):
        state.record(_post(day, pillar=day))
    assert state.recent_pillars(2) == ["2026-03-02", "2026-03-03"]
    assert state.recent(0) == []


def test_has_slot_and_posts_on(tmp_path: Path):
    state = State(path=tmp_path / "state.json")
    state.record(_post("2026-03-01", slot=0))
    assert state.has_slot("2026-03-01", 0)
    assert not state.has_slot("2026-03-01", 1)
    assert len(state.posts_on("2026-03-01")) == 1


def test_corrupt_file_is_reported(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        State.load(path)


def test_future_schema_is_refused(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text('{"version": 999, "posts": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="newer than this code"):
        State.load(path)


def test_save_leaves_no_temp_files(tmp_path: Path):
    state = State(path=tmp_path / "state.json")
    state.record(_post("2026-03-01"))
    state.save()
    state.save()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]
