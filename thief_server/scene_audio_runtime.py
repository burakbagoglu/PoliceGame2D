"""Yayınlanmış sahnelerin merkezi ses timeline'ını tekilleştirerek çalıştırır."""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional


class SceneAudioRuntime:
    """Çok sayıda client poll etse bile her cue'yu yalnızca bir kez tetikler."""

    def __init__(self, play_cue: Callable[[dict], bool], stop_loops: Callable[[], None]):
        self._play_cue = play_cue
        self._stop_loops = stop_loops
        self._lock = threading.RLock()
        self._scene_id: Optional[str] = None
        self._started_at = 0.0
        self._fired = set()

    def reset(self):
        with self._lock:
            self._scene_id = None
            self._started_at = 0.0
            self._fired.clear()
            self._stop_loops()

    def tick(self, scene_id: str, document: dict, now: Optional[float] = None) -> list:
        now = time.monotonic() if now is None else float(now)
        with self._lock:
            scene = document.get("scenes", {}).get(scene_id)
            if not isinstance(scene, dict):
                return []
            if scene_id != self._scene_id:
                self._stop_loops()
                self._scene_id = scene_id
                self._started_at = now
                self._fired.clear()

            elapsed = max(0.0, now - self._started_at)
            duration = max(0.1, float(scene.get("duration", 5.0)))
            loop_timeline = bool(scene.get("loop_timeline", False))
            cycle = int(elapsed // duration) if loop_timeline else 0
            timeline_time = elapsed % duration if loop_timeline else min(elapsed, duration)
            fired_now = []
            for index, cue in enumerate(scene.get("audio_cues", [])):
                if not isinstance(cue, dict) or not cue.get("enabled", True):
                    continue
                cue_time = max(0.0, float(cue.get("time", 0)))
                key = (-1 if cue.get("loop") else cycle, str(cue.get("id", index)))
                if cue_time <= timeline_time and key not in self._fired:
                    self._fired.add(key)
                    self._play_cue(dict(cue))
                    fired_now.append(dict(cue))
            if loop_timeline:
                self._fired = {key for key in self._fired if key[0] == -1 or key[0] >= cycle - 1}
            return fired_now