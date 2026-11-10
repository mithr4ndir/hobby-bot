# hobby-bot

Discord bot that posts SF and Bay Area social hobby picks from a small catalog. Three per batch by default. Remembers the last nine picks so consecutive batches do not repeat, flags listings nobody has verified in 90 days, and hides expired ones. Runs as a Deployment on Kubernetes from `k8s-argocd/apps/automation/hobby-bot`.

## Commands

| Command | What it does | Who |
|---|---|---|
| `/suggest [category] [count]` | Picks on demand. Default 3, spread across categories, avoids the last 9 shown. | Anyone |
| `/plan` | Posts the weekly system (anchors and conversion rules). | Anyone |
| `/feed on [weekday] [hour] [count]` | Weekly post in the current channel. Default Monday 09:00 Pacific, 3 picks. | Manage Server |
| `/feed off` | Turns the weekly post off. | Manage Server |
| `/feed status` | On/off, channel, schedule, last post, how many recent picks are remembered. | Manage Server |
| `/feed now` | Posts a batch immediately. | Manage Server |
| `/activity add` | Add an event: name, category, when, where, cost, notes, https URL, optional expiry date. Stamped verified today. | Manage Server |
| `/activity remove <name>` | Delete an event. | Manage Server |
| `/activity verify <name>` | Mark an event as checked today, clearing its stale flag. | Manage Server |
| `/activity list [category]` | Show the catalog with last-checked dates. | Manage Server |
| `/activity stale` | Show events not verified in 90 days and expired ones. | Manage Server |
| `/activity reload` | Re-read `activities.json` after a hand edit. Keeps the old catalog if the file fails validation. | Manage Server |

## How freshness works

- Every entry has `verified` (ISO date). Picks older than 90 days show a "verify first" marker. Run `/activity verify` after you confirm a listing is still real.
- Optional `expires` hides an entry from picks after that date. Use it for a limited series or a venue closing.
- `/activity stale` once a month is the maintenance loop. Verify what is still running, remove what is dead.
- The "recent" memory lives in `feed_state.json` and is shared by `/suggest` and the weekly feed, so neither repeats the other.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `DISCORD_TOKEN_FILE` | Path to a file holding the bot token (mounted Secret). Wins over `DISCORD_TOKEN`. | unset |
| `DISCORD_TOKEN` | Bot token for local runs, usually via `.env`. | unset |
| `DISCORD_GUILD_ID` | Optional server ID. Slash commands sync instantly to that server instead of globally. | unset |
| `HOBBY_BOT_DATA_DIR` | Where `activities.json` and `feed_state.json` live. Seeded from the bundled catalog on first start. | source dir |
| `HOBBY_BOT_HEARTBEAT` | File touched once a minute while healthy. `healthcheck.py` reads it for the liveness probe. | unset |
| `HOBBY_BOT_WEBHOOK_FILE` | Path to a file holding a Discord webhook URL (mounted Secret). Wins over `HOBBY_BOT_WEBHOOK_URL`. | unset |
| `HOBBY_BOT_WEBHOOK_URL` | Webhook URL for local runs. | unset |
| `TZ` | Zone for the weekly feed hour. | system |

## Modes

- **Gateway mode** (a usable token): slash commands plus the weekly feed. The feed posts to the channel chosen with `/feed on`, or through the webhook when no channel was chosen.
- **Webhook-only mode** (no token, but a webhook URL): the weekly feed posts through the webhook, attributed to "Hobby Bot". No slash commands. With no state file yet, the feed starts enabled on the defaults (Monday 09:00, 3 picks). The process checks for a token every minute and exits when one appears so the container restarts into gateway mode.
- `python bot.py --post-now` posts one batch through the webhook and exits. Handy for a smoke test or a one-off Job.

A token equal to `REPLACE_ME` (or empty) with no webhook makes the bot log a warning and wait, re-reading the file every minute. It never crash loops on a placeholder.

## Discord setup

1. https://discord.com/developers/applications, New Application.
2. Bot tab: Reset Token, copy it. No privileged intents are needed.
3. OAuth2 tab, URL Generator: scopes `bot` and `applications.commands`; bot permissions `Send Messages` and `Embed Links`. Open the URL and add the bot to your server.
4. Developer Mode in Discord (Settings, Advanced), right-click the server, Copy Server ID.

## Run locally

```powershell
Copy-Item .env.example .env   # paste DISCORD_TOKEN and DISCORD_GUILD_ID
uv sync --frozen
uv run python bot.py
```

## Validate

```powershell
uv run ruff check
uv run pytest -q -rs
uv export --frozen --no-dev --format requirements.txt -o requirements.lock
```

CI fails if `requirements.lock` is out of date with `uv.lock`. Re-export after any dependency change.

## Image

`ghcr.io/mithr4ndir/hobby-bot`, built by `.github/workflows/ci.yml` on every push to `main` and weekly. Dependencies install from the hash-locked `requirements.lock`, the image runs as UID 65532 with a read-only root filesystem, and Trivy fails the build on HIGH or CRITICAL findings. Tags: the pyproject semver, `sha-<commit>`, `latest`. The cluster pins semver plus digest.

## Editing the picks by hand

Edit `activities.json`, then `/activity reload`. Every entry needs `name`, `category`, `when`, `where`, `cost`, `notes`, `url` (https only), and may carry `verified` and `expires`. Names must be unique. Categories are fixed in `suggest.py`; add a new one there and in `CATEGORY_LABELS`, then restart so the slash command choices re-sync. In the cluster the live file is on the PVC, not in the image.

## Security notes

- Token only from a mounted Secret file or the local `.env`. `.env` and `feed_state.json` are gitignored.
- No privileged intents, so the bot cannot read messages.
- `/feed` and `/activity` are gated on Manage Server permission server-side by Discord.
- Every string field has a length cap enforced both in the command definition and in `suggest.py`. URLs must be https with no whitespace. Dates must parse as ISO.
- Catalog and state writes are atomic (temp file plus rename), so a crash cannot corrupt them.
- Errors shown to users are generic except validation messages the code itself produced. Details go to the JSON log on stdout.
- The feed posts at most once per calendar day even if the bot restarts.
