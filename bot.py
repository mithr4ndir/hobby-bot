"""Discord bot: /suggest, /plan, /feed on|off|status|now, /activity add|remove|verify|list|stale|reload.

Run with:  python bot.py   (reads DISCORD_TOKEN from the environment or .env)
"""

from __future__ import annotations

import logging
import math
import os
import sys
from datetime import date, datetime
from typing import Final

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

import feed_state
import paths
import runtime
import suggest

LOG: Final = logging.getLogger("hobbybot")

EMBED_COLOR: Final = discord.Color.from_rgb(88, 101, 242)
STALE_MARK: Final = "⚠ verify first"


def _load_token() -> str:
    """Local dev: DISCORD_TOKEN from .env. Cluster: DISCORD_TOKEN_FILE from a
    mounted Secret, which may hold a placeholder until the real token lands.
    Waiting keeps the pod Running instead of crash looping."""
    load_dotenv()
    token = runtime.wait_for_token(on_wait=lambda: runtime.touch_heartbeat(runtime.heartbeat_path()))
    if token is None:
        LOG.error("no Discord token available")
        sys.exit(1)
    return token


def _guild_scope() -> discord.Object | None:
    """Optional DISCORD_GUILD_ID restricts command sync to one server, which
    makes slash commands appear instantly instead of after global propagation."""
    raw = os.environ.get("DISCORD_GUILD_ID", "").strip()
    if not raw:
        return None
    if not raw.isdigit():
        LOG.error("DISCORD_GUILD_ID must be numeric")
        sys.exit(1)
    return discord.Object(id=int(raw))


def activity_embed(title: str, picks: list[suggest.Activity], today: date) -> discord.Embed:
    embed = discord.Embed(title=title, color=EMBED_COLOR)
    for activity in picks:
        label = suggest.CATEGORY_LABELS.get(activity.category, activity.category)
        header = f"{activity.name}  ({label})"
        if suggest.is_stale(activity, today):
            header += f"  {STALE_MARK}"
        checked = f"Last checked {activity.verified}" if activity.verified else "Never verified"
        value = (
            f"**When:** {activity.when}\n"
            f"**Where:** {activity.where}\n"
            f"**Cost:** {activity.cost}\n"
            f"{activity.notes}\n"
            f"[Link]({activity.url})  •  {checked}"
        )
        embed.add_field(name=header[:256], value=value[:1024], inline=False)
    embed.set_footer(text="Schedules drift. Run /activity verify <name> after you confirm one.")
    return embed


def plan_embed(plan: suggest.Plan) -> discord.Embed:
    embed = discord.Embed(title=plan.title, color=EMBED_COLOR)
    embed.add_field(name="The model", value="\n".join(f"• {line}" for line in plan.model), inline=False)
    for anchor in plan.anchors:
        embed.add_field(
            name=anchor.get("slot", "Anchor"),
            value=f"**{anchor.get('name', '')}**\n{anchor.get('when', '')}\n{anchor.get('where', '')}",
            inline=False,
        )
    embed.add_field(name="Conversion rules", value="\n".join(f"• {line}" for line in plan.rules), inline=False)
    return embed


def _lines(items: list[suggest.Activity], with_date: bool) -> str:
    if not items:
        return "none"
    out = []
    for a in items:
        suffix = f" (checked {a.verified or 'never'})" if with_date else ""
        out.append(f"• {a.name}{suffix}")
    text = "\n".join(out)
    return text[:1000] + ("\n…" if len(text) > 1000 else "")


class HobbyBot(discord.Client):
    def __init__(self) -> None:
        # No privileged intents needed: slash commands and sending embeds only.
        super().__init__(intents=discord.Intents.none())
        self.tree = app_commands.CommandTree(self)
        runtime.seed_data(paths.BUNDLED_DATA_FILE, suggest.DATA_FILE)
        self.plan, self.activities = suggest.load_data()
        self.state = feed_state.load()
        self.heartbeat_file = runtime.heartbeat_path()

    async def setup_hook(self) -> None:
        scope = _guild_scope()
        if scope is not None:
            self.tree.copy_global_to(guild=scope)
            await self.tree.sync(guild=scope)
        else:
            await self.tree.sync()
        self.feed_tick.start()
        self.heartbeat.start()

    async def on_ready(self) -> None:
        LOG.info("logged in as %s (id=%s)", self.user, getattr(self.user, "id", "?"))

    # ---- shared helpers ---------------------------------------------------

    def take_picks(self, count: int, category: str | None = None) -> list[suggest.Activity]:
        """Pick, then remember what was shown so the next batch avoids it."""
        today = date.today()
        exclude = feed_state.recent_set(self.state)
        if category is None:
            picks = suggest.pick_spread(self.activities, count, exclude=exclude, today=today)
        else:
            picks = suggest.pick(self.activities, count, category, exclude=exclude, today=today)
        if picks:
            self.state = feed_state.remember(self.state, [p.name for p in picks])
            self._save_state()
        return picks

    def _save_state(self) -> bool:
        try:
            feed_state.save(self.state)
            return True
        except (OSError, ValueError):
            LOG.exception("could not save feed state")
            return False

    def save_catalog(self, activities: tuple[suggest.Activity, ...]) -> bool:
        try:
            suggest.save_data(self.plan, activities)
        except OSError:
            LOG.exception("could not save activities.json")
            return False
        self.activities = activities
        return True

    # ---- scheduled feed ---------------------------------------------------

    @tasks.loop(minutes=10)
    async def feed_tick(self) -> None:
        now = datetime.now().astimezone()
        if not feed_state.is_due(self.state, now):
            return
        channel = self.get_channel(self.state.channel_id or 0)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            LOG.warning("feed channel %s not found or not a text channel", self.state.channel_id)
            return
        picks = self.take_picks(self.state.count)
        try:
            await channel.send(embed=activity_embed("This week's picks", picks, now.date()))
        except discord.HTTPException:
            LOG.exception("failed to post scheduled feed")
            return
        self.state = feed_state.mark_posted(self.state, now)
        self._save_state()

    @feed_tick.before_loop
    async def _wait_ready(self) -> None:
        await self.wait_until_ready()

    # ---- liveness ---------------------------------------------------------

    @tasks.loop(minutes=1)
    async def heartbeat(self) -> None:
        """Touch the heartbeat file only while the gateway session is healthy.
        latency is NaN when no heartbeat ack has arrived, so a wedged socket
        stops the file aging out and the kubelet restarts the pod."""
        if self.is_closed() or not math.isfinite(self.latency):
            return
        runtime.touch_heartbeat(self.heartbeat_file)

    @heartbeat.before_loop
    async def _heartbeat_wait_ready(self) -> None:
        await self.wait_until_ready()


bot = HobbyBot()

CATEGORY_CHOICES: Final = [
    app_commands.Choice(name=suggest.CATEGORY_LABELS[c], value=c) for c in suggest.CATEGORIES
]
WEEKDAY_CHOICES: Final = [
    app_commands.Choice(name=day.capitalize(), value=day) for day in feed_state.WEEKDAYS
]


# ---- /suggest and /plan ------------------------------------------------------


@bot.tree.command(name="suggest", description="Get a few social hobby picks for the week")
@app_commands.describe(
    category="Limit picks to one category (default: a spread across categories)",
    count="How many picks, 1 to 5 (default 3)",
)
@app_commands.choices(category=CATEGORY_CHOICES)
async def suggest_cmd(
    interaction: discord.Interaction,
    category: app_commands.Choice[str] | None = None,
    count: app_commands.Range[int, suggest.MIN_COUNT, suggest.MAX_COUNT] = suggest.DEFAULT_COUNT,
) -> None:
    picks = bot.take_picks(count, category.value if category else None)
    if not picks:
        await interaction.response.send_message("Nothing live in that category right now.", ephemeral=True)
        return
    title = f"Your picks: {category.name}" if category else "Your picks"
    await interaction.response.send_message(embed=activity_embed(title, picks, date.today()))


@bot.tree.command(name="plan", description="Post the weekly social hobby system")
async def plan_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(embed=plan_embed(bot.plan))


# ---- /feed -------------------------------------------------------------------

feed_group = app_commands.Group(
    name="feed",
    description="Toggle the scheduled weekly picks",
    default_permissions=discord.Permissions(manage_guild=True),
    guild_only=True,
)


@feed_group.command(name="on", description="Post picks in this channel every week")
@app_commands.describe(
    weekday="Day to post (default Monday)",
    hour="Hour to post, 0 to 23, bot's local time (default 9)",
    count="How many picks each time, 1 to 5 (default 3)",
)
@app_commands.choices(weekday=WEEKDAY_CHOICES)
async def feed_on(
    interaction: discord.Interaction,
    weekday: app_commands.Choice[str] | None = None,
    hour: app_commands.Range[int, 0, 23] = feed_state.DEFAULT_HOUR,
    count: app_commands.Range[int, 1, 5] = feed_state.DEFAULT_COUNT,
) -> None:
    if interaction.channel_id is None:
        await interaction.response.send_message("Run this inside a text channel.", ephemeral=True)
        return
    new_state = feed_state.FeedState(
        enabled=True,
        channel_id=interaction.channel_id,
        weekday=weekday.value if weekday else feed_state.DEFAULT_WEEKDAY,
        hour=hour,
        count=count,
        last_posted=None,
        recent=bot.state.recent,
    )
    bot.state = new_state
    if not bot._save_state():
        await interaction.response.send_message("Could not save settings. Check the bot logs.", ephemeral=True)
        return
    LOG.info("feed enabled by user_id=%s in channel_id=%s", interaction.user.id, interaction.channel_id)
    await interaction.response.send_message(
        f"Feed on. {count} picks every {new_state.weekday.capitalize()} at {hour:02d}:00 in this channel.",
        ephemeral=True,
    )


@feed_group.command(name="off", description="Stop the scheduled picks")
async def feed_off(interaction: discord.Interaction) -> None:
    from dataclasses import replace

    bot.state = replace(bot.state, enabled=False)
    if not bot._save_state():
        await interaction.response.send_message("Could not save settings. Check the bot logs.", ephemeral=True)
        return
    LOG.info("feed disabled by user_id=%s", interaction.user.id)
    await interaction.response.send_message("Feed off.", ephemeral=True)


@feed_group.command(name="status", description="Show whether the feed is on and where it posts")
async def feed_status(interaction: discord.Interaction) -> None:
    s = bot.state
    if not s.enabled:
        await interaction.response.send_message("Feed is off.", ephemeral=True)
        return
    await interaction.response.send_message(
        f"Feed is on: {s.count} picks every {s.weekday.capitalize()} at {s.hour:02d}:00 "
        f"in <#{s.channel_id}>. Last posted: {s.last_posted or 'never'}. "
        f"Remembering {len(s.recent)} recent picks to avoid repeats.",
        ephemeral=True,
    )


@feed_group.command(name="now", description="Post a batch of picks right now")
async def feed_now(interaction: discord.Interaction) -> None:
    picks = bot.take_picks(bot.state.count)
    await interaction.response.send_message(embed=activity_embed("Picks on demand", picks, date.today()))


bot.tree.add_command(feed_group)


# ---- /activity (catalog management) -------------------------------------------

activity_group = app_commands.Group(
    name="activity",
    description="Add, remove, verify or list events in the catalog",
    default_permissions=discord.Permissions(manage_guild=True),
    guild_only=True,
)


@activity_group.command(name="add", description="Add a new event or group to the catalog")
@app_commands.describe(
    name="Short unique name",
    category="Which bucket it belongs to",
    when="Day and time, e.g. 'Tuesdays 7pm'",
    where="Venue and street",
    cost="Free, $10, buy a drink, etc.",
    notes="Why it is good for a newbie",
    url="https:// link to the schedule or group page",
    expires="Optional ISO date (YYYY-MM-DD) after which it is hidden, for one-off series",
)
@app_commands.choices(category=CATEGORY_CHOICES)
async def activity_add(
    interaction: discord.Interaction,
    name: app_commands.Range[str, 1, suggest.MAX_NAME],
    category: app_commands.Choice[str],
    when: app_commands.Range[str, 1, suggest.MAX_SHORT],
    where: app_commands.Range[str, 1, suggest.MAX_SHORT],
    cost: app_commands.Range[str, 1, suggest.MAX_SHORT],
    notes: app_commands.Range[str, 1, suggest.MAX_NOTES],
    url: app_commands.Range[str, 9, suggest.MAX_URL],
    expires: app_commands.Range[str, 10, 10] | None = None,
) -> None:
    try:
        new = suggest.activity_from_dict(
            {
                "name": name,
                "category": category.value,
                "when": when,
                "where": where,
                "cost": cost,
                "notes": notes,
                "url": url,
                "verified": date.today().isoformat(),
                "expires": expires,
            }
        )
        updated = suggest.add_activity(bot.activities, new)
    except suggest.DataError as exc:
        # DataError messages are our own text, safe to show.
        await interaction.response.send_message(f"Not added: {exc}", ephemeral=True)
        return
    if not bot.save_catalog(updated):
        await interaction.response.send_message("Could not save the catalog. Check the bot logs.", ephemeral=True)
        return
    LOG.info("activity added by user_id=%s: %s", interaction.user.id, new.name)
    await interaction.response.send_message(f"Added **{new.name}** to {category.name}.", ephemeral=True)


@activity_group.command(name="remove", description="Remove an event from the catalog")
@app_commands.describe(name="Exact name as shown in /activity list")
async def activity_remove(
    interaction: discord.Interaction,
    name: app_commands.Range[str, 1, suggest.MAX_NAME],
) -> None:
    try:
        updated = suggest.remove_activity(bot.activities, name)
    except suggest.DataError as exc:
        await interaction.response.send_message(f"Not removed: {exc}", ephemeral=True)
        return
    if not bot.save_catalog(updated):
        await interaction.response.send_message("Could not save the catalog. Check the bot logs.", ephemeral=True)
        return
    LOG.info("activity removed by user_id=%s: %s", interaction.user.id, name)
    await interaction.response.send_message(f"Removed **{name.strip()}**.", ephemeral=True)


@activity_group.command(name="verify", description="Mark an event as checked today so it stops being flagged stale")
@app_commands.describe(name="Exact name as shown in /activity list")
async def activity_verify(
    interaction: discord.Interaction,
    name: app_commands.Range[str, 1, suggest.MAX_NAME],
) -> None:
    try:
        updated = suggest.mark_verified(bot.activities, name, date.today())
    except suggest.DataError as exc:
        await interaction.response.send_message(f"Not verified: {exc}", ephemeral=True)
        return
    if not bot.save_catalog(updated):
        await interaction.response.send_message("Could not save the catalog. Check the bot logs.", ephemeral=True)
        return
    await interaction.response.send_message(f"**{name.strip()}** verified as of today.", ephemeral=True)


@activity_group.command(name="list", description="List everything in the catalog, optionally one category")
@app_commands.choices(category=CATEGORY_CHOICES)
async def activity_list(
    interaction: discord.Interaction,
    category: app_commands.Choice[str] | None = None,
) -> None:
    today = date.today()
    embed = discord.Embed(title="Catalog", color=EMBED_COLOR)
    for cat in suggest.CATEGORIES:
        if category and cat != category.value:
            continue
        items = [a for a in bot.activities if a.category == cat and not suggest.is_expired(a, today)]
        if items:
            embed.add_field(name=suggest.CATEGORY_LABELS[cat], value=_lines(items, with_date=True), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@activity_group.command(name="stale", description="Show events nobody has verified in 90 days, and expired ones")
async def activity_stale(interaction: discord.Interaction) -> None:
    stale, expired = suggest.stale_report(bot.activities, date.today())
    embed = discord.Embed(title="Freshness check", color=EMBED_COLOR)
    embed.add_field(
        name=f"Stale (not checked in {suggest.STALE_AFTER_DAYS} days)",
        value=_lines(stale, with_date=True),
        inline=False,
    )
    embed.add_field(name="Expired (hidden from picks)", value=_lines(expired, with_date=False), inline=False)
    embed.set_footer(text="Confirm a listing, then /activity verify <name>. Gone for good? /activity remove <name>.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@activity_group.command(name="reload", description="Reload activities.json after editing it by hand")
async def activity_reload(interaction: discord.Interaction) -> None:
    try:
        plan, activities = suggest.load_data()
    except (OSError, ValueError) as exc:
        LOG.warning("reload failed: %s", exc)
        await interaction.response.send_message(
            "Reload failed, the file did not validate. Previous catalog kept. See logs.", ephemeral=True
        )
        return
    bot.plan, bot.activities = plan, activities
    await interaction.response.send_message(f"Reloaded {len(activities)} activities.", ephemeral=True)


bot.tree.add_command(activity_group)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    # Generic message to the user, details to the log.
    LOG.warning("command error for user_id=%s: %s", interaction.user.id, error.__class__.__name__)
    if isinstance(error, app_commands.MissingPermissions):
        message = "You need Manage Server permission for that."
    else:
        message = "Something went wrong. Try again in a moment."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


def main() -> None:
    runtime.configure_logging()
    token = _load_token()
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
