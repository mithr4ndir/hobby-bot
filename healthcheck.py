"""Liveness probe entry point: exit 0 if the heartbeat file is fresh.

    python healthcheck.py            (uses HOBBY_BOT_HEARTBEAT, 300s)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from runtime import heartbeat_fresh

MAX_AGE_SECONDS = 300


def main() -> int:
    raw = os.environ.get("HOBBY_BOT_HEARTBEAT", "").strip()
    if not raw:
        print("HOBBY_BOT_HEARTBEAT not set", file=sys.stderr)
        return 1
    return 0 if heartbeat_fresh(Path(raw), MAX_AGE_SECONDS) else 1


if __name__ == "__main__":
    sys.exit(main())
