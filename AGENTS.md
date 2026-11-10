# Agent instructions: hobby-bot

## Purpose

A Discord bot that posts SF and Bay Area social hobby picks, three at a time, on demand
(`/suggest`) or on a weekly toggle (`/feed on`). Remembers the last nine picks so batches
do not repeat, flags catalog entries nobody verified in 90 days, hides expired ones, and
lets a server admin add, remove and verify entries from Discord (`/activity`). Shipped as
`ghcr.io/mithr4ndir/hobby-bot`, run as a Deployment from `k8s-argocd/apps/automation/hobby-bot`.

## Boundaries

- `[tool.uv] package = false`. The image copies the modules and runs `python /app/bot.py`.
- Pure logic lives in `suggest.py`, `feed_state.py`, `runtime.py`. `bot.py` is the thin
  Discord layer. Tests never need a token or the network.
- The token comes from `DISCORD_TOKEN_FILE` (a mounted Secret) in the cluster and
  `DISCORD_TOKEN` in `.env` locally. Nothing secret is in this repo.
- State is JSON replaced by rename (`activities.json`, `feed_state.json` under
  `HOBBY_BOT_DATA_DIR`). Do not move it to SQLite: it lives on NFS.
- The bundled `activities.json` is a seed. `runtime.seed_data` copies it into the data
  volume once and never overwrites the live copy.

## Validation

```
uv sync --frozen
uv run ruff check
uv run pytest -q -rs
uv export --frozen --no-dev --format requirements.txt -o requirements.lock
```

CI fails if `requirements.lock` drifts from `uv.lock`. Re-export after any dependency change.

## Landmines

- A placeholder token (`REPLACE_ME`) must make the bot idle, not crash loop. The 1Password
  item is created with that value before the real token exists. See `runtime.wait_for_token`.
- The liveness probe reads the heartbeat file's mtime. The heartbeat is touched only while
  the gateway latency is finite, so a wedged websocket restarts the pod. It is also touched
  while waiting for a token, because a restart would not help there.
- `/feed on` resets `last_posted` so the first scheduled post is not skipped. It keeps
  `recent` so switching the schedule does not cause repeats.
- Categories are a fixed tuple in `suggest.py`. Adding one needs a restart so the slash
  command choices re-sync with Discord.
