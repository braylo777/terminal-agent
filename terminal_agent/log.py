"""Append-only action log. One JSON object per line."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class ActionLog:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.events: list[dict] = []
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")

    def write(self, event: dict) -> dict:
        row = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        self.events.append(row)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        return row
