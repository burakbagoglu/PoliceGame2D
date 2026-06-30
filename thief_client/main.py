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
import pygame
import time

# Modül yolunu ekle
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.config import GameConfig
from lib.animation import ThiefAnimator
from lib.hit_input import HitInput, KeyboardHitInput
from lib.net_client import NetClient
from lib.game import GameLogic, GameState
from lib.effects import FloatingText, Particle
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
            debug=self.config.debug,
        )
        self.net_client.start()

        # Çalışma bayrakları
        self.running = True
        self.reopen_setup = False

        # Config'e bağlı her şeyi kur (oynanabilir alan, animatör, oyun, yüzeyler)
        self._apply_config()

    # ---------------------------------------------------------------- setup
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
        self.view_w, self.view_h = rect.w, rect.h

        # Çizim hedefi (canvas) — pleksi boyutunda
        self.canvas = pygame.Surface((self.view_w, self.view_h))

        # --- Oyun koordinatları (oynanabilir alan uzayında, çözünürlükten bağımsız) ---
        self.thief_y = int(round(self.view_h * cfg.thief_ground_pct / 100))

        if cfg.band_enabled:
            bw = max(1, int(cfg.band_width_px))
            cx = self.view_w * cfg.band_center_pct / 100
            self.band_x_min = int(round(cx - bw / 2))
            self.band_x_max = int(round(cx + bw / 2))
        else:
            bw = 0
            self.band_x_min = 0
            self.band_x_max = 0
        self.band_width_px = bw

        scale = int(cfg.thief_scale)
        margin = cfg.spawn_margin_px or (48 * scale // 2 + 60)
        spawn_x = self.view_w + margin
        reset_x = -margin

        # --- Animatör ---
        self.animator = ThiefAnimator(
            self.sprite_path,
            scale=cfg.thief_scale,
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
            speed_px_s=cfg.thief_speed_px_s,
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
        for _ in range(15):
            self.particles.append(Particle(thief_x, self.thief_y - 80))

    def _on_direction_change(self, direction: int):
        """Yön değiştiğinde çağrılır"""
        self.animator.set_direction(direction)

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
            if self.hit_input.get_hit():
                self.game.process_hit()

            if self.net_client.consume_score_reset():
                self.game.reset_score()
                self._score_surface_value = None

            # Server spawn kontrolü
            if self.config.server_controlled and self.game.is_idle():
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

            # Çiz (canvas'a) ve ekrana sun
            self._draw()
            self._present()

    def _present(self):
        """Canvas'ı ekrana (oynanabilir alana) blit et."""
        self.screen.blit(self.canvas, (self.view_x, self.view_y))
        pygame.display.flip()

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

        # IDLE durumunda ve oyun aktif değilse bekleme mesajı göster
        if self.game.is_idle() and not self.net_client.server_game_active:
            self._draw_idle(shake_x, shake_y)
        else:
            if self.config.band_enabled:
                self._draw_band(shake_x, shake_y)

            if self.config.shadow_enabled and not self.game.is_idle():
                self._draw_shadow(shake_x, shake_y)

            self._draw_thief(shake_x, shake_y)

            for p in self.particles:
                p.draw(self.canvas, shake_x, shake_y)

            for t in self.floating_texts:
                t.draw(self.canvas, shake_x, shake_y)

        self._draw_score(shake_x, shake_y)
        self._draw_hit_flash(shake_x, shake_y)

        if self.config.debug:
            self._draw_debug(shake_x, shake_y)

    def _draw_idle(self, offset_x=0, offset_y=0):
        """IDLE durumunda bekleme mesajı"""
        text = self.idle_surface
        x = (self.view_w - text.get_width()) // 2 + offset_x
        y = (self.view_h - text.get_height()) // 2 + offset_y
        self.canvas.blit(text, (x, y))

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
                y += 15 * self.config.thief_scale

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
                y += 15 * self.config.thief_scale

            if self.thief_alpha < 255:
                frame = frame.copy()
                frame.set_alpha(self.thief_alpha)

            self.canvas.blit(frame, (x, y))

    def _draw_score(self, offset_x=0, offset_y=0):
        """Skoru çiz"""
        if self._score_surface_value != self.game.score:
            self.score_surface = self.font.render(f"Skor: {self.game.score}", True, self.text_color)
            self._score_surface_value = self.game.score

        score_text = self.score_surface
        x = self.view_w - score_text.get_width() - 20 + offset_x
        y = 20 + offset_y
        self.canvas.blit(score_text, (x, y))

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
