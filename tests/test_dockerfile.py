"""The image copies modules by name. A module added to the repo but not to the
COPY line imports fine locally and crash loops in the cluster (0.2.0 did
exactly that with webhook.py). Keep the two lists in sync."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_copies_every_root_module() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    copy_lines = [line for line in dockerfile.splitlines() if line.startswith("COPY ") and "/app/" in line]
    assert copy_lines, "expected a COPY ... /app/ line"
    copied = set()
    for line in copy_lines:
        copied.update(re.findall(r"\S+\.(?:py|json)", line.split("COPY ", 1)[1]))
    modules = {p.name for p in ROOT.glob("*.py")}
    assert modules <= copied, f"not copied into the image: {sorted(modules - copied)}"
    assert "activities.json" in copied
