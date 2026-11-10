"""Exercise bot.py's mode selection and webhook-only loop without a token or network."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import discord
import pytest

import bot
import feed_state as fs
import runtime
import suggest

HOOK = "https://discord.com/api/webhooks/123456789012345678/abcDEF_ghi-JKL"


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point suggest/feed_state at a temp dir so tests never touch the real files."""
    monkeypatch.setattr(suggest, "DATA_FILE", tmp_path / "activities.json")
    monkeypatch.setattr(fs, "STATE_FILE", tmp_path / "feed_state.json")
    state_path = tmp_path / "feed_state.json"
    monkeypatch.setattr(fs, "initial", lambda has_webhook, path=state_path: fs_initial(has_webhook, path))
    monkeypatch.setattr(fs, "save", lambda state, path=tmp_path / "feed_state.json": fs_save(state, path))
    monkeypatch.setattr(fs, "load", lambda path=tmp_path / "feed_state.json": fs_load(path))
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_TOKEN_FILE", raising=False)
    monkeypatch.setenv("HOBBY_BOT_HEARTBEAT", str(tmp_path / "heartbeat"))
    return tmp_path


# Re-export the originals under new names so the lambdas above can bind the temp path.
fs_initial = fs.initial
fs_save = fs.save
fs_load = fs.load


def test_core_seeds_catalog_and_defaults_feed_on_with_webhook(data_dir: Path) -> None:
    core = bot.Core(HOOK)
    assert (data_dir / "activities.json").exists()
    assert len(core.activities) >= 15
    assert core.state.enabled is True
    assert json.loads((data_dir / "feed_state.json").read_text(encoding="utf-8"))["enabled"] is True


def test_core_feed_off_by_default_without_webhook(data_dir: Path) -> None:
    core = bot.Core(None)
    assert core.state.enabled is False


def test_post_via_webhook_sends_embed_and_remembers(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, discord.Embed]] = []

    async def fake_post(url: str, embed: discord.Embed, username: str = "x") -> bool:
        sent.append((url, embed))
        return True

    monkeypatch.setattr(bot.webhook, "post_embed", fake_post)
    core = bot.Core(HOOK)
    ok = asyncio.run(core.post_via_webhook("Picks on demand"))
    assert ok is True
    assert len(sent) == 1
    url, embed = sent[0]
    assert url == HOOK
    assert embed.title == "Picks on demand"
    assert len(embed.fields) == 3
    assert len(core.state.recent) == 3


def test_post_via_webhook_without_url_is_false(data_dir: Path) -> None:
    core = bot.Core(None)
    assert asyncio.run(core.post_via_webhook("x")) is False


def test_webhook_only_loop_posts_when_due_then_exits_on_token(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    posts: list[str] = []

    async def fake_post(url: str, embed: discord.Embed, username: str = "x") -> bool:
        posts.append(embed.title)
        return True

    monkeypatch.setattr(bot.webhook, "post_embed", fake_post)

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return datetime(2026, 11, 9, 9, 5).astimezone()

    monkeypatch.setattr(bot, "datetime", FakeDT)

    tokens = iter([None, None, "A" * 26 + "." + "b" * 8 + "." + "c" * 40])
    monkeypatch.setattr(runtime, "read_token", lambda env=None: next(tokens))

    sleeps: list[int] = []

    async def fake_sleep(seconds: int) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(bot.asyncio, "sleep", fake_sleep)

    core = bot.Core(HOOK)
    asyncio.run(bot.webhook_only_loop(core, tick_seconds=1))

    assert posts == ["This week's picks"], "due once at Monday 09:05, posted exactly once across three ticks"
    assert core.state.last_posted == "2026-11-09"
    assert sleeps == [1, 1], "two waits before the token showed up on the third tick"
    assert (data_dir / "heartbeat").exists()


def test_main_post_now_requires_webhook(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOBBY_BOT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("HOBBY_BOT_WEBHOOK_FILE", raising=False)
    assert bot.main(["--post-now"]) == 1


def test_main_post_now_posts_once(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_post(url: str, embed: discord.Embed, username: str = "x") -> bool:
        calls.append(embed.title)
        return True

    monkeypatch.setattr(bot.webhook, "post_embed", fake_post)
    monkeypatch.setenv("HOBBY_BOT_WEBHOOK_URL", HOOK)
    assert bot.main(["--post-now"]) == 0
    assert calls == ["Picks on demand"]


def test_register_commands_builds_full_tree(data_dir: Path) -> None:
    core = bot.Core(None)
    client = bot.HobbyBot(core)
    bot.register_commands(client)
    names = sorted(c.name for c in client.tree.get_commands())
    assert names == ["activity", "feed", "plan", "suggest"]
    groups = {c.name: sorted(s.name for s in c.commands) for c in client.tree.get_commands() if hasattr(c, "commands")}
    assert groups["feed"] == ["now", "off", "on", "status"]
    assert groups["activity"] == ["add", "list", "reload", "remove", "stale", "verify"]
    asyncio.run(client.close())


def test_gateway_client_requests_only_guilds_intent(data_dir: Path) -> None:
    """get_channel() needs the guild cache, which only the guilds intent fills.
    Without it the weekly feed silently fell back to the webhook even after
    /feed on. Nothing privileged may be requested."""
    client = bot.HobbyBot(bot.Core(None))
    intents = client.intents
    assert intents.guilds is True
    assert intents.message_content is False
    assert intents.members is False
    assert intents.presences is False
    asyncio.run(client.close())
