"""Client heartbeat store used by the dashboard and calibration tools."""

from __future__ import annotations

import copy
import threading
import time
from typing import Any, Dict


class ClientTelemetryStore:
    """Keep transient client health data in memory without disk churn."""

    def __init__(self, offline_after_seconds: float = 15.0):
        self.offline_after_seconds = float(offline_after_seconds)
        self._lock = threading.RLock()
        self._clients: Dict[int, dict] = {}

    def update(self, screen_id: int, payload: Dict[str, Any]) -> dict:
        now = time.time()
        clean = {
            "screen_id": int(screen_id),
            "received_at": now,
            "fps": round(max(0.0, min(240.0, float(payload.get("fps", 0) or 0))), 1),
            "memory_mb": round(max(0.0, float(payload.get("memory_mb", 0) or 0)), 1),
            "cpu_temp_c": payload.get("cpu_temp_c"),
            "uptime_seconds": max(0, int(payload.get("uptime_seconds", 0) or 0)),
            "scene_version": str(payload.get("scene_version", ""))[:80],
            "active_scene": str(payload.get("active_scene", ""))[:40],
            "network_connected": bool(payload.get("network_connected", False)),
            "serial_connected": bool(payload.get("serial_connected", False)),
            "events_failed": max(0, int(payload.get("events_failed", 0) or 0)),
            "queue_depth": max(0, int(payload.get("queue_depth", 0) or 0)),
            "app_version": str(payload.get("app_version", ""))[:40],
            "frame_time_p95_ms": round(max(0.0, min(1000.0, float(payload.get("frame_time_p95_ms", 0) or 0))), 1),
            "draw_time_p95_ms": round(max(0.0, min(1000.0, float(payload.get("draw_time_p95_ms", 0) or 0))), 1),
            "blit_time_p95_ms": round(max(0.0, min(1000.0, float(payload.get("blit_time_p95_ms", 0) or 0))), 1),
            "flip_time_p95_ms": round(max(0.0, min(1000.0, float(payload.get("flip_time_p95_ms", 0) or 0))), 1),
            "performance_profile": str(payload.get("performance_profile", ""))[:32],
            "quality_level": str(payload.get("quality_level", ""))[:16],
            "render_width": max(0, min(7680, int(payload.get("render_width", 0) or 0))),
            "render_height": max(0, min(4320, int(payload.get("render_height", 0) or 0))),
            "output_width": max(0, min(7680, int(payload.get("output_width", 0) or 0))),
            "output_height": max(0, min(4320, int(payload.get("output_height", 0) or 0))),
            "direct_render": bool(payload.get("direct_render", False)),
            "render_mode": str(payload.get("render_mode", "full-render"))[:24],
            "updated_pixel_ratio": round(max(
                0.0,
                min(100.0, float(payload.get("updated_pixel_ratio", 100) or 0)),
            ), 2),
            "dirty_rect_count": max(
                0,
                min(128, int(payload.get("dirty_rect_count", 0) or 0)),
            ),
            "piezo": self._clean_piezo(payload.get("piezo")),
        }
        temperature = clean["cpu_temp_c"]
        if temperature is not None:
            clean["cpu_temp_c"] = round(max(-20.0, min(150.0, float(temperature))), 1)
        with self._lock:
            self._clients[int(screen_id)] = clean
        return self._with_status(clean, now)

    @staticmethod
    def _clean_piezo(value: Any) -> dict:
        if not isinstance(value, dict):
            return {}
        samples = value.get("samples", [])
        if not isinstance(samples, list):
            samples = []
        samples = [
            max(0, min(4095, int(sample)))
            for sample in samples[-60:]
            if isinstance(sample, (int, float))
        ]
        return {
            "latest": max(0, min(4095, int(value.get("latest", 0) or 0))),
            "peak": max(0, min(4095, int(value.get("peak", 0) or 0))),
            "sample_count": max(0, int(value.get("sample_count", len(samples)) or 0)),
            "hit_count": max(0, int(value.get("hit_count", 0) or 0)),
            "samples": samples,
        }

    def _with_status(self, client: dict, now: float) -> dict:
        result = copy.deepcopy(client)
        age = max(0.0, now - float(result["received_at"]))
        result["last_seen_seconds"] = round(age, 1)
        result["online"] = age <= self.offline_after_seconds
        return result

    def list(self, num_screens: int = 8) -> dict:
        now = time.time()
        with self._lock:
            clients = []
            for screen_id in range(1, int(num_screens) + 1):
                stored = self._clients.get(screen_id)
                if stored:
                    clients.append(self._with_status(stored, now))
                else:
                    clients.append({
                        "screen_id": screen_id,
                        "online": False,
                        "last_seen_seconds": None,
                        "piezo": {},
                    })
        return {
            "offline_after_seconds": self.offline_after_seconds,
            "online_count": sum(1 for client in clients if client["online"]),
            "clients": clients,
        }
