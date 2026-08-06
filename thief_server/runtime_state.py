"""Atomic runtime checkpoint used to recover an active game after power loss."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional


class RuntimeStateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self._lock = threading.RLock()

    def save(self, payload: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            fd, temporary = tempfile.mkstemp(
                prefix=self.path.name + ".",
                suffix=".tmp",
                dir=self.path.parent,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def load(self) -> Optional[dict]:
        with self._lock:
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                return None
        return payload if isinstance(payload, dict) else None

    def clear(self):
        with self._lock:
            self.path.unlink(missing_ok=True)
