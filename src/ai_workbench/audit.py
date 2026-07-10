"""Append-only, local audit log without secrets."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, root: Path):
        self.path = root / ".pipkinpad-audit.jsonl"

    def record(self, event: str, **details: Any) -> None:
        record = {"at": datetime.now(timezone.utc).isoformat(), "event": event, **details}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
