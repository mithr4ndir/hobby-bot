from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

import feed_state as fs
import runtime

MONDAY_9 = datetime(2026, 11, 9, 9, 15)  # 2026-11-09 is a Monday
HOOK = "https://discord.com/api/webhooks/123456789012345678/abcDEF_ghi-JKL"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "REPLACE_ME",
        "https://example.com/api/webhooks/1/x",
        "http://discord.com/api/webhooks/1/x",
        HOOK + " ",
        HOOK + "\n",
        "https://discord.com/api/webhooks/" + "a" * 300,
    ],
)
def test_looks_like_webhook_url_rejects(value: str) -> None:
    assert not runtime.looks_like_webhook_url(value)


def test_looks_like_webhook_url_accepts() -> None:
    assert runtime.looks_like_webhook_url(HOOK)
    assert runtime.looks_like_webhook_url(HOOK.replace("discord.com", "discordapp.com"))


def test_read_webhook_url_file_wins_and_strips(tmp_path: Path) -> None:
    f = tmp_path / "webhook_url"
    f.write_text(HOOK + "\n", encoding="utf-8")
    assert runtime.read_webhook_url({"HOBBY_BOT_WEBHOOK_FILE": str(f), "HOBBY_BOT_WEBHOOK_URL": "x"}) == HOOK


def test_read_webhook_url_placeholder_or_missing_is_none(tmp_path: Path) -> None:
    f = tmp_path / "webhook_url"
    f.write_text("REPLACE_ME", encoding="utf-8")
    assert runtime.read_webhook_url({"HOBBY_BOT_WEBHOOK_FILE": str(f)}) is None
    assert runtime.read_webhook_url({"HOBBY_BOT_WEBHOOK_FILE": str(tmp_path / "nope")}) is None
    assert runtime.read_webhook_url({}) is None
    assert runtime.read_webhook_url({"HOBBY_BOT_WEBHOOK_URL": HOOK}) == HOOK


def test_is_due_with_webhook_and_no_channel() -> None:
    state = fs.FeedState(enabled=True, channel_id=None, weekday="monday", hour=9)
    assert not fs.is_due(state, MONDAY_9)
    assert fs.is_due(state, MONDAY_9, has_webhook=True)


def test_is_due_webhook_still_respects_enabled_and_schedule() -> None:
    off = fs.FeedState(enabled=False, weekday="monday", hour=9)
    assert not fs.is_due(off, MONDAY_9, has_webhook=True)
    wrong_hour = fs.FeedState(enabled=True, weekday="monday", hour=10)
    assert not fs.is_due(wrong_hour, MONDAY_9, has_webhook=True)
    posted = fs.FeedState(enabled=True, weekday="monday", hour=9, last_posted="2026-11-09")
    assert not fs.is_due(posted, MONDAY_9, has_webhook=True)


def test_initial_defaults_on_with_webhook_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = fs.initial(has_webhook=True, path=path)
    assert state.enabled is True
    assert state.channel_id is None
    assert state.weekday == fs.DEFAULT_WEEKDAY and state.hour == fs.DEFAULT_HOUR and state.count == 3
    assert path.exists(), "the default-on choice must be written so /feed off can later stick"
    assert fs.load(path) == state


def test_initial_defaults_off_without_webhook(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = fs.initial(has_webhook=False, path=path)
    assert state.enabled is False
    assert not path.exists()


def test_initial_respects_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    fs.save(fs.FeedState(enabled=False, weekday="friday"), path)
    state = fs.initial(has_webhook=True, path=path)
    assert state.enabled is False, "/feed off written earlier must survive a restart"
    assert state.weekday == "friday"
