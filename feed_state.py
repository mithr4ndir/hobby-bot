"""Persistent bot state: feed on/off schedule plus a short memory of recent
picks so consecutive batches do not repeat. Small JSON file, atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Final

from paths import DATA_DIR

STATE_FILE: Final[Path] = DATA_DIR / "feed_state.json"

WEEKDAYS: Final[tuple[str, ...]] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

DEFAULT_WEEKDAY: Final[str] = "monday"
DEFAULT_HOUR: Final[int] = 9
DEFAULT_COUNT: Final[int] = 3

# How many recently shown names to remember. Three batches of three.
RECENT_KEEP: Final[int] = 9


@dataclass(frozen=True, slots=True)
class FeedState:
    enabled: bool = False
    channel_id: int | None = None
    weekday: str = DEFAULT_WEEKDAY
    hour: int = DEFAULT_HOUR
    count: int = DEFAULT_COUNT
    last_posted: str | None = None  # ISO date, so one post per day max
    recent: tuple[str, ...] = ()  # casefolded names, oldest first

    def validate(self) -> FeedState:
        if self.weekday not in WEEKDAYS:
            raise ValueError(f"weekday must be one of {WEEKDAYS}")
        if not 0 <= self.hour <= 23:
            raise ValueError("hour must be 0..23")
        if not 1 <= self.count <= 5:
            raise ValueError("count must be 1..5")
        if self.channel_id is not None and self.channel_id <= 0:
            raise ValueError("channel_id must be a positive integer")
        if self.last_posted is not None:
            date.fromisoformat(self.last_posted)
        if len(self.recent) > RECENT_KEEP or not all(isinstance(n, str) for n in self.recent):
            raise ValueError("recent must be a short list of strings")
        return self


def load(path: Path = STATE_FILE) -> FeedState:
    """Load state, returning defaults when the file is absent or unreadable."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return FeedState()
    except (OSError, json.JSONDecodeError):
        # A corrupt state file should not take the bot down. Start fresh and
        # let the operator re-enable the feed.
        return FeedState()
    if not isinstance(raw, dict):
        return FeedState()
    try:
        recent_raw = raw.get("recent", [])
        if not isinstance(recent_raw, list):
            raise ValueError("recent must be a list")
        return FeedState(
            enabled=bool(raw.get("enabled", False)),
            channel_id=int(raw["channel_id"]) if raw.get("channel_id") is not None else None,
            weekday=str(raw.get("weekday", DEFAULT_WEEKDAY)),
            hour=int(raw.get("hour", DEFAULT_HOUR)),
            count=int(raw.get("count", DEFAULT_COUNT)),
            last_posted=str(raw["last_posted"]) if raw.get("last_posted") else None,
            recent=tuple(str(n) for n in recent_raw[-RECENT_KEEP:]),
        ).validate()
    except (ValueError, TypeError):
        return FeedState()


def save(state: FeedState, path: Path = STATE_FILE) -> None:
    """Write atomically so a crash mid-write cannot leave a half file."""
    state.validate()
    payload = json.dumps(asdict(state), indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".feed_state_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def is_due(state: FeedState, now: datetime, has_webhook: bool = False) -> bool:
    """True when the feed is on, there is somewhere to post (a channel chosen
    with /feed on, or a configured webhook), it is the configured weekday and
    hour, and we have not already posted today."""
    if not state.enabled:
        return False
    if state.channel_id is None and not has_webhook:
        return False
    if WEEKDAYS[now.weekday()] != state.weekday:
        return False
    if now.hour != state.hour:
        return False
    return state.last_posted != now.date().isoformat()


def initial(has_webhook: bool, path: Path = STATE_FILE) -> FeedState:
    """State at startup. With no state file yet and a webhook configured, the
    feed starts enabled on the defaults (Monday 09:00, 3 picks) so a fresh
    deployment posts without anyone running /feed on. /feed off still sticks,
    because it writes the file."""
    if path.exists():
        return load(path)
    state = FeedState(enabled=has_webhook)
    if has_webhook:
        save(state, path)
    return state


def mark_posted(state: FeedState, now: datetime) -> FeedState:
    return replace(state, last_posted=now.date().isoformat())


def remember(state: FeedState, names: list[str], keep: int = RECENT_KEEP) -> FeedState:
    """Append shown names, dropping duplicates and keeping only the newest `keep`."""
    merged = [n for n in state.recent if n.casefold() not in {x.casefold() for x in names}]
    merged.extend(n.casefold() for n in names)
    return replace(state, recent=tuple(merged[-keep:]))


def recent_set(state: FeedState) -> frozenset[str]:
    return frozenset(state.recent)
