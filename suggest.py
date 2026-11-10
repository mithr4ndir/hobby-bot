"""Pure suggestion and catalog logic. No Discord imports so it is trivially testable."""

from __future__ import annotations

import json
import os
import random
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Final

from paths import DATA_DIR

DATA_FILE: Final[Path] = DATA_DIR / "activities.json"

CATEGORIES: Final[tuple[str, ...]] = (
    "board_games",
    "social_deduction",
    "ttrpg",
    "card_games",
    "dance",
    "biking",
    "community",
)

CATEGORY_LABELS: Final[dict[str, str]] = {
    "board_games": "Board games",
    "social_deduction": "Social deduction",
    "ttrpg": "Tabletop RPG",
    "card_games": "Card games",
    "dance": "Dance",
    "biking": "Biking",
    "community": "Nerd community",
}

MIN_COUNT: Final[int] = 1
MAX_COUNT: Final[int] = 5
DEFAULT_COUNT: Final[int] = 3

# An entry not re-verified within this window gets a "verify before you go" flag.
STALE_AFTER_DAYS: Final[int] = 90

# Field length limits. Discord embeds cap field values at 1024 chars, and these
# are also the server-side bound for anything added through a slash command.
MAX_NAME: Final[int] = 80
MAX_SHORT: Final[int] = 120
MAX_NOTES: Final[int] = 300
MAX_URL: Final[int] = 200

# Not security sensitive, but SystemRandom costs nothing.
_rng = random.SystemRandom()


@dataclass(frozen=True, slots=True)
class Activity:
    name: str
    category: str
    when: str
    where: str
    cost: str
    notes: str
    url: str
    verified: str | None = None  # ISO date of the last human check
    expires: str | None = None  # ISO date after which the entry is hidden


@dataclass(frozen=True, slots=True)
class Plan:
    title: str
    model: tuple[str, ...]
    rules: tuple[str, ...]
    anchors: tuple[dict[str, str], ...]


class DataError(ValueError):
    """Raised when activities.json is missing fields or malformed."""


def _require_str(obj: dict[str, object], key: str, context: str, max_len: int) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DataError(f"{context}: field '{key}' must be a non-empty string")
    if len(value) > max_len:
        raise DataError(f"{context}: field '{key}' longer than {max_len} chars")
    return value.strip()


def _optional_date(obj: dict[str, object], key: str, context: str) -> str | None:
    value = obj.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise DataError(f"{context}: field '{key}' must be an ISO date string")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise DataError(f"{context}: field '{key}' is not a valid ISO date") from exc


def validate_url(url: str) -> str:
    url = url.strip()
    if not url.startswith("https://") or len(url) > MAX_URL or any(c.isspace() for c in url):
        raise DataError("url must start with https:// and contain no spaces")
    return url


def activity_from_dict(item: dict[str, object], context: str = "activity") -> Activity:
    category = _require_str(item, "category", context, MAX_SHORT)
    if category not in CATEGORIES:
        raise DataError(f"{context}: unknown category '{category}'")
    return Activity(
        name=_require_str(item, "name", context, MAX_NAME),
        category=category,
        when=_require_str(item, "when", context, MAX_SHORT),
        where=_require_str(item, "where", context, MAX_SHORT),
        cost=_require_str(item, "cost", context, MAX_SHORT),
        notes=_require_str(item, "notes", context, MAX_NOTES),
        url=validate_url(_require_str(item, "url", context, MAX_URL)),
        verified=_optional_date(item, "verified", context),
        expires=_optional_date(item, "expires", context),
    )


def load_data(path: Path = DATA_FILE) -> tuple[Plan, tuple[Activity, ...]]:
    """Load and validate the data file. Fails loudly on bad data."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise DataError("top level must be an object")

    plan_raw = raw.get("plan")
    if not isinstance(plan_raw, dict):
        raise DataError("'plan' must be an object")
    plan = Plan(
        title=_require_str(plan_raw, "title", "plan", MAX_SHORT),
        model=tuple(str(x) for x in plan_raw.get("model", [])),
        rules=tuple(str(x) for x in plan_raw.get("rules", [])),
        anchors=tuple(
            {k: str(v) for k, v in a.items()} for a in plan_raw.get("anchors", [])
        ),
    )

    items_raw = raw.get("activities")
    if not isinstance(items_raw, list) or not items_raw:
        raise DataError("'activities' must be a non-empty list")

    activities: list[Activity] = []
    seen: set[str] = set()
    for index, item in enumerate(items_raw):
        if not isinstance(item, dict):
            raise DataError(f"activities[{index}] must be an object")
        activity = activity_from_dict(item, f"activities[{index}]")
        key = activity.name.casefold()
        if key in seen:
            raise DataError(f"activities[{index}]: duplicate name '{activity.name}'")
        seen.add(key)
        activities.append(activity)
    return plan, tuple(activities)


def save_data(plan: Plan, activities: tuple[Activity, ...], path: Path = DATA_FILE) -> None:
    """Atomic write so a crash cannot leave a half-written catalog."""
    payload = json.dumps(
        {
            "plan": {
                "title": plan.title,
                "model": list(plan.model),
                "rules": list(plan.rules),
                "anchors": [dict(a) for a in plan.anchors],
            },
            "activities": [asdict(a) for a in activities],
        },
        indent=2,
        ensure_ascii=False,
    )
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".activities_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---- freshness -------------------------------------------------------------


def is_expired(activity: Activity, today: date) -> bool:
    return activity.expires is not None and date.fromisoformat(activity.expires) < today


def is_stale(activity: Activity, today: date, max_age_days: int = STALE_AFTER_DAYS) -> bool:
    """Stale means nobody has confirmed the listing recently. Unverified counts as stale."""
    if activity.verified is None:
        return True
    return date.fromisoformat(activity.verified) < today - timedelta(days=max_age_days)


def active(activities: tuple[Activity, ...], today: date) -> tuple[Activity, ...]:
    return tuple(a for a in activities if not is_expired(a, today))


def stale_report(activities: tuple[Activity, ...], today: date) -> tuple[list[Activity], list[Activity]]:
    """Returns (stale, expired)."""
    stale = [a for a in activities if not is_expired(a, today) and is_stale(a, today)]
    expired = [a for a in activities if is_expired(a, today)]
    return stale, expired


# ---- catalog edits ---------------------------------------------------------


def find(activities: tuple[Activity, ...], name: str) -> Activity | None:
    key = name.strip().casefold()
    return next((a for a in activities if a.name.casefold() == key), None)


def add_activity(activities: tuple[Activity, ...], new: Activity) -> tuple[Activity, ...]:
    if find(activities, new.name) is not None:
        raise DataError(f"an activity named '{new.name}' already exists")
    return activities + (new,)


def remove_activity(activities: tuple[Activity, ...], name: str) -> tuple[Activity, ...]:
    target = find(activities, name)
    if target is None:
        raise DataError(f"no activity named '{name}'")
    if len(activities) == 1:
        raise DataError("refusing to remove the last activity")
    return tuple(a for a in activities if a is not target)


def mark_verified(activities: tuple[Activity, ...], name: str, today: date) -> tuple[Activity, ...]:
    target = find(activities, name)
    if target is None:
        raise DataError(f"no activity named '{name}'")
    updated = replace(target, verified=today.isoformat())
    return tuple(updated if a is target else a for a in activities)


# ---- picking ---------------------------------------------------------------


def _check_count(count: int) -> None:
    if not MIN_COUNT <= count <= MAX_COUNT:
        raise ValueError(f"count must be between {MIN_COUNT} and {MAX_COUNT}")


def _avoid(pool: list[Activity], exclude: frozenset[str], need: int) -> list[Activity]:
    """Drop recently shown items unless that would leave too few to fill the batch."""
    fresh = [a for a in pool if a.name.casefold() not in exclude]
    return fresh if len(fresh) >= need else pool


def pick(
    activities: tuple[Activity, ...],
    count: int = DEFAULT_COUNT,
    category: str | None = None,
    exclude: frozenset[str] = frozenset(),
    today: date | None = None,
) -> list[Activity]:
    """Return `count` distinct activities, optionally within one category,
    preferring ones not in `exclude` (casefolded names) and never expired ones.
    Returns fewer than `count` only when the pool is smaller than `count`.
    """
    _check_count(count)
    if category is not None and category not in CATEGORIES:
        raise ValueError(f"unknown category '{category}'")
    today = today or date.today()

    pool = [a for a in active(activities, today) if category is None or a.category == category]
    if not pool:
        return []
    pool = _avoid(pool, exclude, count)
    return _rng.sample(pool, k=min(count, len(pool)))


def pick_spread(
    activities: tuple[Activity, ...],
    count: int = DEFAULT_COUNT,
    exclude: frozenset[str] = frozenset(),
    today: date | None = None,
) -> list[Activity]:
    """Like pick() but prefers distinct categories so a batch is not all dance."""
    _check_count(count)
    today = today or date.today()
    live = list(active(activities, today))
    if not live:
        return []

    preferred = _avoid(live, exclude, count)

    by_category: dict[str, list[Activity]] = {}
    for activity in preferred:
        by_category.setdefault(activity.category, []).append(activity)

    categories = list(by_category)
    _rng.shuffle(categories)

    chosen: list[Activity] = [_rng.choice(by_category[c]) for c in categories[:count]]

    if len(chosen) < count:
        remaining = [a for a in preferred if a not in chosen]
        chosen.extend(_rng.sample(remaining, k=min(count - len(chosen), len(remaining))))
    return chosen
