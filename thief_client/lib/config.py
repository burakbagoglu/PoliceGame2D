"""
Config modülü - JSON dosyasından ayarları okur
"""
import json
import os
from dataclasses import dataclass
from typing import Tuple, Optional


@dataclass
class GameConfig:
    """Oyun konfigürasyon sınıfı"""
    screen_id: int
    server_url: str
    fps: int
    thief_speed_px_s: float
    spawn_x: int
    reset_x: int
    random_direction: bool  # False = sadece sağdan, True = rastgele yön
    band_enabled: bool  # False = ekrana vur direkt düşsün
    band_x_min: int
    band_x_max: int
    hit_cooldown_ms: int
    fullscreen: bool
    gpio_pin: int
    debounce_ms: int
    screen_width: int
    screen_height: int
    thief_scale: int
    thief_y: int
    anim_fps: int
    band_color: Tuple[int, int, int, int]
    shadow_enabled: bool
    shadow_alpha: int
    shadow_scale_x: float
    shadow_scale_y: float
    shadow_offset_y: int
    debug: bool
    
    @classmethod
    def from_file(cls, filepath: str) -> "GameConfig":
        """JSON dosyasından config oku"""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Config dosyası bulunamadı: {filepath}")
        
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        return cls(
            screen_id=data.get("screen_id", 1),
            server_url=data.get("server_url", "http://192.168.1.10:8000/event"),
            fps=data.get("fps", 30),
            thief_speed_px_s=data.get("thief_speed_px_s", 360),
            spawn_x=data.get("spawn_x", 1920),
            reset_x=data.get("reset_x", -200),
            random_direction=data.get("random_direction", False),
            band_enabled=data.get("band_enabled", True),
            band_x_min=data.get("band_x_min", 900),
            band_x_max=data.get("band_x_max", 1020),
            hit_cooldown_ms=data.get("hit_cooldown_ms", 200),
            fullscreen=data.get("fullscreen", True),
            gpio_pin=data.get("gpio_pin", 17),
            debounce_ms=data.get("debounce_ms", 200),
            screen_width=data.get("screen_width", 1920),
            screen_height=data.get("screen_height", 1080),
            thief_scale=data.get("thief_scale", 4),
            thief_y=data.get("thief_y", 980),
            anim_fps=data.get("anim_fps", 12),
            band_color=tuple(data.get("band_color", [255, 255, 0, 80])),
            shadow_enabled=data.get("shadow_enabled", True),
            shadow_alpha=data.get("shadow_alpha", 80),
            shadow_scale_x=data.get("shadow_scale_x", 1.0),
            shadow_scale_y=data.get("shadow_scale_y", 0.3),
            shadow_offset_y=data.get("shadow_offset_y", 5),
            debug=data.get("debug", False),
        )
    
    @property
    def band_width(self) -> int:
        """Hedef bandının genişliği"""
        return self.band_x_max - self.band_x_min
    
    @property
    def band_center(self) -> int:
        """Hedef bandının merkezi"""
        return (self.band_x_min + self.band_x_max) // 2
