"""Transient commands delivered to clients through their regular poll request."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Dict, Optional


class ClientCommandStore:
    """Small in-memory command queue; one pending command per screen."""

    ALLOWED_COMMANDS = {"restart"}

    def __init__(self):
        self._lock = threading.RLock()
        self._pending: Dict[int, dict] = {}

    def queue(self, screen_id: int, command: str) -> dict:
        command = str(command).strip().lower()
        if command not in self.ALLOWED_COMMANDS:
            raise ValueError(f"Unsupported client command: {command}")
        item = {
            "token": uuid.uuid4().hex,
            "type": command,
            "screen_id": int(screen_id),
            "issued_at": time.time(),
        }
        with self._lock:
            self._pending[int(screen_id)] = item
        return dict(item)

    def poll(self, screen_id: int) -> Optional[dict]:
        with self._lock:
            item = self._pending.pop(int(screen_id), None)
        return dict(item) if item else None

    def pending(self, screen_id: int) -> Optional[dict]:
        with self._lock:
            item = self._pending.get(int(screen_id))
        return dict(item) if item else None

    def clear(self):
        with self._lock:
            self._pending.clear()
