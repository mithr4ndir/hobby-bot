"""Container plumbing kept out of bot.py: token loading, data seeding, heartbeat,
JSON logs. No Discord imports so it is testable without a token."""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final

LOG: Final = logging.getLogger("hobbybot.runtime")

# The 1Password item is created with this value before the real token exists.
# The bot must idle on it, not crash loop and page the alert channel.
TOKEN_PLACEHOLDER: Final[str] = "REPLACE_ME"
TOKEN_POLL_SECONDS: Final[int] = 60

# Discord bot tokens are three dot-separated base64url segments. The check is a
# cheap sanity filter so a stray newline or a webhook URL pasted into the item
# is caught here with a clear log line instead of a cryptic login failure.
_TOKEN_ALPHABET: Final[frozenset[str]] = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_."
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "src": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def looks_like_token(value: str) -> bool:
    if not value or value == TOKEN_PLACEHOLDER or value.startswith("REPLACE"):
        return False
    if len(value) < 50 or len(value) > 200:
        return False
    if any(c not in _TOKEN_ALPHABET for c in value):
        return False
    return value.count(".") == 2


def read_token(env: dict[str, str] | None = None) -> str | None:
    """DISCORD_TOKEN_FILE (a mounted Secret) wins over DISCORD_TOKEN (local dev).
    Returns None when neither yields something token-shaped."""
    env = os.environ if env is None else env
    path = env.get("DISCORD_TOKEN_FILE", "").strip()
    if path:
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            LOG.warning("token file unreadable: %s", exc.__class__.__name__)
            return None
        return value if looks_like_token(value) else None
    value = env.get("DISCORD_TOKEN", "").strip()
    return value if looks_like_token(value) else None


_WEBHOOK_PREFIXES: Final[tuple[str, ...]] = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
    "https://canary.discord.com/api/webhooks/",
)


def looks_like_webhook_url(value: str) -> bool:
    if not value or len(value) > 300 or any(c.isspace() for c in value):
        return False
    return value.startswith(_WEBHOOK_PREFIXES)


def read_webhook_url(env: dict[str, str] | None = None) -> str | None:
    """HOBBY_BOT_WEBHOOK_FILE (a mounted Secret) wins over HOBBY_BOT_WEBHOOK_URL.
    Returns None when neither yields a Discord webhook URL."""
    env = os.environ if env is None else env
    path = env.get("HOBBY_BOT_WEBHOOK_FILE", "").strip()
    if path:
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            LOG.warning("webhook file unreadable: %s", exc.__class__.__name__)
            return None
        return value if looks_like_webhook_url(value) else None
    value = env.get("HOBBY_BOT_WEBHOOK_URL", "").strip()
    return value if looks_like_webhook_url(value) else None


def wait_for_token(
    sleep: Callable[[float], None] = time.sleep,
    on_wait: Callable[[], None] | None = None,
    poll_seconds: int = TOKEN_POLL_SECONDS,
    max_polls: int | None = None,
) -> str | None:
    """Block until a real token appears. In the cluster the Secret starts as a
    placeholder; External Secrets swaps it in later and the mounted file updates."""
    polls = 0
    while True:
        token = read_token()
        if token is not None:
            return token
        if max_polls is not None and polls >= max_polls:
            return None
        if polls == 0:
            LOG.warning("no usable Discord token yet, waiting (placeholder or empty)")
        if on_wait is not None:
            on_wait()
        sleep(poll_seconds)
        polls += 1


def seed_data(bundled: Path, target: Path) -> bool:
    """Copy the shipped catalog into the data volume the first time. Never
    overwrites: the volume copy is the one edited through /activity."""
    if target.exists():
        return False
    if bundled.resolve() == target.resolve():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".seed.tmp")
    shutil.copyfile(bundled, tmp)
    os.replace(tmp, target)
    LOG.info("seeded catalog into %s", target)
    return True


def heartbeat_path() -> Path | None:
    raw = os.environ.get("HOBBY_BOT_HEARTBEAT", "").strip()
    return Path(raw) if raw else None


def touch_heartbeat(path: Path | None) -> None:
    """The liveness probe checks this file's mtime. Called while connected, and
    while waiting for a token (a restart would not help there)."""
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except OSError as exc:
        LOG.warning("heartbeat touch failed: %s", exc.__class__.__name__)


def heartbeat_fresh(path: Path, max_age_seconds: int, now: float | None = None) -> bool:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    now = time.time() if now is None else now
    return (now - mtime) < max_age_seconds
