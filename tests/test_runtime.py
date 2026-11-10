from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pytest

import runtime

# Token-shaped for runtime.looks_like_token (three segments, base64url alphabet,
# 50 to 200 chars) but deliberately not shaped like a real Discord token, so
# GitHub push protection does not flag the test file.
GOOD_TOKEN = ".".join(["A" * 26, "b" * 8, "c" * 40])


@pytest.mark.parametrize(
    "value",
    [
        "",
        "REPLACE_ME",
        "REPLACE_WITH_REAL_TOKEN",
        "short.two.dots",
        "https://discord.com/api/webhooks/123/abc",  # a webhook URL pasted by mistake
        GOOD_TOKEN + "\n",  # read_token strips, but looks_like_token must reject raw newline
        GOOD_TOKEN.replace(".", "", 1),  # only one dot
        "x" * 201,
    ],
)
def test_looks_like_token_rejects(value: str) -> None:
    assert not runtime.looks_like_token(value)


def test_looks_like_token_accepts_shape() -> None:
    assert runtime.looks_like_token(GOOD_TOKEN)


def test_read_token_prefers_file_and_strips(tmp_path: Path) -> None:
    f = tmp_path / "token"
    f.write_text(GOOD_TOKEN + "\n", encoding="utf-8")
    env = {"DISCORD_TOKEN_FILE": str(f), "DISCORD_TOKEN": "REPLACE_ME"}
    assert runtime.read_token(env) == GOOD_TOKEN


def test_read_token_placeholder_file_is_none(tmp_path: Path) -> None:
    f = tmp_path / "token"
    f.write_text("REPLACE_ME", encoding="utf-8")
    assert runtime.read_token({"DISCORD_TOKEN_FILE": str(f)}) is None


def test_read_token_missing_file_is_none(tmp_path: Path) -> None:
    assert runtime.read_token({"DISCORD_TOKEN_FILE": str(tmp_path / "nope")}) is None


def test_read_token_env_fallback() -> None:
    assert runtime.read_token({"DISCORD_TOKEN": GOOD_TOKEN}) == GOOD_TOKEN
    assert runtime.read_token({}) is None


def test_wait_for_token_polls_until_file_becomes_real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "token"
    f.write_text("REPLACE_ME", encoding="utf-8")
    monkeypatch.setenv("DISCORD_TOKEN_FILE", str(f))
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    sleeps: list[float] = []
    waits: list[int] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            f.write_text(GOOD_TOKEN, encoding="utf-8")

    token = runtime.wait_for_token(sleep=fake_sleep, on_wait=lambda: waits.append(1), poll_seconds=7)
    assert token == GOOD_TOKEN
    assert sleeps == [7, 7]
    assert len(waits) == 2, "heartbeat hook must run on every wait so liveness passes while idle"


def test_wait_for_token_gives_up_after_max_polls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "token"
    f.write_text("REPLACE_ME", encoding="utf-8")
    monkeypatch.setenv("DISCORD_TOKEN_FILE", str(f))
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    assert runtime.wait_for_token(sleep=lambda _s: None, poll_seconds=0, max_polls=3) is None


def test_seed_data_copies_once_and_never_overwrites(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled.json"
    bundled.write_text('{"a": 1}', encoding="utf-8")
    target = tmp_path / "data" / "activities.json"

    assert runtime.seed_data(bundled, target) is True
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
    assert not list((tmp_path / "data").glob("*.seed.tmp"))

    target.write_text('{"edited": true}', encoding="utf-8")
    assert runtime.seed_data(bundled, target) is False
    assert json.loads(target.read_text(encoding="utf-8")) == {"edited": True}


def test_seed_data_noop_when_same_path(tmp_path: Path) -> None:
    f = tmp_path / "activities.json"
    f.write_text("{}", encoding="utf-8")
    assert runtime.seed_data(f, f) is False


def test_heartbeat_touch_and_freshness(tmp_path: Path) -> None:
    hb = tmp_path / "deep" / "heartbeat"
    runtime.touch_heartbeat(hb)
    assert hb.exists()
    now = time.time()
    assert runtime.heartbeat_fresh(hb, 300, now=now)
    assert not runtime.heartbeat_fresh(hb, 300, now=now + 301)
    assert not runtime.heartbeat_fresh(tmp_path / "missing", 300)


def test_touch_heartbeat_none_is_noop() -> None:
    runtime.touch_heartbeat(None)


def test_json_formatter_escapes_quotes() -> None:
    record = logging.LogRecord("x", logging.INFO, "f", 1, 'he said "hi"', None, None)
    line = runtime.JsonFormatter().format(record)
    parsed = json.loads(line)
    assert parsed["msg"] == 'he said "hi"'
    assert parsed["level"] == "INFO"
