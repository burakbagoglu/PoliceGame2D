#!/usr/bin/env python3
"""
Thief Client - Raspberry Pi Zero 2 W için interaktif hırsız oyunu
Ana giriş noktası. Server kontrollü veya bağımsız çalışabilir.

Oyun, "oynanabilir alan" (pleksi) boyutunda bir ara yüzeye (canvas) çizilir;
bu yüzey ekranın doğru konumuna blit edilir, kalan her yer siyah bar kalır.
Böylece pleksi dışındaki bölgede oyun görünmez ve daha az piksel render edilir.
"""
import sys
import os
import json
import math
import io
import pygame
import time
from collections import deque

# Modül yolunu ekle
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.config import GameConfig
from lib.animation import ThiefAnimator
from lib.hit_input import HitInput, KeyboardHitInput
from lib.net_client import NetClient
from lib.game import GameLogic, GameState
from lib.effects import FloatingText, Particle, Confetti
from lib.scene_renderer import SceneRenderer
from lib.setup_wizard import SetupWizard
import random


class ThiefGame:
    """Ana oyun sınıfı"""

    def __init__(self, config_path: str = "config.json"):
        """
        Args:
            config_path: Konfigürasyon dosyası yolu
        """
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.config_file = os.path.join(self.script_dir, config_path)

        # Ham config (sihirbaz bunun üzerinde çalışır ve kaydeder)
        self.raw = GameConfig.read_raw(self.config_file)

        # Pygame başlat
        pygame.init()
        pygame.mouse.set_visible(False)

        # Ekran oluştur (fiziksel ekran boyutu)
        fullscreen = self.raw.get("fullscreen", True)
        if os.environ.get("THIEF_FULLSCREEN", "").lower() in ("0", "false", "no"):
            fullscreen = False
        if fullscreen:
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            info = pygame.display.Info()
            self.screen_width = info.current_w
            self.screen_height = info.current_h
        else:
            self.screen_width = self.raw.get("screen_width", 1920)
            self.screen_height = self.raw.get("screen_height", 1080)
            self.screen = pygame.display.set_mode(
                (self.screen_width, self.screen_height)
            )

        pygame.display.set_caption(
            f"Hırsız Oyunu - Ekran {self.raw.get('screen_id', 1)}"
        )

        # Sprite yolları
        self.sprite_path, self.fall_sprite_path, self.death_sprite_path = (
            self._resolve_sprite_paths()
        )

        # İlk açılış sihirbazı (config kurulu değilse)
        if not self.raw.get("installed", False):
            self._run_setup(force=False)

        # Config nesnesi
        self.config = GameConfig.from_dict(self.raw)

        # Saat
        self.clock = pygame.time.Clock()
        self._frame_times_ms = deque(maxlen=180)
        self._draw_times_ms = deque(maxlen=180)
        self._blit_times_ms = deque(maxlen=180)
        self._flip_times_ms = deque(maxlen=180)
        self.frame_time_p95_ms = 0.0
        self.draw_time_p95_ms = 0.0
        self.blit_time_p95_ms = 0.0
        self.flip_time_p95_ms = 0.0
        self.performance_profile = self.config.performance_profile
        self._quality_order = ["minimal", "low", "medium", "high"]
        self._base_quality = {
            "pi_zero_2w": "low",
            "balanced": "medium",
            "high": "high",
        }.get(self.performance_profile, "low")
        self.quality_level = self._base_quality
        self._last_quality_evaluation = time.monotonic()
        self._quality_recovery_since = None

        # Fontlar (bir kez)
        self.font = pygame.font.Font(None, 72)
        self.small_font = pygame.font.Font(None, 36)
        self.idle_font = pygame.font.Font(None, 48)

        # Renkler
        self.bg_color = (40, 44, 52)
        self.text_color = (255, 255, 255)
        self.idle_text_color = (150, 150, 150)
        self.idle_surface = self.idle_font.render(
            "Oyun bekleniyor...", True, self.idle_text_color
        )

        # Hit input (debug modunda klavye kullan)
        if self.config.debug:
            self.hit_input = KeyboardHitInput(debug=True)
        else:
            self.hit_input = HitInput(
                port=self.config.serial_port,
                baud=self.config.serial_baud,
                debug=self.config.debug,
            )
        self.hit_input.start()

        # Network client
        self.net_client = NetClient(
            server_url=self.config.server_url,
            server_base_url=self.config.server_base_url,
            screen_id=self.config.screen_id,
            poll_interval_ms=self.config.poll_interval_ms,
            scene_cache_dir=os.path.join(self.script_dir, "scene_cache"),
            debug=self.config.debug,
            telemetry_provider=self._build_telemetry,
        )
        self.net_client.start()
        self._last_scene_payload = None

        # Çalışma bayrakları
        self.running = True
        self.reopen_setup = False

        # Config'e bağlı her şeyi kur (oynanabilir alan, animatör, oyun, yüzeyler)
        self._apply_config()

    # ---------------------------------------------------------------- setup
    @staticmethod
    def _read_process_memory_mb() -> float:
        """Linux /proc üzerinden ek bağımlılık olmadan RSS ölç."""
        try:
            with open("/proc/self/status", "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1024.0
        except (OSError, ValueError, IndexError):
            pass
        return 0.0

    @staticmethod
    def _read_cpu_temperature():
        try:
            with open(
                "/sys/class/thermal/thermal_zone0/temp", "r", encoding="ascii"
            ) as handle:
                value = float(handle.read().strip())
            return value / 1000.0 if value > 200 else value
        except (OSError, ValueError):
            return None

    def _build_telemetry(self) -> dict:
        return {
            "fps": float(self.clock.get_fps()),
            "memory_mb": self._read_process_memory_mb(),
            "cpu_temp_c": self._read_cpu_temperature(),
            "serial_connected": bool(getattr(self.hit_input, "connected", False)),
            "piezo": self.hit_input.get_telemetry(),
            "frame_time_p95_ms": round(self.frame_time_p95_ms, 1),
            "draw_time_p95_ms": round(self.draw_time_p95_ms, 1),
            "blit_time_p95_ms": round(self.blit_time_p95_ms, 1),
            "flip_time_p95_ms": round(self.flip_time_p95_ms, 1),
            "performance_profile": self.performance_profile,
            "quality_level": self.quality_level,
            "render_width": getattr(self, "view_w", self.config.render_width),
            "render_height": getattr(self, "view_h", self.config.render_height),
            "output_width": getattr(self, "output_view_w", self.screen_width),
            "output_height": getattr(self, "output_view_h", self.screen_height),
            "direct_render": bool(getattr(self, "direct_render", False)),
            "app_version": "scene-engine-v7-direct-render",
        }
    def _set_quality_level(self, quality_level: str):
        if quality_level == self.quality_level:
            return
        self.quality_level = quality_level
        if hasattr(self, "scene_renderer"):
            self.scene_renderer.set_quality(quality_level)
        if self.config.debug:
            print(f"[Performans] Kalite seviyesi: {quality_level}")

    @staticmethod
    def _p95(samples) -> float:
        if not samples:
            return 0.0
        ordered = sorted(samples)
        index = max(
            0,
            min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1),
        )
        return ordered[index]

    def _record_frame_performance(
        self,
        frame_ms: float,
        draw_ms: float,
        blit_ms: float,
        flip_ms: float,
    ):
        self._frame_times_ms.append(max(0.0, float(frame_ms)))
        self._draw_times_ms.append(max(0.0, float(draw_ms)))
        self._blit_times_ms.append(max(0.0, float(blit_ms)))
        self._flip_times_ms.append(max(0.0, float(flip_ms)))
        if len(self._frame_times_ms) >= 20:
            self.frame_time_p95_ms = self._p95(self._frame_times_ms)
            self.draw_time_p95_ms = self._p95(self._draw_times_ms)
            self.blit_time_p95_ms = self._p95(self._blit_times_ms)
            self.flip_time_p95_ms = self._p95(self._flip_times_ms)
        if not self.config.adaptive_quality or len(self._frame_times_ms) < 30:
            return
        now = time.monotonic()
        if now - self._last_quality_evaluation < 3.0:
            return
        self._last_quality_evaluation = now
        fps = float(self.clock.get_fps())
        temperature = self._read_cpu_temperature()
        frame_budget_ms = 1000.0 / max(15.0, self.config.min_fps)
        overloaded = (
            self.frame_time_p95_ms > frame_budget_ms
            or (fps > 1.0 and fps < self.config.min_fps)
            or (temperature is not None and temperature >= 78.0)
        )
        current_index = self._quality_order.index(self.quality_level)
        base_index = self._quality_order.index(self._base_quality)
        if overloaded:
            self._quality_recovery_since = None
            if current_index > 0:
                self._set_quality_level(self._quality_order[current_index - 1])
            return
        healthy = (
            self.frame_time_p95_ms < frame_budget_ms * 0.65
            and (fps <= 1.0 or fps >= self.config.min_fps + 2.0)
            and (temperature is None or temperature < 72.0)
        )
        if not healthy or current_index >= base_index:
            self._quality_recovery_since = None
            return
        self._quality_recovery_since = self._quality_recovery_since or now
        if now - self._quality_recovery_since >= 30.0:
            self._set_quality_level(self._quality_order[current_index + 1])
            self._quality_recovery_since = None

    def _effect_budget(self, high: int, medium: int, low: int, minimal: int) -> int:
        return {
            "high": high,
            "medium": medium,
            "low": low,
            "minimal": minimal,
        }[self.quality_level]

    def _resolve_sprite_paths(self):
        """Sprite dosya yollarını çöz."""
        script_dir = self.script_dir

        sprite_path = os.path.join(
            script_dir, "..", "thief-1.0", "PNG", "48x64_scale2x", "thief.png"
        )
        if not os.path.exists(sprite_path):
            sprite_path = os.path.join(script_dir, "assets", "thief.png")
        if not os.path.exists(sprite_path):
            print(f"[HATA] Sprite dosyası bulunamadı: {sprite_path}")
            print("Lütfen thief.png dosyasını assets/ klasörüne kopyalayın")
            sys.exit(1)

        fall_sprite_path = os.path.join(script_dir, "assets", "thief_with_fall.png")
        if not os.path.exists(fall_sprite_path):
            fall_sprite_path = os.path.join(script_dir, "..", "thief-1.0", "thief.png")
        if not os.path.exists(fall_sprite_path):
            fall_sprite_path = None

        death_sprite_path = os.path.join(script_dir, "..", "thief-1.0", "deadthief.png")
        if not os.path.exists(death_sprite_path):
            death_sprite_path = os.path.join(script_dir, "assets", "deadthief.png")
        if not os.path.exists(death_sprite_path):
            death_sprite_path = None

        return sprite_path, fall_sprite_path, death_sprite_path

    def _run_setup(self, force: bool):
        """Kurulum sihirbazını çalıştır ve sonucu kaydet."""
        wizard = SetupWizard(
            self.screen,
            self.raw,
            self.screen_width,
            self.screen_height,
            sprite_path=self.sprite_path,
        )
        result = wizard.run()
        if result is not None:
            self.raw = result
            self._save_config()
            print("[Setup] Ayarlar kaydedildi.")
            return True
        if force:
            print("[Setup] İptal edildi (değişiklik kaydedilmedi).")
        return False

    def _save_config(self):
        """Ham config'i dosyaya atomik olarak yaz (güç kesintisinde bozulmasın)."""
        import tempfile
        try:
            dir_ = os.path.dirname(self.config_file) or "."
            fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self.raw, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.config_file)
            except BaseException:
                if os.path.exists(tmp):
                    os.remove(tmp)
                raise
        except OSError as e:
            print(f"[HATA] Config kaydedilemedi: {e}")

    def _apply_config(self):
        """Config'e bağlı tüm runtime nesnelerini (yeniden) oluştur."""
        cfg = self.config

        # --- Oynanabilir alan (pleksi) ---
        rect = cfg.playarea.compute(self.screen_width, self.screen_height)
        self.view_x, self.view_y = rect.x, rect.y
        self.output_view_w, self.output_view_h = rect.w, rect.h
        self.render_ratio = min(
            1.0,
            cfg.render_width / max(1, self.output_view_w),
            cfg.render_height / max(1, self.output_view_h),
        )
        self.view_w = max(320, round(self.output_view_w * self.render_ratio))
        self.view_h = max(180, round(self.output_view_h * self.render_ratio))

        # Render ve fiziksel alan aynı boyuttaysa doğrudan ekran alt yüzeyine çiz.
        # Bu, Pi Zero'da her kare yapılan tam ekran canvas -> screen kopyasını kaldırır.
        self.direct_render = (self.view_w, self.view_h) == (
            self.output_view_w,
            self.output_view_h,
        )
        if self.direct_render:
            self.screen.fill((0, 0, 0))
            self.canvas = self.screen.subsurface(
                pygame.Rect(self.view_x, self.view_y, self.view_w, self.view_h)
            )
            self.present_surface = None
        else:
            self.canvas = pygame.Surface((self.view_w, self.view_h)).convert()
            self.present_surface = pygame.Surface(
                (self.output_view_w, self.output_view_h)
            ).convert()

        # --- Oyun koordinatları (oynanabilir alan uzayında, çözünürlükten bağımsız) ---
        self.thief_y = int(round(self.view_h * cfg.thief_ground_pct / 100))

        if cfg.band_enabled:
            bw = max(1, round(cfg.band_width_px * self.render_ratio))
            cx = self.view_w * cfg.band_center_pct / 100
            self.band_x_min = int(round(cx - bw / 2))
            self.band_x_max = int(round(cx + bw / 2))
        else:
            bw = 0
            self.band_x_min = 0
            self.band_x_max = 0
        self.band_width_px = bw

        scale = max(1, round(cfg.thief_scale * self.render_ratio))
        self.runtime_thief_scale = scale
        configured_margin = round(cfg.spawn_margin_px * self.render_ratio)
        margin = configured_margin or (48 * scale // 2 + round(60 * self.render_ratio))
        spawn_x = self.view_w + margin
        reset_x = -margin

        # --- Animatör ---
        self.animator = ThiefAnimator(
            self.sprite_path,
            scale=scale,
            anim_fps=cfg.anim_fps,
            fall_sprite_path=self.fall_sprite_path,
            death_sprite_path=self.death_sprite_path,
        )
        self.animator.set_state("run")

        # --- Oyun mantığı ---
        self.game = GameLogic(
            spawn_x=spawn_x,
            reset_x=reset_x,
            thief_y=self.thief_y,
            speed_px_s=cfg.thief_speed_px_s * self.render_ratio,
            random_direction=cfg.random_direction,
            band_enabled=cfg.band_enabled,
            band_x_min=self.band_x_min,
            band_x_max=self.band_x_max,
            hit_cooldown_ms=cfg.hit_cooldown_ms,
            screen_width=self.view_w,
            server_controlled=cfg.server_controlled,
            on_score=self._on_score,
            on_direction_change=self._on_direction_change,
            debug=cfg.debug,
        )
        self.animator.set_direction(self.game.get_direction())

        # --- Önceden hazırlanan render yüzeyleri (view boyutunda) ---
        self.band_color = cfg.band_color
        self.band_surface = None
        if cfg.band_enabled:
            self.band_surface = pygame.Surface((bw, self.view_h), pygame.SRCALPHA)
            self.band_surface.fill(self.band_color)

        self.flash_red_surface = pygame.Surface((self.view_w // 2, self.view_h))
        self.flash_red_surface.fill((255, 0, 0))
        self.flash_red_surface.set_alpha(60)
        self.flash_blue_surface = pygame.Surface((self.view_w // 2, self.view_h))
        self.flash_blue_surface.fill((0, 0, 255))
        self.flash_blue_surface.set_alpha(60)

        # Skor yüzeyi cache
        self.score_surface = None
        self._score_surface_value = None

        # Tema fontları ve renkleri
        self.theme_yellow = (255, 224, 56)
        self.theme_yellow_light = (255, 242, 145)
        self.theme_red = (236, 45, 76)
        self.theme_purple = (105, 35, 165)
        self.theme_purple_dark = (53, 20, 88)
        self.theme_white = (255, 255, 247)
        self.idle_title_font = self._theme_font(
            max(30, min(76, self.view_w // 16)), bold=True
        )
        self.idle_subtitle_font = self._theme_font(
            max(18, min(34, self.view_w // 34)), bold=True
        )
        self.score_label_font = self._theme_font(
            max(15, min(25, self.view_w // 55)), bold=True
        )
        self.score_number_font = self._theme_font(
            max(34, min(72, self.view_w // 20)), bold=True
        )
        self.countdown_title_font = self._theme_font(
            max(46, min(112, self.view_w // 10)), bold=True
        )
        self.countdown_number_font = self._theme_font(
            max(110, min(270, self.view_h // 3)), bold=True
        )
        self.countdown_go_font = self._theme_font(
            max(60, min(150, self.view_w // 8)), bold=True
        )
        self.idle_card_surface = self._build_idle_card()
        self.countdown_card_cache = {}
        self.countdown_dim_surface = pygame.Surface(
            (self.view_w, self.view_h),
            pygame.SRCALPHA,
        )
        self.countdown_dim_surface.fill((30, 8, 51, 145))

        # Sunucuyla senkron başlangıç gösterisi
        self.countdown_display_message = None
        self.countdown_stage_start = 0
        self.countdown_display_until = 0
        self.countdown_was_active = False
        self.countdown_confetti = []
        self.scene_renderer = SceneRenderer(self.view_w, self.view_h)
        self.scene_renderer.set_quality(self.quality_level)
        if self._last_scene_payload:
            self.scene_renderer.apply(**self._last_scene_payload)
            self._apply_scene_runtime_layout(self._last_scene_payload.get("document"))

        # Gölge cache
        self.shadow_cache = {}

        # Efekt durumları
        self.hit_flash = False
        self.hit_flash_end = 0
        self.floating_texts = []
        self.particles = []
        self.shake_timer = 0.0
        self.shake_magnitude = 0.0
        self.thief_alpha = 255
        self.hit_stop_timer = 0.0

        # Arka plan (view boyutuna ölçeklenir)
        self.background = None
        bg_path = os.path.join(self.script_dir, "assets", "bg", "bg.png")
        if os.path.exists(bg_path):
            bg = pygame.image.load(bg_path).convert()
            self.background = pygame.transform.scale(bg, (self.view_w, self.view_h))

        # Ekranı siyaha boya (bar bölgeleri sabit kalır)
        self.screen.fill((0, 0, 0))
        pygame.display.flip()

    # ---------------------------------------------------------------- events
    def _on_score(self, points: int, combo: int = 1):
        """Skor arttığında çağrılır"""
        self.net_client.send_score(points)
        self.hit_flash = True
        self.hit_flash_end = pygame.time.get_ticks() + 200
        self._score_surface_value = None

        # Hit Stop (Zamanı dondur)
        self.hit_stop_timer = 0.15

        # Sarsıntı tetikle
        self.shake_timer = 0.2
        self.shake_magnitude = 15.0

        # Yüzen metin (Floating Text) ekle
        thief_x = self.game.get_thief_center_x()
        self.floating_texts.append(
            FloatingText(thief_x, self.thief_y - 150, "YAKALANDI!", self.font, (255, 100, 100))
        )
        self.floating_texts.append(
            FloatingText(thief_x, self.thief_y - 100, f"+{points}", self.idle_font, (255, 255, 0))
        )

        if combo > 1:
            self.floating_texts.append(
                FloatingText(thief_x, self.thief_y - 200, f"{combo}x KOMBO!", self.font, (255, 150, 0))
            )

        # Para/Altın parçacıkları fırlat
        for _ in range(self._effect_budget(15, 12, 8, 4)):
            self.particles.append(Particle(thief_x, self.thief_y - 80))

    def _on_direction_change(self, direction: int):
        """Yön değiştiğinde çağrılır"""
        self.animator.set_direction(direction)

    def _apply_scene_runtime_layout(self, document):
        """Editördeki hit-zone ve path öğelerini oyun koordinatlarına dönüştür."""
        if not isinstance(document, dict):
            return
        canvas = document.get("canvas", {})
        source_w = max(1.0, float(canvas.get("width", 1920)))
        source_h = max(1.0, float(canvas.get("height", 1080)))
        scale_x, scale_y = self.view_w / source_w, self.view_h / source_h
        scene = document.get("scenes", {}).get("gameplay", {})
        zones, path_points = [], []
        for element in scene.get("elements", []):
            if not isinstance(element, dict) or element.get("hidden"):
                continue
            if element.get("type") == "hit_zone":
                zones.append({
                    "x": float(element.get("x", 0)) * scale_x,
                    "y": float(element.get("y", 0)) * scale_y,
                    "width": float(element.get("width", 1)) * scale_x,
                    "height": float(element.get("height", 1)) * scale_y,
                })
            elif element.get("type") == "path" and not path_points:
                origin_x, origin_y = float(element.get("x", 0)), float(element.get("y", 0))
                path_points = [
                    ((origin_x + float(point.get("x", 0))) * scale_x,
                     (origin_y + float(point.get("y", 0))) * scale_y)
                    for point in element.get("points", [])
                    if isinstance(point, dict)
                ]
        self.game.configure_runtime_layout(zones, path_points)
    # ---------------------------------------------------------------- loop
    def start(self):
        """Dış döngü: oyun + (gerekirse) sihirbazı yeniden açma."""
        try:
            while True:
                self.running = True
                self.run()

                if self.reopen_setup:
                    self.reopen_setup = False
                    self._run_setup(force=True)
                    self.config = GameConfig.from_dict(self.raw)
                    self._apply_config()
                    continue
                break
        finally:
            self._cleanup()

    def run(self):
        """Ana oyun döngüsü (tek oturum)"""
        while self.running:
            dt = self.clock.tick(self.config.fps) / 1000.0
            frame_started = time.perf_counter()

            # Hit Stop (Zaman donması) hesaplaması
            game_dt = dt
            if self.hit_stop_timer > 0:
                self.hit_stop_timer -= dt
                game_dt = 0.0

            # Event'leri işle
            self._handle_events()
            if not self.running:
                break

            # Hit kontrolü
            if self.hit_input.get_hit() and not self.net_client.server_screen_complete:
                self.game.process_hit()

            if self.net_client.consume_score_reset():
                self.game.reset_score()
                self._score_surface_value = None

            scene_payload = self.net_client.consume_scene_config()
            if scene_payload:
                self._last_scene_payload = {
                    "version": scene_payload["version"],
                    "document": scene_payload["document"],
                    "asset_paths": scene_payload.get("asset_paths", {}),
                    "preview_scene": scene_payload.get("preview_scene"),
                }
                self.scene_renderer.apply(**self._last_scene_payload)
                self._apply_scene_runtime_layout(scene_payload.get("document"))
            # Server spawn kontrolü
            if (
                self.config.server_controlled
                and not self.net_client.server_screen_complete
                and self.game.is_idle()
            ):
                if self.net_client.get_spawn():
                    self.game.trigger_spawn()

            # Piezo config relay
            piezo_config = self.net_client.get_piezo_config()
            if piezo_config:
                self.hit_input.send_config(
                    piezo_config.get("threshold", 100),
                    piezo_config.get("refractory_ms", 200),
                )

            # Güncelle
            self.game.update(game_dt)
            self._update_animation(game_dt)
            self._update_effects(dt)
            self._update_countdown(dt)

            # Çiz (canvas'a) ve ekrana sun; aşamaları ayrı ölç.
            draw_started = time.perf_counter()
            self._draw()
            draw_ms = (time.perf_counter() - draw_started) * 1000.0
            blit_ms, flip_ms = self._present()
            self._capture_requested_scene_preview()
            self._record_frame_performance(
                (time.perf_counter() - frame_started) * 1000.0,
                draw_ms,
                blit_ms,
                flip_ms,
            )

    def _present(self):
        """Canvas kopyalama/ölçekleme ve ekran flip sürelerini ayrı ölç."""
        blit_started = time.perf_counter()
        output = self.canvas
        if self.present_surface is not None:
            pygame.transform.scale(
                self.canvas,
                (self.output_view_w, self.output_view_h),
                self.present_surface,
            )
            output = self.present_surface
        if not self.direct_render:
            self.screen.blit(output, (self.view_x, self.view_y))
        blit_ms = (time.perf_counter() - blit_started) * 1000.0

        flip_started = time.perf_counter()
        pygame.display.flip()
        flip_ms = (time.perf_counter() - flip_started) * 1000.0
        return blit_ms, flip_ms

    def _capture_requested_scene_preview(self):
        """Server isterse güncel canvası bir kez küçültüp ağ threadine teslim et."""
        request_token = self.net_client.consume_scene_screenshot_request()
        if not request_token:
            return
        try:
            source = self.canvas
            max_width, max_height = 960, 540
            ratio = min(
                1.0,
                max_width / max(1, source.get_width()),
                max_height / max(1, source.get_height()),
            )
            if ratio < 0.999:
                size = (
                    max(1, round(source.get_width() * ratio)),
                    max(1, round(source.get_height() * ratio)),
                )
                source = pygame.transform.scale(source, size)
            buffer = io.BytesIO()
            pygame.image.save(source, buffer, "client-preview.png")
            self.net_client.submit_scene_screenshot(request_token, buffer.getvalue())
        except (pygame.error, OSError, ValueError) as exc:
            if self.config.debug:
                print(f"[Client] Önizleme görüntüsü alınamadı: {exc}")
    def _handle_events(self):
        """Pygame event'lerini işle"""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.running = False
                elif event.key == pygame.K_F11:
                    pygame.display.toggle_fullscreen()
                elif event.key == pygame.K_s:
                    # Kurulum sihirbazını yeniden aç
                    self.reopen_setup = True
                    self.running = False

            # Klavye hit input için
            if isinstance(self.hit_input, KeyboardHitInput):
                self.hit_input.process_event(event)

    def _update_animation(self, dt: float):
        """Animasyonu güncelle"""
        if self.game.is_idle():
            pass  # IDLE'da animasyon yok
        elif self.game.is_running():
            self.animator.set_state("run")
            self.animator.update(dt)
        elif self.game.is_falling():
            self.animator.set_state("fall")
            self.animator.update(dt)

    def _update_effects(self, dt: float):
        """Sarsıntı, yazı ve parçacıkları güncelle"""
        if self.shake_timer > 0:
            self.shake_timer -= dt

        alive_particles = []
        for p in self.particles:
            if p.update(dt, self.thief_y):
                alive_particles.append(p)
        self.particles = alive_particles

        alive_texts = []
        for t in self.floating_texts:
            if t.update(dt):
                alive_texts.append(t)
        self.floating_texts = alive_texts

        # Hırsızın ölüm sonrası fade out efekti
        self.thief_alpha = 255
        if self.game.thief.state in [GameState.FALL, GameState.COOLDOWN, GameState.IDLE]:
            if hasattr(self.game.thief, "fall_start") and self.game.thief.fall_start > 0:
                elapsed = time.time() - self.game.thief.fall_start
                fade_start = 2.0
                fade_duration = 1.0

                if elapsed > fade_start + fade_duration:
                    self.thief_alpha = 0
                elif elapsed > fade_start:
                    progress = (elapsed - fade_start) / fade_duration
                    self.thief_alpha = int(255 * (1.0 - progress))

    def _update_countdown(self, dt: float):
        """Sunucudan gelen sahne değişimini gösteriye dönüştür."""
        status = self.net_client.get_countdown_status()
        active = status["active"]
        message = status["message"]
        now = pygame.time.get_ticks()

        if active and message and message != self.countdown_display_message:
            self._start_countdown_stage(message, now)
        elif (
            self.countdown_was_active
            and not active
            and self.net_client.server_game_active
        ):
            self._start_countdown_stage("BAŞLA!", now, duration_ms=750)
        elif not active and not self.net_client.server_game_active:
            self.countdown_display_message = None
            self.countdown_confetti.clear()

        self.countdown_was_active = active
        self.countdown_confetti = [
            piece for piece in self.countdown_confetti if piece.update(dt)
        ]

        if (
            not active
            and self.countdown_display_message != "BAŞLA!"
            and now >= self.countdown_display_until
        ):
            self.countdown_display_message = None

    def _start_countdown_stage(
        self,
        message: str,
        now: int,
        duration_ms: int = 1150,
    ):
        self.countdown_display_message = message
        self.countdown_stage_start = now
        self.countdown_display_until = now + duration_ms

        count = self._effect_budget(82, 65, 42, 20) if message == "HIRSIZLARI VUR" else self._effect_budget(58, 45, 30, 14)
        if self.scene_renderer.ready:
            return
        origin_y = int(self.view_h * (0.58 if message == "HIRSIZLARI VUR" else 0.52))
        for _ in range(count):
            self.countdown_confetti.append(
                Confetti(self.view_w // 2, origin_y, spread=1.15)
            )

    # ---------------------------------------------------------------- draw
    def _draw(self):
        """Oynanabilir alanı (canvas) çiz"""
        # Sarsıntı ofseti
        shake_x = 0
        shake_y = 0
        if self.shake_timer > 0:
            shake_x = random.randint(int(-self.shake_magnitude), int(self.shake_magnitude))
            shake_y = random.randint(int(-self.shake_magnitude), int(self.shake_magnitude))

        # Arka plan
        if self.background:
            if shake_x or shake_y:
                self.canvas.fill((0, 0, 0))
            self.canvas.blit(self.background, (shake_x, shake_y))
        else:
            self.canvas.fill(self.bg_color)

        if self.scene_renderer.ready and self.scene_renderer.preview_scene:
            self.scene_renderer.draw(
                self.canvas,
                self.scene_renderer.preview_scene,
                self._scene_context(),
                restart_token=self.scene_renderer.version or "",
                resolve_rules=False,
            )
            if self.config.debug:
                self._draw_debug(shake_x, shake_y)
            return

        jail_scene_active = (
            self.net_client.server_game_active
            and self.net_client.server_screen_complete
            and self.net_client.server_scene == "jail"
        )
        if jail_scene_active:
            if not self.scene_renderer.draw(
                self.canvas,
                "jail",
                self._scene_context(),
            ):
                self._draw_idle(shake_x, shake_y)
            if self.config.debug:
                self._draw_debug(shake_x, shake_y)
            return

        result_scene_active = (
            not self.net_client.server_game_active
            and self.net_client.server_scene in ("win", "lose")
        )

        # Sonuç sahnesi lokal hırsız animasyonunu beklemeden hemen görünür.
        if result_scene_active:
            if not self.scene_renderer.draw(
                self.canvas,
                self.net_client.server_scene,
                self._scene_context(),
            ):
                self._draw_idle(shake_x, shake_y)
        # IDLE durumunda ve oyun aktif değilse bekleme mesajı göster
        elif self.game.is_idle() and not self.net_client.server_game_active:
            if not self.scene_renderer.draw(
                self.canvas,
                "waiting",
                self._scene_context(),
            ):
                self._draw_idle(shake_x, shake_y)
        else:
            if self.config.band_enabled:
                self._draw_band(shake_x, shake_y)

            if (
                self.config.shadow_enabled
                and self.quality_level != "minimal"
                and not self.game.is_idle()
            ):
                self._draw_shadow(shake_x, shake_y)

            self._draw_thief(shake_x, shake_y)

            if self.quality_level != "minimal":
                for p in self.particles:
                    p.draw(self.canvas, shake_x, shake_y)

            for t in self.floating_texts:
                t.draw(self.canvas, shake_x, shake_y)

        if self.net_client.server_game_active and self.scene_renderer.ready:
            self.scene_renderer.draw(
                self.canvas,
                "gameplay",
                self._scene_context(),
            )
        elif not self.scene_renderer.ready:
            self._draw_score(shake_x, shake_y)
        self._draw_hit_flash(shake_x, shake_y)
        self._draw_countdown()

        if self.config.debug:
            self._draw_debug(shake_x, shake_y)

    def _draw_idle(self, offset_x=0, offset_y=0):
        """IDLE durumunda temalı, canlı bekleme kartı."""
        card = self.idle_card_surface
        x = (self.view_w - card.get_width()) // 2 + offset_x
        y = (self.view_h - card.get_height()) // 2 + offset_y
        pulse = (math.sin(pygame.time.get_ticks() * 0.004) + 1.0) * 0.5
        glow = tuple(
            int(self.theme_red[i] * (1.0 - pulse) + self.theme_purple[i] * pulse)
            for i in range(3)
        )
        border_rect = pygame.Rect(x - 8, y - 8, card.get_width() + 16, card.get_height() + 16)
        pygame.draw.rect(self.canvas, glow, border_rect, width=6, border_radius=28)
        self.canvas.blit(card, (x, y))

        dot_y = y + card.get_height() - 34
        active_dot = (pygame.time.get_ticks() // 380) % 3
        for index in range(3):
            radius = 9 if index == active_dot else 6
            color = self.theme_red if index == active_dot else self.theme_purple
            pygame.draw.circle(
                self.canvas,
                color,
                (self.view_w // 2 + (index - 1) * 30 + offset_x, dot_y),
                radius,
            )

    def _draw_band(self, offset_x=0, offset_y=0):
        """Hedef bandını çiz"""
        if self.band_surface:
            self.canvas.blit(self.band_surface, (self.band_x_min + offset_x, offset_y))

    def _get_shadow_surface(self, frame):
        """Mevcut sprite frame'i icin cache'lenmis golge yuzeyi dondur."""
        cache_key = id(frame)
        shadow = self.shadow_cache.get(cache_key)
        if shadow:
            return shadow

        shadow_width = int(frame.get_width() * self.config.shadow_scale_x)
        shadow_height = int(frame.get_height() * self.config.shadow_scale_y)

        shadow = pygame.Surface(
            (frame.get_width(), frame.get_height()),
            pygame.SRCALPHA,
        )
        shadow.blit(frame, (0, 0))

        try:
            import numpy as np
            alpha_array = pygame.surfarray.pixels_alpha(shadow)
            alpha_array[:] = np.where(alpha_array > 0, self.config.shadow_alpha, 0)
            del alpha_array
        except (NotImplementedError, ModuleNotFoundError):
            shadow.fill((0, 0, 0, self.config.shadow_alpha), special_flags=pygame.BLEND_RGBA_MULT)

        shadow = pygame.transform.scale(shadow, (shadow_width, shadow_height))
        self.shadow_cache[cache_key] = shadow
        return shadow

    def _draw_shadow(self, offset_x=0, offset_y=0):
        """Hırsızın gölgesini çiz"""
        if self.thief_alpha <= 0:
            return

        frame = self.animator.get_current_frame()

        if frame:
            shadow = self._get_shadow_surface(frame)
            shadow_width = shadow.get_width()
            shadow_height = shadow.get_height()

            x = self.game.thief.x - shadow_width // 2 + offset_x
            y = self.game.thief.y - shadow_height // 2 + self.config.shadow_offset_y + offset_y

            if self.animator.current_state == "fall":
                y += 15 * self.runtime_thief_scale

            draw_shadow = shadow
            if self.thief_alpha < 255:
                draw_shadow = shadow.copy()
                draw_shadow.set_alpha(self.thief_alpha)

            self.canvas.blit(draw_shadow, (x, y))

    def _draw_thief(self, offset_x=0, offset_y=0):
        """Hırsızı çiz"""
        if self.thief_alpha <= 0:
            return

        frame = self.animator.get_current_frame()

        if frame:
            x = self.game.thief.x - frame.get_width() // 2 + offset_x
            y = self.game.thief.y - frame.get_height() + offset_y

            if self.animator.current_state == "fall":
                y += 15 * self.runtime_thief_scale

            if self.thief_alpha < 255:
                frame = frame.copy()
                frame.set_alpha(self.thief_alpha)

            self.canvas.blit(frame, (x, y))

    def _draw_score(self, offset_x=0, offset_y=0):
        """Skoru temalı kart içinde çiz."""
        score_key = (self.game.score, self.game.combo)
        if self._score_surface_value != score_key:
            self.score_surface = self._build_score_card(
                self.game.score,
                self.game.combo,
            )
            self._score_surface_value = score_key

        x = self.view_w - self.score_surface.get_width() - 24 + offset_x
        y = 24 + offset_y
        shadow_rect = pygame.Rect(
            x + 7,
            y + 8,
            self.score_surface.get_width(),
            self.score_surface.get_height(),
        )
        pygame.draw.rect(
            self.canvas,
            self.theme_purple_dark,
            shadow_rect,
            border_radius=18,
        )
        self.canvas.blit(self.score_surface, (x, y))

    def _theme_font(self, size: int, bold: bool = False):
        return pygame.font.SysFont(
            "dejavusans,arial,freesans",
            int(size),
            bold=bold,
        )

    @staticmethod
    def _outlined_text(font, text, fill, outline, width=4):
        base = font.render(text, True, fill)
        surface = pygame.Surface(
            (base.get_width() + width * 2, base.get_height() + width * 2),
            pygame.SRCALPHA,
        )
        edge = font.render(text, True, outline)
        for dx, dy in (
            (-width, 0),
            (width, 0),
            (0, -width),
            (0, width),
            (-width, -width),
            (-width, width),
            (width, -width),
            (width, width),
        ):
            surface.blit(edge, (width + dx, width + dy))
        surface.blit(base, (width, width))
        return surface

    def _build_idle_card(self):
        width = min(int(self.view_w * 0.78), 920)
        height = max(190, min(int(self.view_h * 0.34), 320))
        card = pygame.Surface((width, height), pygame.SRCALPHA)
        pygame.draw.rect(
            card,
            self.theme_yellow,
            card.get_rect(),
            border_radius=24,
        )
        pygame.draw.rect(
            card,
            self.theme_purple,
            card.get_rect(),
            width=7,
            border_radius=24,
        )
        pygame.draw.rect(
            card,
            self.theme_red,
            (12, 12, width - 24, height - 24),
            width=3,
            border_radius=17,
        )

        title = self._outlined_text(
            self.idle_title_font,
            "OYUN BEKLENİYOR",
            self.theme_red,
            self.theme_purple,
            width=3,
        )
        subtitle = self.idle_subtitle_font.render(
            "HAZIR OL  •  HIRSIZLAR YAKINDA",
            True,
            self.theme_purple,
        )
        card.blit(title, ((width - title.get_width()) // 2, 34))
        card.blit(
            subtitle,
            ((width - subtitle.get_width()) // 2, 50 + title.get_height()),
        )
        return card

    def _build_score_card(self, score: int, combo: int):
        width = max(190, min(310, self.view_w // 4))
        height = max(105, min(150, self.view_h // 6))
        card = pygame.Surface((width, height), pygame.SRCALPHA)
        pygame.draw.rect(
            card,
            self.theme_yellow,
            card.get_rect(),
            border_radius=18,
        )
        pygame.draw.rect(
            card,
            self.theme_purple,
            card.get_rect(),
            width=5,
            border_radius=18,
        )
        label = self.score_label_font.render(
            "YAKALANAN",
            True,
            self.theme_purple,
        )
        number = self._outlined_text(
            self.score_number_font,
            str(score),
            self.theme_red,
            self.theme_white,
            width=2,
        )
        card.blit(label, (18, 12))
        card.blit(number, (18, height - number.get_height() - 10))

        if combo > 1:
            combo_text = self.score_label_font.render(
                f"{combo}x KOMBO",
                True,
                self.theme_white,
            )
            badge = pygame.Rect(
                width - combo_text.get_width() - 24,
                height - combo_text.get_height() - 22,
                combo_text.get_width() + 14,
                combo_text.get_height() + 8,
            )
            pygame.draw.rect(card, self.theme_red, badge, border_radius=10)
            card.blit(combo_text, (badge.x + 7, badge.y + 4))
        return card

    def _build_countdown_card(self, message: str):
        width = min(int(self.view_w * 0.72), 980)
        height = min(int(self.view_h * 0.55), 560)
        card = pygame.Surface((width, height), pygame.SRCALPHA)
        pygame.draw.rect(
            card,
            self.theme_yellow,
            card.get_rect(),
            border_radius=34,
        )
        pygame.draw.rect(
            card,
            self.theme_purple,
            card.get_rect(),
            width=12,
            border_radius=34,
        )
        pygame.draw.rect(
            card,
            self.theme_red,
            (18, 18, width - 36, height - 36),
            width=5,
            border_radius=23,
        )

        if message == "HIRSIZLARI VUR":
            lines = ("HIRSIZLARI", "VUR")
            font = self.countdown_title_font
            gap = 0
        else:
            lines = (message,)
            font = (
                self.countdown_go_font
                if message == "BAŞLA!"
                else self.countdown_number_font
            )
            gap = 0

        rendered = [
            self._outlined_text(
                font,
                line,
                self.theme_red,
                self.theme_purple,
                width=max(4, font.get_height() // 25),
            )
            for line in lines
        ]
        total_height = sum(item.get_height() for item in rendered) + gap
        y = (height - total_height) // 2
        for item in rendered:
            card.blit(item, ((width - item.get_width()) // 2, y))
            y += item.get_height() + gap
        return card

    def _draw_countdown(self):
        message = self.countdown_display_message
        now = pygame.time.get_ticks()
        if not message or now >= self.countdown_display_until:
            return

        scene_id = "intro" if message == "HIRSIZLARI VUR" else "countdown"
        if self.scene_renderer.draw(
            self.canvas,
            scene_id,
            self._scene_context(countdown=message),
            restart_token=message,
        ):
            return

        self.canvas.blit(self.countdown_dim_surface, (0, 0))

        if self.quality_level != "minimal":
            for piece in self.countdown_confetti:
                piece.draw(self.canvas)

        card = self.countdown_card_cache.get(message)
        if card is None:
            card = self._build_countdown_card(message)
            self.countdown_card_cache[message] = card

        elapsed = max(0, now - self.countdown_stage_start)
        progress = min(1.0, elapsed / 920.0)
        eased = 1.0 - pow(1.0 - progress, 3)
        scale = 0.62 + eased * 0.5 + math.sin(progress * math.pi * 4) * 0.025
        scaled_size = (
            max(1, int(card.get_width() * scale)),
            max(1, int(card.get_height() * scale)),
        )
        # Kısa introda her kare ölçeklenir; normal scale Pi Zero'da smoothscale'den
        # belirgin biçimde daha ucuzdur ve çizgi roman görünümüne de uyar.
        scaled = (
            card
            if self.quality_level == "minimal"
            else pygame.transform.scale(card, scaled_size)
        )
        self.canvas.blit(
            scaled,
            (
                (self.view_w - scaled.get_width()) // 2,
                (self.view_h - scaled.get_height()) // 2,
            ),
        )

    def _scene_context(self, countdown=None):
        """Sahne metinlerindeki dinamik değişkenlerin anlık değerleri."""
        remaining = max(0, self.net_client.server_remaining_seconds)
        return {
            "score": self.net_client.server_total_score,
            "combo": self.game.combo,
            "countdown": countdown or self.countdown_display_message or "3",
"target_score": self.net_client.server_target_score,
            "screen_score": self.net_client.server_screen_score,
            "screen_target": self.net_client.server_screen_target,
            "screen_remaining": self.net_client.server_screen_remaining,
            "screen_complete": self.net_client.server_screen_complete,
            "remaining_time": f"{remaining // 60:02d}:{remaining % 60:02d}",
            "remaining_seconds": remaining,
            "hit_count": self.game.score,
            "game_active": self.net_client.server_game_active,
            "active_scene": self.net_client.server_scene,
            "screen_id": self.config.screen_id,
        }

    def _draw_hit_flash(self, offset_x=0, offset_y=0):
        """Hit flash (Polis Sireni) efekti çiz"""
        if self.hit_flash:
            if pygame.time.get_ticks() < self.hit_flash_end:
                self.canvas.blit(self.flash_red_surface, (offset_x, offset_y))
                self.canvas.blit(self.flash_blue_surface, (self.view_w // 2 + offset_x, offset_y))
            else:
                self.hit_flash = False

    def _draw_debug(self, offset_x=0, offset_y=0):
        """Debug bilgilerini çiz"""
        state_text = self.small_font.render(
            f"State: {self.game.get_state_name()} | Yön: {self.game.get_direction_name()}",
            True, self.text_color,
        )
        self.canvas.blit(state_text, (20 + offset_x, 20 + offset_y))

        pos_text = self.small_font.render(
            f"X: {int(self.game.thief.x)}", True, self.text_color,
        )
        self.canvas.blit(pos_text, (20 + offset_x, 50 + offset_y))

        fps_text = self.small_font.render(
            f"FPS: {int(self.clock.get_fps())}", True, self.text_color,
        )
        self.canvas.blit(fps_text, (20 + offset_x, 80 + offset_y))

        net_status = self.net_client.get_status()
        net_text = self.small_font.render(
            f"Net: {'OK' if net_status['connected'] else 'OFFLINE'} | "
            f"Sent: {net_status['events_sent']} | "
            f"Spawns: {net_status['spawns_received']}",
            True, self.text_color,
        )
        self.canvas.blit(net_text, (20 + offset_x, 110 + offset_y))

        mode = "SERVER" if self.config.server_controlled else "LOCAL"
        view_text = self.small_font.render(
            f"Ekran: {self.config.screen_id} | Mod: {mode} | "
            f"View: {self.view_w}x{self.view_h} @({self.view_x},{self.view_y})",
            True, self.text_color,
        )
        self.canvas.blit(view_text, (20 + offset_x, 140 + offset_y))

        hit_text = self.small_font.render(
            f"Hit Input: {'SPACE tuşu' if isinstance(self.hit_input, KeyboardHitInput) else 'Serial'} | S: Kurulum",
            True, self.text_color,
        )
        self.canvas.blit(hit_text, (20 + offset_x, 170 + offset_y))

    def _cleanup(self):
        """Kaynakları temizle"""
        self.hit_input.stop()
        self.net_client.stop()
        pygame.quit()


def main():
    """Ana fonksiyon"""
    config_path = "config.json"

    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    try:
        game = ThiefGame(config_path)
        game.start()
    except FileNotFoundError as e:
        print(f"[HATA] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nOyun kapatılıyor...")
    except Exception as e:
        print(f"[HATA] Beklenmeyen hata: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
