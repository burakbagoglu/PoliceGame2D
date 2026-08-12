"""Validation and atomic persistence for dashboard-managed client settings."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Dict


PROFILE_NAMES = {"pi_zero_2w", "balanced", "high"}


def _integer(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    result = int(value)
    if not low <= result <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return result


def _number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not low <= result <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return result


def validate_remote_settings(value: Any) -> Dict[str, Any]:
    """Return only the allowlisted, normalized settings accepted from the server."""
    if not isinstance(value, dict):
        raise ValueError("settings must be an object")

    profile = str(value.get("performance_profile", "pi_zero_2w")).lower()
    if profile not in PROFILE_NAMES:
        raise ValueError("unsupported performance profile")

    clean: Dict[str, Any] = {
        "fps": _integer(value.get("fps", 30), "fps", 15, 60),
        "performance_profile": profile,
        "render_width": _integer(value.get("render_width", 1280), "render_width", 320, 1920),
        "render_height": _integer(value.get("render_height", 720), "render_height", 180, 1080),
        "adaptive_quality": bool(value.get("adaptive_quality", True)),
        "min_fps": _number(value.get("min_fps", 24.0), "min_fps", 15.0, 60.0),
    }

    playarea = value.get("playarea", {})
    if not isinstance(playarea, dict):
        raise ValueError("playarea must be an object")
    mode = str(playarea.get("mode", "manual_px"))
    if mode not in {"manual_px", "physical"}:
        raise ValueError("unsupported playarea mode")
    clean["playarea"] = {
        "enabled": bool(playarea.get("enabled", False)),
        "mode": mode,
        "x": _integer(playarea.get("x", 0), "playarea.x", 0, 7680),
        "y": _integer(playarea.get("y", 0), "playarea.y", 0, 4320),
        "width": _integer(playarea.get("width", 1280), "playarea.width", 1, 7680),
        "height": _integer(playarea.get("height", 720), "playarea.height", 1, 4320),
        "screen_diagonal_in": _number(
            playarea.get("screen_diagonal_in", 24.0),
            "playarea.screen_diagonal_in",
            5.0,
            100.0,
        ),
        "plexi_width_cm": _number(
            playarea.get("plexi_width_cm", 50.0),
            "playarea.plexi_width_cm",
            1.0,
            300.0,
        ),
        "plexi_height_cm": _number(
            playarea.get("plexi_height_cm", 30.0),
            "playarea.plexi_height_cm",
            1.0,
            300.0,
        ),
        "align_x": str(playarea.get("align_x", "center")),
        "align_y": str(playarea.get("align_y", "center")),
        "margin_left_cm": _number(
            playarea.get("margin_left_cm", 0.0),
            "playarea.margin_left_cm",
            0.0,
            300.0,
        ),
        "margin_top_cm": _number(
            playarea.get("margin_top_cm", 0.0),
            "playarea.margin_top_cm",
            0.0,
            300.0,
        ),
    }
    if clean["playarea"]["align_x"] not in {"left", "center", "right", "custom"}:
        raise ValueError("unsupported horizontal alignment")
    if clean["playarea"]["align_y"] not in {"top", "center", "bottom", "custom"}:
        raise ValueError("unsupported vertical alignment")
    return clean


def apply_remote_settings(config_path, payload: Any) -> bool:
    """Apply a newer settings revision atomically; identity/network fields stay untouched."""
    if not isinstance(payload, dict) or not payload.get("changed"):
        return False
    revision = _integer(payload.get("revision", 0), "revision", 1, 2_147_483_647)
    settings = validate_remote_settings(payload.get("settings"))
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as handle:
        current = json.load(handle)
    if not isinstance(current, dict):
        raise ValueError("client config root must be an object")
    if int(current.get("remote_settings_revision", 0) or 0) == revision:
        return False

    updated = dict(current)
    for key in (
        "fps",
        "performance_profile",
        "render_width",
        "render_height",
        "adaptive_quality",
        "min_fps",
    ):
        updated[key] = settings[key]
    existing_playarea = current.get("playarea", {})
    if not isinstance(existing_playarea, dict):
        existing_playarea = {}
    updated["playarea"] = {**existing_playarea, **settings["playarea"]}
    updated["remote_settings_revision"] = revision

    original_mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(updated, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, original_mode)
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
    return True
