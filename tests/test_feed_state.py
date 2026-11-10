from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

import feed_state as fs


def test_load_missing_file_returns_defaults(tmp_path: Path) -> None:
    state = fs.load(tmp_path / "nope.json")
    assert state == fs.FeedState()
    assert state.enabled is False
    assert state.recent == ()


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    original = fs.FeedState(
        enabled=True,
        channel_id=123,
        weekday="friday",
        hour=18,
        count=4,
        last_posted="2026-11-06",
        recent=("a", "b"),
    )
    fs.save(original, path)
    assert fs.load(path) == original
    assert not list(tmp_path.glob(".feed_state_*.tmp")), "temp file left behind"


def test_corrupt_file_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    assert fs.load(path) == fs.FeedState()


def test_invalid_values_in_file_return_defaults(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"enabled": true, "channel_id": 5, "weekday": "funday", "hour": 9}', encoding="utf-8")
    assert fs.load(path) == fs.FeedState()


def test_oversized_recent_in_file_is_trimmed(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    names = [f"n{i}" for i in range(50)]
    path.write_text(json.dumps({"recent": names}), encoding="utf-8")
    state = fs.load(path)
    assert len(state.recent) == fs.RECENT_KEEP
    assert state.recent[-1] == "n49"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"weekday": "funday"},
        {"hour": 24},
        {"hour": -1},
        {"count": 0},
        {"count": 6},
        {"channel_id": 0},
        {"last_posted": "yesterday"},
        {"recent": tuple(str(i) for i in range(fs.RECENT_KEEP + 1))},
    ],
)
def test_save_rejects_invalid_state(tmp_path: Path, kwargs: dict) -> None:
    with pytest.raises(ValueError):
        fs.save(fs.FeedState(**kwargs), tmp_path / "state.json")


MONDAY_9 = datetime(2026, 11, 9, 9, 15)  # 2026-11-09 is a Monday


def test_is_due_true_on_matching_day_and_hour() -> None:
    assert fs.is_due(fs.FeedState(enabled=True, channel_id=1, weekday="monday", hour=9), MONDAY_9)


def test_is_due_false_when_disabled() -> None:
    assert not fs.is_due(fs.FeedState(enabled=False, channel_id=1, weekday="monday", hour=9), MONDAY_9)


def test_is_due_false_without_channel() -> None:
    assert not fs.is_due(fs.FeedState(enabled=True, channel_id=None, weekday="monday", hour=9), MONDAY_9)


def test_is_due_false_on_wrong_day() -> None:
    assert not fs.is_due(fs.FeedState(enabled=True, channel_id=1, weekday="tuesday", hour=9), MONDAY_9)


def test_is_due_false_on_wrong_hour() -> None:
    assert not fs.is_due(fs.FeedState(enabled=True, channel_id=1, weekday="monday", hour=10), MONDAY_9)


def test_is_due_false_after_posting_today() -> None:
    state = fs.FeedState(enabled=True, channel_id=1, weekday="monday", hour=9)
    posted = fs.mark_posted(state, MONDAY_9)
    assert posted.last_posted == "2026-11-09"
    assert not fs.is_due(posted, MONDAY_9.replace(minute=45))


def test_is_due_true_again_next_week() -> None:
    state = fs.FeedState(enabled=True, channel_id=1, weekday="monday", hour=9, last_posted="2026-11-09")
    assert fs.is_due(state, datetime(2026, 11, 16, 9, 5))


def test_remember_keeps_newest_and_dedupes() -> None:
    state = fs.FeedState()
    state = fs.remember(state, ["A", "B", "C"])
    state = fs.remember(state, ["D", "E", "F"])
    state = fs.remember(state, ["G", "H", "I"])
    assert state.recent == tuple("abcdefghi")
    state = fs.remember(state, ["J", "a"])  # 'a' moves to the newest end, oldest drops
    assert state.recent == ("c", "d", "e", "f", "g", "h", "i", "j", "a")
    assert len(state.recent) == fs.RECENT_KEEP
    assert fs.recent_set(state) == frozenset("cdefghija")
