"""Where mutable data lives. Locally that is the source directory; in the
container it is the /data volume (HOBBY_BOT_DATA_DIR)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

SOURCE_DIR: Final[Path] = Path(__file__).resolve().parent


def data_dir() -> Path:
    raw = os.environ.get("HOBBY_BOT_DATA_DIR", "").strip()
    return Path(raw) if raw else SOURCE_DIR


DATA_DIR: Final[Path] = data_dir()
BUNDLED_DATA_FILE: Final[Path] = SOURCE_DIR / "activities.json"
