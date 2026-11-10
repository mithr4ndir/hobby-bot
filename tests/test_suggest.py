from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import suggest

TODAY = date(2026, 11, 9)


def _act(name: str, category: str = "dance", **kw) -> suggest.Activity:
    base = dict(
        name=name,
        category=category,
        when="w",
        where="x",
        cost="c",
        notes="n",
        url="https://example.com",
        verified="2026-11-09",
        expires=None,
    )
    base.update(kw)
    return suggest.Activity(**base)


@pytest.fixture(scope="module")
def data() -> tuple[suggest.Plan, tuple[suggest.Activity, ...]]:
    return suggest.load_data()


# ---- loading ----------------------------------------------------------------


def test_data_file_loads_and_is_valid(data) -> None:
    plan, activities = data
    assert plan.title
    assert len(plan.anchors) == 2
    assert len(activities) >= 15
    assert all(a.category in suggest.CATEGORIES for a in activities)
    assert all(a.verified for a in activities), "every shipped entry carries a verified date"


def test_every_category_has_at_least_one_activity(data) -> None:
    _, activities = data
    assert {a.category for a in activities} == set(suggest.CATEGORIES)


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "activities.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _ok_item(**kw) -> dict:
    item = {
        "name": "Thing",
        "category": "dance",
        "when": "x",
        "where": "x",
        "cost": "x",
        "notes": "x",
        "url": "https://example.com",
    }
    item.update(kw)
    return item


PLAN = {"plan": {"title": "x", "model": [], "rules": [], "anchors": []}}


@pytest.mark.parametrize(
    "item",
    [
        _ok_item(category="running"),
        {"name": "No where", "category": "dance", "when": "x"},
        _ok_item(url="http://insecure.example.com"),
        _ok_item(url="https://exa mple.com"),
        _ok_item(verified="last tuesday"),
        _ok_item(expires="2026-13-40"),
        _ok_item(name="x" * (suggest.MAX_NAME + 1)),
    ],
)
def test_load_data_rejects_bad_items(tmp_path: Path, item: dict) -> None:
    path = _write(tmp_path, {**PLAN, "activities": [item]})
    with pytest.raises(suggest.DataError):
        suggest.load_data(path)


def test_load_data_rejects_duplicate_names(tmp_path: Path) -> None:
    path = _write(tmp_path, {**PLAN, "activities": [_ok_item(name="Dup"), _ok_item(name="dup")]})
    with pytest.raises(suggest.DataError):
        suggest.load_data(path)


def test_load_data_rejects_empty_list(tmp_path: Path) -> None:
    path = _write(tmp_path, {**PLAN, "activities": []})
    with pytest.raises(suggest.DataError):
        suggest.load_data(path)


def test_save_round_trip(tmp_path: Path, data) -> None:
    plan, activities = data
    path = tmp_path / "activities.json"
    suggest.save_data(plan, activities, path)
    plan2, activities2 = suggest.load_data(path)
    assert plan2 == plan
    assert activities2 == activities
    assert not list(tmp_path.glob(".activities_*.tmp"))


# ---- freshness --------------------------------------------------------------


def test_stale_when_never_verified() -> None:
    assert suggest.is_stale(_act("a", verified=None), TODAY)


def test_stale_after_window() -> None:
    old = (TODAY - timedelta(days=suggest.STALE_AFTER_DAYS + 1)).isoformat()
    edge = (TODAY - timedelta(days=suggest.STALE_AFTER_DAYS)).isoformat()
    assert suggest.is_stale(_act("a", verified=old), TODAY)
    assert not suggest.is_stale(_act("a", verified=edge), TODAY)
    assert not suggest.is_stale(_act("a", verified=TODAY.isoformat()), TODAY)


def test_expired_entries_are_hidden_from_picks() -> None:
    acts = (
        _act("live"),
        _act("gone", expires="2026-11-08"),
        _act("today", expires="2026-11-09"),
    )
    live = suggest.active(acts, TODAY)
    assert {a.name for a in live} == {"live", "today"}
    for _ in range(20):
        assert all(p.name != "gone" for p in suggest.pick(acts, 2, today=TODAY))


def test_stale_report_splits_correctly() -> None:
    acts = (
        _act("fresh"),
        _act("old", verified="2020-01-01"),
        _act("gone", expires="2020-01-01"),
    )
    stale, expired = suggest.stale_report(acts, TODAY)
    assert [a.name for a in stale] == ["old"]
    assert [a.name for a in expired] == ["gone"]


# ---- catalog edits ----------------------------------------------------------


def test_add_remove_verify() -> None:
    acts = (_act("a"), _act("b"))
    acts = suggest.add_activity(acts, _act("c", verified=None))
    assert suggest.find(acts, "C") is not None
    with pytest.raises(suggest.DataError):
        suggest.add_activity(acts, _act("A"))

    acts = suggest.mark_verified(acts, "c", TODAY)
    assert suggest.find(acts, "c").verified == "2026-11-09"
    with pytest.raises(suggest.DataError):
        suggest.mark_verified(acts, "zzz", TODAY)

    acts = suggest.remove_activity(acts, "B")
    assert suggest.find(acts, "b") is None
    with pytest.raises(suggest.DataError):
        suggest.remove_activity(acts, "nope")


def test_cannot_remove_last_activity() -> None:
    with pytest.raises(suggest.DataError):
        suggest.remove_activity((_act("only"),), "only")


def test_activity_from_dict_strips_and_validates() -> None:
    a = suggest.activity_from_dict(_ok_item(name="  Spaced  "))
    assert a.name == "Spaced"
    with pytest.raises(suggest.DataError):
        suggest.activity_from_dict(_ok_item(notes="x" * (suggest.MAX_NOTES + 1)))


# ---- picking ----------------------------------------------------------------


def test_pick_default_returns_three_distinct(data) -> None:
    _, activities = data
    picks = suggest.pick(activities)
    assert len(picks) == 3
    assert len({p.name for p in picks}) == 3


@pytest.mark.parametrize("count", [1, 2, 5])
def test_pick_respects_count(data, count: int) -> None:
    _, activities = data
    assert len(suggest.pick(activities, count)) == count


@pytest.mark.parametrize("count", [0, 6, -1, 100])
def test_pick_rejects_out_of_range_count(data, count: int) -> None:
    _, activities = data
    with pytest.raises(ValueError):
        suggest.pick(activities, count)
    with pytest.raises(ValueError):
        suggest.pick_spread(activities, count)


def test_pick_filters_by_category(data) -> None:
    _, activities = data
    picks = suggest.pick(activities, 3, "dance")
    assert picks
    assert all(p.category == "dance" for p in picks)


def test_pick_rejects_unknown_category(data) -> None:
    _, activities = data
    with pytest.raises(ValueError):
        suggest.pick(activities, 3, "running")  # back says no


def test_pick_returns_fewer_when_pool_is_small(data) -> None:
    _, activities = data
    pool = [a for a in activities if a.category == "social_deduction"]
    assert len(suggest.pick(activities, 5, "social_deduction")) == min(5, len(pool))


def test_pick_spread_uses_distinct_categories(data) -> None:
    _, activities = data
    for _ in range(20):
        picks = suggest.pick_spread(activities, 3)
        assert len(picks) == 3
        assert len({p.category for p in picks}) == 3


def test_pick_avoids_recent_names() -> None:
    acts = tuple(_act(f"a{i}") for i in range(6))
    recent = frozenset({"a0", "a1", "a2"})
    for _ in range(30):
        picks = suggest.pick(acts, 3, exclude=recent, today=TODAY)
        assert {p.name for p in picks} == {"a3", "a4", "a5"}


def test_pick_spread_avoids_recent_names(data) -> None:
    _, activities = data
    first = suggest.pick_spread(activities, 3, today=TODAY)
    recent = frozenset(p.name.casefold() for p in first)
    for _ in range(30):
        second = suggest.pick_spread(activities, 3, exclude=recent, today=TODAY)
        assert not {p.name for p in second} & {p.name for p in first}


def test_pick_falls_back_when_everything_is_recent() -> None:
    acts = tuple(_act(f"a{i}") for i in range(3))
    recent = frozenset({"a0", "a1", "a2"})
    picks = suggest.pick(acts, 3, exclude=recent, today=TODAY)
    assert len(picks) == 3, "a tiny pool must still fill the batch rather than return nothing"
