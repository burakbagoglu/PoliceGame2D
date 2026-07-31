"""Server sahne belgelerini düşük maliyetli Pygame çizimlerine dönüştürür."""

from __future__ import annotations

import hashlib
import math
import os
import random
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

import pygame


def parse_color(value: Any, fallback=(255, 255, 255)) -> Tuple[int, ...]:
    if value == "transparent":
        return (0, 0, 0, 0)
    if isinstance(value, (list, tuple)) and len(value) in (3, 4):
        return tuple(max(0, min(255, int(item))) for item in value)
    if isinstance(value, str) and value.startswith("#"):
        raw = value[1:]
        if len(raw) in (6, 8):
            try:
                parts = tuple(int(raw[index:index + 2], 16) for index in range(0, len(raw), 2))
                return parts
            except ValueError:
                pass
    return fallback


class SceneRenderer:
    """Bir sahne dokümanını client oynanabilir alanına ölçekleyerek çizer."""

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.document: Optional[dict] = None
        self.version: Optional[str] = None
        self.preview_scene: Optional[str] = None
        self._scale_x = 1.0
        self._scale_y = 1.0
        self._base_width = 1920.0
        self._base_height = 1080.0
        self._font_cache: Dict[Tuple[int, bool], pygame.font.Font] = {}
        self._surface_cache = OrderedDict()
        self._surface_cache_bytes = 0
        self._surface_cache_limit = 48 * 1024 * 1024
        self._sprite_cache: Dict[str, pygame.Surface] = {}
        self._asset_paths: Dict[str, str] = {}
        self._active_token: Optional[str] = None
        self._scene_started_at = time.monotonic()
        self.quality_level = "high"
        self._compiled_scenes: Dict[str, dict] = {}
        self._compiled_rules = []

    @property
    def ready(self) -> bool:
        return bool(self.document and self.document.get("scenes"))

    def has_scene(self, scene_id: str) -> bool:
        return bool(self.ready and scene_id in self.document.get("scenes", {}))

    def apply(
        self,
        version: str,
        document: dict,
        asset_paths: Optional[Dict[str, str]] = None,
        preview_scene: Optional[str] = None,
    ):
        canvas = document.get("canvas", {})
        base_width = max(1, float(canvas.get("width", 1920)))
        base_height = max(1, float(canvas.get("height", 1080)))
        self.document = document
        self.version = version
        self.preview_scene = preview_scene
        self._base_width = base_width
        self._base_height = base_height
        self._scale_x = self.width / base_width
        self._scale_y = self.height / base_height
        self._asset_paths = dict(asset_paths or {})
        self._surface_cache.clear()
        self._surface_cache_bytes = 0
        self._sprite_cache.clear()
        self._compile_document()
        self._active_token = None

        for name, path in self._asset_paths.items():
            if not path or not os.path.isfile(path):
                continue
            try:
                self._sprite_cache[name] = pygame.image.load(path).convert_alpha()
            except (pygame.error, OSError):
                continue

    def set_quality(self, quality_level: str):
        quality = str(quality_level or "high").lower()
        if quality not in {"minimal", "low", "medium", "high"}:
            quality = "low"
        if quality != self.quality_level:
            self.quality_level = quality
            self._surface_cache.clear()
            self._surface_cache_bytes = 0

    def _compile_document(self):
        self._compiled_scenes = {}
        for scene_id, scene in (self.document or {}).get("scenes", {}).items():
            elements = tuple(sorted(scene.get("elements", []), key=lambda item: float(item.get("z", 0))))
            self._compiled_scenes[str(scene_id)] = {
                "scene": scene,
                "elements": elements,
                "folders": {
                    str(item.get("id", "")): item
                    for item in scene.get("layer_groups", [])
                    if isinstance(item, dict)
                },
                "element_map": {
                    str(item.get("id", "")): item
                    for item in elements
                    if isinstance(item, dict)
                },
            }
        self._compiled_rules = tuple(sorted(
            (self.document or {}).get("rules", []),
            key=lambda rule: float(rule.get("priority", 0)),
            reverse=True,
        ))

    def _cache_get(self, key):
        surface = self._surface_cache.get(key)
        if surface is not None:
            self._surface_cache.move_to_end(key)
        return surface

    def _cache_put(self, key, surface: pygame.Surface):
        cost = max(1, surface.get_width() * surface.get_height() * 4)
        previous = self._surface_cache.pop(key, None)
        if previous is not None:
            self._surface_cache_bytes -= previous.get_width() * previous.get_height() * 4
        if cost > self._surface_cache_limit // 2:
            return surface
        self._surface_cache[key] = surface
        self._surface_cache_bytes += cost
        while self._surface_cache and self._surface_cache_bytes > self._surface_cache_limit:
            _, removed = self._surface_cache.popitem(last=False)
            self._surface_cache_bytes -= removed.get_width() * removed.get_height() * 4
        return surface

    def set_active_scene(self, scene_id: str, restart_token: str = ""):
        token = f"{scene_id}:{restart_token}"
        if token != self._active_token:
            self._active_token = token
            self._scene_started_at = time.monotonic()
    def draw(
        self,
        target: pygame.Surface,
        scene_id: str,
        context: Optional[dict] = None,
        restart_token: str = "",
        resolve_rules: bool = True,
    ) -> bool:
        context = context or {}
        if resolve_rules:
            scene_id = self.resolve_scene(scene_id, context)
        if not self.has_scene(scene_id):
            return False

        self.set_active_scene(scene_id, restart_token)
        compiled = self._compiled_scenes.get(scene_id)
        if not compiled:
            return False
        scene = compiled["scene"]
        elapsed = max(0.0, time.monotonic() - self._scene_started_at)
        duration = max(0.1, float(scene.get("duration", 5.0)))
        timeline_elapsed = elapsed % duration if scene.get("loop_timeline") else min(elapsed, duration)
        transition = scene.get("transition", {})
        transition_duration = max(0.0, float(transition.get("duration", 0.0)))
        transition_type = str(transition.get("type", "none"))
        if self.quality_level == "minimal":
            transition_type = "none"
        elif self.quality_level == "low" and transition_type == "zoom":
            transition_type = "fade"
        transition_active = transition_type != "none" and transition_duration > 0 and elapsed < transition_duration
        draw_target = pygame.Surface(target.get_size(), pygame.SRCALPHA) if transition_active else target

        background = scene.get("background", "transparent")
        if background != "transparent":
            draw_target.fill(parse_color(background, (40, 44, 52)))

        folders = compiled["folders"]
        elements = compiled["elements"]
        element_map = compiled["element_map"]
        for element in elements:
            folder = folders.get(str(element.get("folder_id", "")), {})
            if folder.get("hidden"):
                continue
            if folder:
                element = dict(element)
                element["opacity"] = float(element.get("opacity", 1.0)) * float(folder.get("opacity", 1.0))
                if str(element.get("blend_mode", "normal")) == "normal":
                    element["blend_mode"] = str(folder.get("blend_mode", "normal"))
            if element.get("hidden") or not self._conditions_match(element.get("visible_when"), context, scene_id):
                continue
            animated = self._animated_element(element, timeline_elapsed)
            previous_clip = draw_target.get_clip()
            mask_id = str(folder.get("mask_element_id", ""))
            if mask_id and mask_id != str(element.get("id", "")):
                mask_element = element_map.get(mask_id)
                if mask_element:
                    draw_target.set_clip(self._element_rect(mask_element))
            self._draw_element(draw_target, scene_id, animated, context, timeline_elapsed)
            draw_target.set_clip(previous_clip)

        if transition_active:
            self._composite_transition(target, draw_target, transition_type, elapsed / transition_duration)
        return True

    def resolve_scene(self, fallback_scene: str, context: dict) -> str:
        """Öncelikli event kurallarından ilk eşleşen özel sahneyi seç."""
        if not self.ready:
            return fallback_scene
        for rule in self._compiled_rules:
            scene_id = str(rule.get("scene_id", ""))
            if not rule.get("enabled", True) or not self.has_scene(scene_id):
                continue
            if self._rule_matches(rule, context, fallback_scene):
                return scene_id
        return fallback_scene

    @staticmethod
    def _rule_matches(rule: dict, context: dict, fallback_scene: str) -> bool:
        event = str(rule.get("event", "always"))
        value = float(rule.get("value", 0) or 0)
        if event == "always":
            return True
        if event == "win":
            return fallback_scene == "win"
        if event == "lose":
            return fallback_scene == "lose"
        if event == "game_active":
            return bool(context.get("game_active")) == bool(rule.get("boolean", True))
        if event == "screen_complete":
            return bool(context.get("screen_complete")) == bool(rule.get("boolean", True))
        mapping = {
            "score_gte": (float(context.get("score", 0)), lambda a, b: a >= b),
            "score_lte": (float(context.get("score", 0)), lambda a, b: a <= b),
            "time_lte": (float(context.get("remaining_seconds", 0)), lambda a, b: a <= b),
            "time_gte": (float(context.get("remaining_seconds", 0)), lambda a, b: a >= b),
            "hit_gte": (float(context.get("hit_count", 0)), lambda a, b: a >= b),
            "combo_gte": (float(context.get("combo", 0)), lambda a, b: a >= b),
        }
        current = mapping.get(event)
        return bool(current and current[1](current[0], value))

    def _conditions_match(self, conditions, context: dict, fallback_scene: str) -> bool:
        if not conditions:
            return True
        if isinstance(conditions, dict):
            conditions = [conditions]
        return all(self._rule_matches(condition, context, fallback_scene) for condition in conditions)

    @staticmethod
    def _ease(value: float, easing: str) -> float:
        value = max(0.0, min(1.0, value))
        if easing == "ease_in":
            return value * value
        if easing == "ease_out":
            return 1.0 - (1.0 - value) * (1.0 - value)
        if easing == "ease_in_out":
            return 2 * value * value if value < 0.5 else 1 - pow(-2 * value + 2, 2) / 2
        return value

    def _animated_element(self, element: dict, elapsed: float) -> dict:
        frames = element.get("keyframes") or []
        if not frames:
            return element
        animated = dict(element)
        frames = sorted(frames, key=lambda frame: float(frame.get("time", 0)))
        before = {"time": 0.0}
        after = frames[0]
        for frame in frames:
            if float(frame.get("time", 0)) <= elapsed:
                before = frame
            if float(frame.get("time", 0)) >= elapsed:
                after = frame
                break
        else:
            after = before
        start = float(before.get("time", 0))
        end = float(after.get("time", start))
        progress = 1.0 if end <= start else (elapsed - start) / (end - start)
        progress = self._ease(progress, str(after.get("easing", "linear")))
        for key in ("x", "y", "width", "height", "opacity", "rotation"):
            base = float(element.get(key, 0 if key != "opacity" else 1))
            start_value = float(before.get(key, base))
            end_value = float(after.get(key, start_value))
            animated[key] = start_value + (end_value - start_value) * progress
        return animated

    @staticmethod
    def _composite_transition(target, layer, transition_type: str, progress: float):
        progress = max(0.0, min(1.0, progress))
        if transition_type == "fade":
            layer.set_alpha(round(progress * 255))
            target.blit(layer, (0, 0))
        elif transition_type in ("slide_left", "slide_right"):
            direction = 1 if transition_type == "slide_left" else -1
            target.blit(layer, (round((1.0 - progress) * target.get_width() * direction), 0))
        elif transition_type == "zoom":
            scale = 0.78 + progress * 0.22
            size = (max(1, round(layer.get_width() * scale)), max(1, round(layer.get_height() * scale)))
            scaled = pygame.transform.scale(layer, size)
            target.blit(scaled, scaled.get_rect(center=target.get_rect().center))
        else:
            target.blit(layer, (0, 0))

    def _draw_element(
        self,
        target: pygame.Surface,
        scene_id: str,
        element: dict,
        context: dict,
        elapsed: float,
    ):
        opacity = max(0.0, min(1.0, float(element.get("opacity", 1.0))))
        effect = str(element.get("effect", "none"))
        speed = max(0.1, float(element.get("effect_speed", 1.0)))
        phase = elapsed * speed
        scale = 1.0
        offset_x = 0.0
        offset_y = 0.0

        if effect == "pulse":
            scale = 1.0 + math.sin(phase * 5.0) * 0.055
        elif effect == "scale":
            progress = min(1.0, elapsed * speed / 0.92)
            eased = 1.0 - pow(1.0 - progress, 3)
            scale = 0.62 + eased * 0.5 + math.sin(progress * math.pi * 4) * 0.025
        elif effect == "fade":
            opacity *= min(1.0, elapsed * speed * 2.5)
        elif effect == "shake":
            offset_x = math.sin(phase * 31.0) * 9.0
            offset_y = math.cos(phase * 27.0) * 5.0
        elif effect == "blink":
            opacity *= 1.0 if math.sin(phase * 6.0) > 0 else 0.25
        elif effect == "float":
            offset_y = math.sin(phase * 3.0) * 18.0
        elif effect == "flash":
            opacity *= 0.45 + (math.sin(phase * 14.0) + 1.0) * 0.275
        elif effect == "camera_shake":
            offset_x = math.sin(phase * 43.0) * 13.0
            offset_y = math.cos(phase * 37.0) * 9.0

        rect = self._element_rect(element)
        rect.x += round(offset_x * self._scale_x)
        rect.y += round(offset_y * self._scale_y)

        element_type = element.get("type")
        if element_type == "confetti":
            self._draw_confetti(target, scene_id, element, rect, elapsed, opacity)
            return

        surface = self._get_element_surface(element, context, rect.size, elapsed)
        if surface is None:
            return
        surface = self._apply_filters(surface, element)
        if effect == "glow":
            self._draw_glow(target, surface, rect, element, phase)

        if scale != 1.0:
            scaled_size = (
                max(1, int(surface.get_width() * scale)),
                max(1, int(surface.get_height() * scale)),
            )
            surface = pygame.transform.scale(surface, scaled_size)
            draw_rect = surface.get_rect(center=rect.center)
        else:
            draw_rect = rect

        rotation = float(element.get("rotation", 0) or 0)
        if rotation:
            center = draw_rect.center
            rotation_key = ("rotation", id(surface), round(rotation, 2))
            rotated = self._cache_get(rotation_key)
            if rotated is None:
                rotated = self._cache_put(rotation_key, pygame.transform.rotate(surface, -rotation))
            surface = rotated
            draw_rect = surface.get_rect(center=center)

        if opacity < 0.999:
            surface = surface.copy()
            surface.set_alpha(round(opacity * 255))
        self._draw_shadow(target, surface, draw_rect, element)
        blend_flags = {
            "add": pygame.BLEND_RGBA_ADD,
            "multiply": pygame.BLEND_RGBA_MULT,
            "screen": pygame.BLEND_RGBA_ADD,
        }
        target.blit(surface, draw_rect, special_flags=blend_flags.get(str(element.get("blend_mode", "normal")), 0))

    def _apply_filters(self, surface: pygame.Surface, element: dict) -> pygame.Surface:
        filters = element.get("filters") if isinstance(element.get("filters"), dict) else {}
        if self.quality_level == "minimal":
            return surface
        brightness = max(0.0, min(2.0, float(filters.get("brightness", 1.0))))
        contrast = max(0.0, min(2.0, float(filters.get("contrast", 1.0))))
        saturation = max(0.0, min(2.0, float(filters.get("saturation", 1.0))))
        blur = max(0.0, min(30.0, float(filters.get("blur", 0.0))))
        if self.quality_level == "low":
            blur = 0.0
        elif self.quality_level == "medium":
            blur = min(8.0, blur)
        if abs(brightness - 1.0) < 0.01 and abs(contrast - 1.0) < 0.01 and abs(saturation - 1.0) < 0.01 and blur < 0.5:
            return surface
        key = ("filter", id(surface), self.quality_level, round(brightness, 2), round(contrast, 2), round(saturation, 2), round(blur, 1))
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        filtered = surface.copy()
        if abs(saturation - 1.0) >= 0.01:
            gray = pygame.transform.grayscale(filtered)
            if saturation < 1.0:
                gray.set_alpha(round((1.0 - saturation) * 255))
                filtered.blit(gray, (0, 0))
            else:
                filtered.blit(filtered, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)
                filtered.set_alpha(255)
        if abs(contrast - 1.0) >= 0.01:
            try:
                pixels = pygame.surfarray.pixels3d(filtered)
                pixels[:] = ((pixels.astype("float32") - 128.0) * contrast + 128.0).clip(0, 255).astype("uint8")
                del pixels
            except (ImportError, ValueError, pygame.error):
                pass
        if abs(brightness - 1.0) >= 0.01:
            level = max(0, min(255, round(255 * brightness)))
            filtered.fill((level, level, level, 255), special_flags=pygame.BLEND_RGBA_MULT)
        if blur >= 0.5:
            divisor = max(2, min(8, round(1 + blur / 4)))
            small = (max(1, filtered.get_width() // divisor), max(1, filtered.get_height() // divisor))
            filtered = pygame.transform.scale(pygame.transform.scale(filtered, small), surface.get_size())
        return self._cache_put(key, filtered)

    def _draw_shadow(self, target: pygame.Surface, surface: pygame.Surface, rect: pygame.Rect, element: dict):
        shadow = element.get("shadow")
        if self.quality_level == "minimal" or not isinstance(shadow, dict) or not shadow.get("enabled", False):
            return
        color = parse_color(shadow.get("color", "#00000088"), (0, 0, 0, 136))
        blur = max(0, min(30, int(shadow.get("blur", 0) or 0)))
        if self.quality_level == "low":
            blur = 0
        elif self.quality_level == "medium":
            blur = min(8, blur)
        key = ("shadow", id(surface), color, blur, self.quality_level)
        layer = self._cache_get(key)
        if layer is None:
            layer = surface.copy()
            layer.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
            if blur:
                divisor = max(2, min(8, 1 + blur // 4))
                small = (max(1, layer.get_width() // divisor), max(1, layer.get_height() // divisor))
                layer = pygame.transform.scale(pygame.transform.scale(layer, small), surface.get_size())
            layer = self._cache_put(key, layer)
        target.blit(layer, rect.move(
            round(float(shadow.get("x", 8)) * self._scale_x),
            round(float(shadow.get("y", 8)) * self._scale_y),
        ))
    def _element_rect(self, element: dict) -> pygame.Rect:
        x, width = self._axis_layout(
            float(element.get("x", 0)),
            float(element.get("width", 1)),
            self._base_width,
            self.width,
            self._scale_x,
            str(element.get("anchor_x", "scale")),
        )
        y, height = self._axis_layout(
            float(element.get("y", 0)),
            float(element.get("height", 1)),
            self._base_height,
            self.height,
            self._scale_y,
            str(element.get("anchor_y", "scale")),
        )
        return pygame.Rect(round(x), round(y), max(1, round(width)), max(1, round(height)))

    def _axis_layout(
        self,
        position: float,
        size: float,
        base_size: float,
        target_size: float,
        axis_scale: float,
        anchor: str,
    ) -> Tuple[float, float]:
        if anchor == "scale":
            return position * axis_scale, size * axis_scale

        uniform_scale = min(self._scale_x, self._scale_y)
        scaled_size = size * uniform_scale
        if anchor in ("left", "top"):
            return position * uniform_scale, scaled_size
        if anchor == "center":
            center_offset = position + size / 2.0 - base_size / 2.0
            return target_size / 2.0 + center_offset * uniform_scale - scaled_size / 2.0, scaled_size
        if anchor in ("right", "bottom"):
            trailing_margin = base_size - position - size
            return target_size - trailing_margin * uniform_scale - scaled_size, scaled_size
        if anchor == "stretch":
            leading = position * uniform_scale
            trailing = (base_size - position - size) * uniform_scale
            return leading, max(1.0, target_size - leading - trailing)
        return position * axis_scale, size * axis_scale

    def _get_element_surface(
        self,
        element: dict,
        context: dict,
        size: Tuple[int, int],
        elapsed: float = 0.0,
    ) -> Optional[pygame.Surface]:
        element_type = element.get("type")
        if element_type == "rect":
            key = ("rect", self._stable_key(element), size)
            cached = self._cache_get(key)
            if cached is None:
                cached = self._cache_put(key, self._build_rect(element, size))
            return cached
        if element_type == "text":
            text = self._replace_tokens(str(element.get("text", "")), context)
            key = ("text", self._stable_key(element), text, size)
            cached = self._cache_get(key)
            if cached is None:
                cached = self._cache_put(key, self._build_text(element, text, size))
            return cached
        if element_type == "score":
            score = int(context.get("score", 0))
            combo = int(context.get("combo", 0))
            key = ("score", self._stable_key(element), score, combo, size)
            cached = self._cache_get(key)
            if cached is None:
                cached = self._cache_put(key, self._build_score(element, score, combo, size))
            return cached
        if element_type == "sprite":
            name = str(element.get("asset", ""))
            sprite = self._sprite_cache.get(name)
            if sprite is None:
                return self._build_missing_sprite(name, size)
            source = sprite
            frame_index = 0
            sheet = element.get("sprite_sheet")
            if isinstance(sheet, dict):
                columns = max(1, int(sheet.get("columns", 1)))
                rows = max(1, int(sheet.get("rows", 1)))
                total = columns * rows
                start = max(0, min(total - 1, int(sheet.get("start", 0))))
                end = max(start, min(total - 1, int(sheet.get("end", total - 1))))
                frame_count = end - start + 1
                frame_step = int(elapsed * max(0.1, float(sheet.get("fps", 8))))
                frame_index = start + (
                    frame_step % frame_count
                    if sheet.get("loop", True)
                    else min(frame_step, frame_count - 1)
                )
                frame_width = max(1, sprite.get_width() // columns)
                frame_height = max(1, sprite.get_height() // rows)
                frame_rect = pygame.Rect(
                    (frame_index % columns) * frame_width,
                    (frame_index // columns) * frame_height,
                    frame_width,
                    frame_height,
                ).clip(sprite.get_rect())
                source = sprite.subsurface(frame_rect)
            key = ("sprite", name, frame_index, size, str(element.get("color", "")))
            cached = self._cache_get(key)
            if cached is None:
                cached = pygame.transform.scale(source, size)
                tint = element.get("color")
                if tint and str(tint).lower() != "#ffffff":
                    cached = cached.copy()
                    tint_color = parse_color(tint, (255, 255, 255))
                    mixed_tint = tuple(round(255 * 0.55 + channel * 0.45) for channel in tint_color[:3])
                    cached.fill((*mixed_tint, 255), special_flags=pygame.BLEND_RGBA_MULT)
                cached = self._cache_put(key, cached)
            return cached
        return None
    @staticmethod
    def _stable_key(element: dict) -> str:
        serialized = repr(sorted(element.items())).encode("utf-8", "replace")
        return hashlib.sha1(serialized).hexdigest()

    def _font(self, size: int, bold: bool) -> pygame.font.Font:
        key = (max(8, int(size)), bool(bold))
        font = self._font_cache.get(key)
        if font is None:
            font = pygame.font.SysFont(
                "dejavusans,arial,freesans",
                key[0],
                bold=key[1],
            )
            self._font_cache[key] = font
        return font

    def _build_rect(self, element: dict, size: Tuple[int, int]) -> pygame.Surface:
        surface = pygame.Surface(size, pygame.SRCALPHA)
        radius = max(0, round(float(element.get("radius", 0)) * min(self._scale_x, self._scale_y)))
        pygame.draw.rect(
            surface,
            parse_color(element.get("fill"), (255, 255, 255)),
            surface.get_rect(),
            border_radius=radius,
        )
        stroke_width = max(
            0,
            round(float(element.get("stroke_width", 0)) * min(self._scale_x, self._scale_y)),
        )
        if stroke_width:
            pygame.draw.rect(
                surface,
                parse_color(element.get("stroke"), (0, 0, 0)),
                surface.get_rect(),
                width=stroke_width,
                border_radius=radius,
            )
        return surface

    def _build_text(
        self,
        element: dict,
        text: str,
        size: Tuple[int, int],
    ) -> pygame.Surface:
        surface = pygame.Surface(size, pygame.SRCALPHA)
        font_size = max(
            8,
            round(float(element.get("font_size", 64)) * min(self._scale_x, self._scale_y)),
        )
        font = self._font(font_size, element.get("font_weight", "bold") == "bold")
        lines = text.split("\n")
        outline_width = max(
            0,
            round(float(element.get("outline_width", 0)) * min(self._scale_x, self._scale_y)),
        )
        rendered = [
            self._outlined_text(
                font,
                line,
                parse_color(element.get("color"), (255, 255, 255)),
                parse_color(element.get("outline_color"), (0, 0, 0)),
                outline_width,
            )
            for line in lines
        ]
        line_gap = max(0, round(font_size * 0.04))
        total_height = sum(item.get_height() for item in rendered)
        total_height += line_gap * max(0, len(rendered) - 1)
        y = (size[1] - total_height) // 2
        align = element.get("align", "center")
        for line_surface in rendered:
            if align == "left":
                x = 0
            elif align == "right":
                x = size[0] - line_surface.get_width()
            else:
                x = (size[0] - line_surface.get_width()) // 2
            surface.blit(line_surface, (x, y))
            y += line_surface.get_height() + line_gap
        return surface

    @staticmethod
    def _outlined_text(
        font: pygame.font.Font,
        text: str,
        color: Tuple[int, ...],
        outline: Tuple[int, ...],
        width: int,
    ) -> pygame.Surface:
        base = font.render(text, True, color)
        if width <= 0:
            return base
        edge = font.render(text, True, outline)
        surface = pygame.Surface(
            (base.get_width() + width * 2, base.get_height() + width * 2),
            pygame.SRCALPHA,
        )
        for dx, dy in (
            (-width, 0), (width, 0), (0, -width), (0, width),
            (-width, -width), (-width, width), (width, -width), (width, width),
        ):
            surface.blit(edge, (width + dx, width + dy))
        surface.blit(base, (width, width))
        return surface

    def _build_score(
        self,
        element: dict,
        score: int,
        combo: int,
        size: Tuple[int, int],
    ) -> pygame.Surface:
        surface = pygame.Surface(size, pygame.SRCALPHA)
        radius = max(0, round(float(element.get("radius", 24)) * min(self._scale_x, self._scale_y)))
        pygame.draw.rect(
            surface,
            parse_color(element.get("fill"), (255, 224, 56)),
            surface.get_rect(),
            border_radius=radius,
        )
        stroke_width = max(
            1,
            round(float(element.get("stroke_width", 7)) * min(self._scale_x, self._scale_y)),
        )
        pygame.draw.rect(
            surface,
            parse_color(element.get("stroke"), (105, 35, 165)),
            surface.get_rect(),
            width=stroke_width,
            border_radius=radius,
        )
        scale = min(self._scale_x, self._scale_y)
        label_font = self._font(max(12, round(28 * scale)), True)
        number_font = self._font(
            max(20, round(float(element.get("font_size", 78)) * scale)),
            True,
        )
        label = label_font.render(
            str(element.get("label", "YAKALANAN")),
            True,
            parse_color(element.get("label_color"), (105, 35, 165)),
        )
        number = number_font.render(
            str(score),
            True,
            parse_color(element.get("color"), (236, 45, 76)),
        )
        margin = max(8, round(20 * scale))
        surface.blit(label, (margin, max(6, round(14 * scale))))
        surface.blit(number, (margin, size[1] - number.get_height() - max(4, round(8 * scale))))
        if combo > 1:
            combo_font = self._font(max(11, round(22 * scale)), True)
            combo_text = combo_font.render(f"{combo}x KOMBO", True, (255, 255, 255))
            padding_x = max(5, round(8 * scale))
            padding_y = max(3, round(4 * scale))
            badge = pygame.Rect(
                size[0] - combo_text.get_width() - padding_x * 2 - margin,
                size[1] - combo_text.get_height() - padding_y * 2 - margin,
                combo_text.get_width() + padding_x * 2,
                combo_text.get_height() + padding_y * 2,
            )
            pygame.draw.rect(
                surface,
                parse_color(element.get("color"), (236, 45, 76)),
                badge,
                border_radius=max(4, round(9 * scale)),
            )
            surface.blit(combo_text, (badge.x + padding_x, badge.y + padding_y))
        return surface

    def _build_missing_sprite(self, name: str, size: Tuple[int, int]) -> pygame.Surface:
        key = ("missing", name, size)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        surface = pygame.Surface(size, pygame.SRCALPHA)
        surface.fill((74, 44, 98, 255))
        font = self._font(max(12, min(28, size[1] // 6)), True)
        text = font.render("SPRITE YÜKLENEMEDİ", True, (255, 255, 255))
        surface.blit(text, text.get_rect(center=surface.get_rect().center))
        return self._cache_put(key, surface)
    def _draw_glow(
        self,
        target: pygame.Surface,
        surface: pygame.Surface,
        rect: pygame.Rect,
        element: dict,
        phase: float,
    ):
        glow_color = parse_color(
            element.get("stroke") or element.get("outline_color"),
            (255, 224, 56),
        )
        alpha = round(40 + (math.sin(phase * 4.0) + 1.0) * 30)
        glow = pygame.Surface(
            (rect.width + 24, rect.height + 24),
            pygame.SRCALPHA,
        )
        pygame.draw.rect(
            glow,
            (*glow_color[:3], alpha),
            glow.get_rect(),
            width=10,
            border_radius=20,
        )
        target.blit(glow, (rect.x - 12, rect.y - 12))

    def _draw_confetti(
        self,
        target: pygame.Surface,
        scene_id: str,
        element: dict,
        rect: pygame.Rect,
        elapsed: float,
        opacity: float,
    ):
        colors = (
            (255, 224, 56),
            (236, 45, 76),
            (141, 69, 211),
            (255, 112, 180),
            (255, 255, 255),
            (56, 205, 255),
        )
        quality_cap = {"minimal": 24, "low": 60, "medium": 110, "high": 180}[self.quality_level]
        amount = max(1, min(quality_cap, int(element.get("amount", 70))))
        seed = int(hashlib.sha1(f"{scene_id}:{element.get('id')}".encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        for index in range(amount):
            start_x = rng.random()
            start_y = rng.random()
            speed = rng.uniform(0.16, 0.42)
            sway = rng.uniform(4.0, 16.0)
            width = rng.randint(3, 8)
            height = rng.randint(7, 14)
            progress = (start_y + elapsed * speed) % 1.15
            x = rect.x + int(start_x * rect.width + math.sin(elapsed * 4 + index) * sway)
            y = rect.y + int(progress * rect.height) - height
            color = colors[index % len(colors)]
            if opacity < 1.0:
                particle = pygame.Surface((width, height), pygame.SRCALPHA)
                particle.fill((*color, round(255 * opacity)))
                target.blit(particle, (x, y))
            else:
                pygame.draw.rect(target, color, (x, y, width, height), border_radius=2)

    @staticmethod
    def _replace_tokens(text: str, context: dict) -> str:
        values = {
            "score": context.get("score", 0),
            "combo": context.get("combo", 0),
            "countdown": context.get("countdown", 3),
            "target_score": context.get("target_score", 0),
            "screen_score": context.get("screen_score", 0),
            "screen_target": context.get("screen_target", 0),
            "screen_remaining": context.get("screen_remaining", 0),
            "screen_complete": context.get("screen_complete", False),
            "remaining_time": context.get("remaining_time", "--:--"),
            "screen_id": context.get("screen_id", 0),
        }
        for key, value in values.items():
            text = text.replace("{" + key + "}", str(value))
        return text
