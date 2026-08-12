"""Persistent, versioned settings delivered to Pi clients during polling."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Optional


class ClientSettingsStore:
    """Store one desired settings document per screen with atomic persistence."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._screens: Dict[int, dict] = {}
        self._load()

    def _load(self):
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError, TypeError):
            return

        screens = document.get("screens", {}) if isinstance(document, dict) else {}
        if not isinstance(screens, dict):
            return
        for raw_screen_id, raw_record in screens.items():
            try:
                screen_id = int(raw_screen_id)
                revision = max(1, int(raw_record.get("revision", 1)))
                settings = raw_record.get("settings", {})
            except (TypeError, ValueError, AttributeError):
                continue
            if 1 <= screen_id <= 8 and isinstance(settings, dict):
                self._screens[screen_id] = {
                    "revision": revision,
                    "settings": copy.deepcopy(settings),
                }

    def _write_locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "screens": {
                str(screen_id): record
                for screen_id, record in sorted(self._screens.items())
            },
        }
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)

    def set(self, screen_id: int, settings: Dict[str, Any]) -> dict:
        screen_id = int(screen_id)
        if not 1 <= screen_id <= 8:
            raise ValueError("screen_id must be between 1 and 8")
        if not isinstance(settings, dict):
            raise ValueError("settings must be an object")
        with self._lock:
            previous = self._screens.get(screen_id, {})
            revision = int(previous.get("revision", 0)) + 1
            self._screens[screen_id] = {
                "revision": revision,
                "settings": copy.deepcopy(settings),
            }
            self._write_locked()
            return self.get(screen_id)

    def get(self, screen_id: int) -> Optional[dict]:
        with self._lock:
            record = self._screens.get(int(screen_id))
            return copy.deepcopy(record) if record else None

    def payload(self, screen_id: int, known_revision: int = 0) -> dict:
        record = self.get(screen_id)
        if not record:
            return {"changed": False, "revision": 0}
        revision = int(record["revision"])
        if revision == int(known_revision or 0):
            return {"changed": False, "revision": revision}
        return {
            "changed": True,
            "revision": revision,
            "settings": record["settings"],
        }
