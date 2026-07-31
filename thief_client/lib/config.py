"""
Config modülü - JSON dosyasından ayarları okur
"""
import json
import os
from dataclasses import dataclass, field
from typing import Tuple

from lib.playarea import PlayAreaConfig


@dataclass
class GameConfig:
    """Oyun konfigürasyon sınıfı"""
    screen_id: int
    server_url: str
    server_base_url: str
    server_controlled: bool
    poll_interval_ms: int
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
    serial_port: str
    serial_baud: int
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

    # --- Yeni alanlar (kurulum sihirbazı + pleksi/oynanabilir alan) ---
    # Bunlar çözünürlükten bağımsız çalışsın diye yüzde/oran olarak tutulur;
    # runtime'da oynanabilir alan boyutuna göre piksele çevrilir.
    installed: bool = False
    playarea: PlayAreaConfig = field(default_factory=PlayAreaConfig)
    thief_ground_pct: float = 95.0   # Zemin çizgisi (oynanabilir alan yüksekliğinin %'si)
    band_center_pct: float = 50.0    # Band merkezi (oynanabilir alan genişliğinin %'si)
    band_width_px: int = 120         # Band genişliği (piksel)
    spawn_margin_px: int = 0         # 0 = otomatik (sprite boyutuna göre)

    # --- Pi client performans profili ---
    performance_profile: str = "pi_zero_2w"
    render_width: int = 1280
    render_height: int = 720
    adaptive_quality: bool = True
    min_fps: float = 24.0

    @staticmethod
    def read_raw(filepath: str) -> dict:
        """JSON dosyasını ham sözlük olarak oku (sihirbaz/kaydetme için)."""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Config dosyası bulunamadı: {filepath}")
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    @classmethod
    def from_file(cls, filepath: str) -> "GameConfig":
        """JSON dosyasından config oku"""
        return cls.from_dict(cls.read_raw(filepath))

    @classmethod
    def from_dict(cls, data: dict) -> "GameConfig":
        """Ham sözlükten config oluştur (env override'ları uygulanır)."""

        def env_bool(name: str, default: bool) -> bool:
            value = os.environ.get(name)
            if value is None:
                return default
            return value.lower() in ("1", "true", "yes")

        # server_base_url: server_url'den türet (yoksa)
        server_url = os.environ.get(
            "THIEF_SERVER_URL",
            data.get("server_url", "http://192.168.1.10:8078/event"),
        )
        server_base_url = os.environ.get("THIEF_SERVER_BASE_URL", data.get("server_base_url", ""))
        if not server_base_url:
            # server_url'den base URL çıkar
            # "http://192.168.1.10:8078/event" → "http://192.168.1.10:8078"
            parts = server_url.rsplit("/", 1)
            server_base_url = parts[0] if len(parts) > 1 else server_url
        elif "THIEF_SERVER_URL" not in os.environ:
            server_url = f"{server_base_url.rstrip('/')}/event"

        # Çözünürlükten bağımsız varsayılanlar (eski configlerden türet)
        screen_w = data.get("screen_width", 1920)
        screen_h = data.get("screen_height", 1080)
        thief_y = data.get("thief_y", 980)
        band_x_min = data.get("band_x_min", 900)
        band_x_max = data.get("band_x_max", 1020)

        default_ground_pct = min(100.0, round(thief_y / screen_h * 100, 1)) if screen_h else 95.0
        default_band_center = (
            round((band_x_min + band_x_max) / 2 / screen_w * 100, 1) if screen_w else 50.0
        )
        default_band_width = max(10, band_x_max - band_x_min)

        profile = str(os.environ.get(
            "THIEF_PERFORMANCE_PROFILE",
            data.get("performance_profile", "pi_zero_2w"),
        )).lower()
        profile_defaults = {
            "pi_zero_2w": (1280, 720, 24.0),
            "balanced": (1600, 900, 26.0),
            "high": (1920, 1080, 28.0),
        }
        if profile not in profile_defaults:
            profile = "pi_zero_2w"
        default_render_w, default_render_h, default_min_fps = profile_defaults[profile]
        render_width = max(320, min(1920, int(data.get("render_width", default_render_w))))
        render_height = max(180, min(1080, int(data.get("render_height", default_render_h))))

        return cls(
            screen_id=int(os.environ.get("THIEF_SCREEN_ID", data.get("screen_id", 1))),
            server_url=server_url,
            server_base_url=server_base_url,
            server_controlled=data.get("server_controlled", True),
            poll_interval_ms=data.get("poll_interval_ms", 500),
            fps=data.get("fps", 30),
            thief_speed_px_s=data.get("thief_speed_px_s", 360),
            spawn_x=data.get("spawn_x", 1920),
            reset_x=data.get("reset_x", -200),
            random_direction=data.get("random_direction", False),
            band_enabled=data.get("band_enabled", True),
            band_x_min=band_x_min,
            band_x_max=band_x_max,
            hit_cooldown_ms=data.get("hit_cooldown_ms", 200),
            fullscreen=env_bool("THIEF_FULLSCREEN", data.get("fullscreen", True)),
            serial_port=os.environ.get("THIEF_SERIAL_PORT", data.get("serial_port", "/dev/ttyUSB0")),
            serial_baud=data.get("serial_baud", 9600),
            screen_width=screen_w,
            screen_height=screen_h,
            thief_scale=data.get("thief_scale", 4),
            thief_y=thief_y,
            anim_fps=data.get("anim_fps", 12),
            band_color=tuple(data.get("band_color", [255, 255, 0, 80])),
            shadow_enabled=data.get("shadow_enabled", True),
            shadow_alpha=data.get("shadow_alpha", 80),
            shadow_scale_x=data.get("shadow_scale_x", 1.0),
            shadow_scale_y=data.get("shadow_scale_y", 0.3),
            shadow_offset_y=data.get("shadow_offset_y", 5),
            debug=env_bool("THIEF_DEBUG", data.get("debug", False)),
            installed=bool(data.get("installed", False)),
            playarea=PlayAreaConfig.from_dict(data.get("playarea")),
            thief_ground_pct=float(data.get("thief_ground_pct", default_ground_pct)),
            band_center_pct=float(data.get("band_center_pct", default_band_center)),
            band_width_px=int(data.get("band_width_px", default_band_width)),
            spawn_margin_px=int(data.get("spawn_margin_px", 0)),
            performance_profile=profile,
            render_width=render_width,
            render_height=render_height,
            adaptive_quality=env_bool("THIEF_ADAPTIVE_QUALITY", data.get("adaptive_quality", True)),
            min_fps=max(15.0, min(60.0, float(data.get("min_fps", default_min_fps)))),
        )

    @property
    def band_width(self) -> int:
        """Hedef bandının genişliği"""
        return self.band_x_max - self.band_x_min

    @property
    def band_center(self) -> int:
        """Hedef bandının merkezi"""
        return (self.band_x_min + self.band_x_max) // 2
