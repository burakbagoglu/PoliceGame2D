#!/usr/bin/env python3
"""
Thief Client - Raspberry Pi Zero 2 W için interaktif hırsız oyunu
Ana giriş noktası
"""
import sys
import os
import pygame

# Modül yolunu ekle
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.config import GameConfig
from lib.animation import ThiefAnimator
from lib.hit_input import HitInput, KeyboardHitInput
from lib.net_client import NetClient
from lib.game import GameLogic, GameState


class ThiefGame:
    """Ana oyun sınıfı"""
    
    def __init__(self, config_path: str = "config.json"):
        """
        Args:
            config_path: Konfigürasyon dosyası yolu
        """
        # Config yükle
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_file = os.path.join(script_dir, config_path)
        self.config = GameConfig.from_file(config_file)
        
        # Pygame başlat
        pygame.init()
        pygame.mouse.set_visible(False)
        
        # Ekran oluştur
        if self.config.fullscreen:
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            # Gerçek ekran boyutlarını al
            info = pygame.display.Info()
            self.screen_width = info.current_w
            self.screen_height = info.current_h
        else:
            self.screen_width = self.config.screen_width
            self.screen_height = self.config.screen_height
            self.screen = pygame.display.set_mode(
                (self.screen_width, self.screen_height)
            )
        
        pygame.display.set_caption(f"Hırsız Oyunu - Ekran {self.config.screen_id}")
        
        # Saat
        self.clock = pygame.time.Clock()
        
        # Sprite sheet yolu
        sprite_path = os.path.join(
            script_dir, 
            "..", 
            "thief-1.0", 
            "PNG", 
            "48x64_scale2x", 
            "thief.png"
        )
        
        # Alternatif yol (assets klasöründe)
        if not os.path.exists(sprite_path):
            sprite_path = os.path.join(script_dir, "assets", "thief.png")
        
        if not os.path.exists(sprite_path):
            print(f"[HATA] Sprite dosyası bulunamadı: {sprite_path}")
            print("Lütfen thief.png dosyasını assets/ klasörüne kopyalayın")
            sys.exit(1)
        
        # Fall sprite yolu (düşme animasyonu)
        fall_sprite_path = os.path.join(script_dir, "assets", "thief_with_fall.png")
        if not os.path.exists(fall_sprite_path):
            # Alternatif yol
            fall_sprite_path = os.path.join(script_dir, "..", "thief-1.0", "thief.png")
        if not os.path.exists(fall_sprite_path):
            fall_sprite_path = None  # Fallback: döndürme efekti kullanılacak
        
        # Animatör
        self.animator = ThiefAnimator(
            sprite_path,
            scale=self.config.thief_scale,
            anim_fps=self.config.anim_fps,
            fall_sprite_path=fall_sprite_path
        )
        self.animator.set_state("run")
        
        # Hit input (debug modunda klavye kullan)
        if self.config.debug:
            self.hit_input = KeyboardHitInput(debug=True)
        else:
            self.hit_input = HitInput(
                port=self.config.serial_port,
                baud=self.config.serial_baud,
                debug=self.config.debug
            )
        self.hit_input.start()
        
        # Network client
        self.net_client = NetClient(
            server_url=self.config.server_url,
            screen_id=self.config.screen_id,
            debug=self.config.debug
        )
        self.net_client.start()
        
        # Oyun mantığı
        self.game = GameLogic(
            spawn_x=self.config.spawn_x,
            reset_x=self.config.reset_x,
            thief_y=self.config.thief_y,
            speed_px_s=self.config.thief_speed_px_s,
            random_direction=self.config.random_direction,
            band_enabled=self.config.band_enabled,
            band_x_min=self.config.band_x_min,
            band_x_max=self.config.band_x_max,
            hit_cooldown_ms=self.config.hit_cooldown_ms,
            screen_width=self.screen_width,
            on_score=self._on_score,
            on_direction_change=self._on_direction_change,
            debug=self.config.debug
        )
        
        # Başlangıç yönünü animatöre bildir
        self.animator.set_direction(self.game.get_direction())
        
        # Çalışıyor mu?
        self.running = True
        
        # Font (skor gösterimi için)
        self.font = pygame.font.Font(None, 72)
        self.small_font = pygame.font.Font(None, 36)
        
        # Renkler
        self.bg_color = (40, 44, 52)  # Koyu gri
        self.band_color = self.config.band_color
        self.text_color = (255, 255, 255)
        self.hit_flash_color = (255, 255, 0)
        
        # Hit flash efekti
        self.hit_flash = False
        self.hit_flash_end = 0
        
        # Arka plan yükle (varsa)
        self.background = None
        bg_path = os.path.join(script_dir, "assets", "bg", "bg.png")
        if os.path.exists(bg_path):
            self.background = pygame.image.load(bg_path).convert()
            self.background = pygame.transform.scale(
                self.background, 
                (self.screen_width, self.screen_height)
            )
    
    def _on_score(self, points: int):
        """Skor arttığında çağrılır"""
        # Server'a gönder
        self.net_client.send_score(points)
        
        # Flash efekti
        self.hit_flash = True
        self.hit_flash_end = pygame.time.get_ticks() + 200
    
    def _on_direction_change(self, direction: int):
        """Yön değiştiğinde çağrılır"""
        # Animatöre yönü bildir
        self.animator.set_direction(direction)
    
    def run(self):
        """Ana oyun döngüsü"""
        while self.running:
            # Delta time hesapla
            dt = self.clock.tick(self.config.fps) / 1000.0
            
            # Event'leri işle
            self._handle_events()
            
            # Hit kontrolü
            if self.hit_input.get_hit():
                self.game.process_hit()
            
            # Oyun güncelle
            self.game.update(dt)
            
            # Animasyonu güncelle
            self._update_animation(dt)
            
            # Çiz
            self._draw()
            
            # Ekranı güncelle
            pygame.display.flip()
        
        # Temizlik
        self._cleanup()
    
    def _handle_events(self):
        """Pygame event'lerini işle"""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.running = False
                elif event.key == pygame.K_F11:
                    # Fullscreen toggle
                    pygame.display.toggle_fullscreen()
            
            # Klavye hit input için
            if isinstance(self.hit_input, KeyboardHitInput):
                self.hit_input.process_event(event)
    
    def _update_animation(self, dt: float):
        """Animasyonu güncelle"""
        # State'e göre animasyon değiştir
        if self.game.is_running():
            self.animator.set_state("run")
        elif self.game.is_falling():
            self.animator.set_state("fall")
        
        self.animator.update(dt)
    
    def _draw(self):
        """Ekranı çiz"""
        # Arka plan
        if self.background:
            self.screen.blit(self.background, (0, 0))
        else:
            self.screen.fill(self.bg_color)
        
        # Hedef bandı çiz (sadece band_enabled ise)
        if self.config.band_enabled:
            self._draw_band()
        
        # Gölgeyi çiz (hırsızdan önce)
        if self.config.shadow_enabled:
            self._draw_shadow()
        
        # Hırsızı çiz
        self._draw_thief()
        
        # Skoru çiz
        self._draw_score()
        
        # Hit flash efekti
        self._draw_hit_flash()
        
        # Debug bilgisi
        if self.config.debug:
            self._draw_debug()
    
    def _draw_band(self):
        """Hedef bandını çiz"""
        band_surface = pygame.Surface(
            (self.config.band_width, self.screen_height),
            pygame.SRCALPHA
        )
        band_surface.fill(self.band_color)
        
        self.screen.blit(band_surface, (self.config.band_x_min, 0))
    
    def _draw_shadow(self):
        """Hırsızın gölgesini çiz"""
        frame = self.animator.get_current_frame()
        
        if frame:
            # Gölge boyutları
            shadow_width = int(self.animator.frame_width * self.config.shadow_scale_x)
            shadow_height = int(self.animator.frame_height * self.config.shadow_scale_y)
            
            # Siyah silüet oluştur
            shadow = pygame.Surface((self.animator.frame_width, self.animator.frame_height), pygame.SRCALPHA)
            shadow.blit(frame, (0, 0))
            
            # Tüm pikselleri siyaha dönüştür (alpha'yı koru)
            shadow_array = pygame.surfarray.pixels3d(shadow)
            shadow_array[:, :, :] = 0  # RGB'yi siyah yap
            del shadow_array
            
            # Alpha değerini ayarla
            alpha_array = pygame.surfarray.pixels_alpha(shadow)
            alpha_array[alpha_array > 0] = self.config.shadow_alpha
            del alpha_array
            
            # Gölgeyi ölçekle (yatay elips şeklinde)
            shadow = pygame.transform.scale(shadow, (shadow_width, shadow_height))
            
            # Gölge pozisyonu (hırsızın ayaklarının altında)
            x = self.game.thief.x - shadow_width // 2
            y = self.game.thief.y - shadow_height // 2 + self.config.shadow_offset_y
            
            self.screen.blit(shadow, (x, y))
    
    def _draw_thief(self):
        """Hırsızı çiz"""
        frame = self.animator.get_current_frame()
        
        if frame:
            # Hırsızın sol üst köşesi
            x = self.game.thief.x - self.animator.frame_width // 2
            y = self.game.thief.y - self.animator.frame_height
            
            self.screen.blit(frame, (x, y))
    
    def _draw_score(self):
        """Skoru çiz"""
        score_text = self.font.render(f"Skor: {self.game.score}", True, self.text_color)
        
        # Sağ üst köşe
        x = self.screen_width - score_text.get_width() - 20
        y = 20
        
        self.screen.blit(score_text, (x, y))
    
    def _draw_hit_flash(self):
        """Hit flash efekti çiz"""
        if self.hit_flash:
            if pygame.time.get_ticks() < self.hit_flash_end:
                flash = pygame.Surface((self.screen_width, self.screen_height))
                flash.fill(self.hit_flash_color)
                flash.set_alpha(50)
                self.screen.blit(flash, (0, 0))
            else:
                self.hit_flash = False
    
    def _draw_debug(self):
        """Debug bilgilerini çiz"""
        # Durum ve Yön
        state_text = self.small_font.render(
            f"State: {self.game.get_state_name()} | Yön: {self.game.get_direction_name()}", 
            True, 
            self.text_color
        )
        self.screen.blit(state_text, (20, 20))
        
        # Pozisyon
        pos_text = self.small_font.render(
            f"X: {int(self.game.thief.x)}", 
            True, 
            self.text_color
        )
        self.screen.blit(pos_text, (20, 50))
        
        # FPS
        fps_text = self.small_font.render(
            f"FPS: {int(self.clock.get_fps())}", 
            True, 
            self.text_color
        )
        self.screen.blit(fps_text, (20, 80))
        
        # Network durumu
        net_status = self.net_client.get_status()
        net_text = self.small_font.render(
            f"Net: {'OK' if net_status['connected'] else 'OFFLINE'} | Sent: {net_status['events_sent']}", 
            True, 
            self.text_color
        )
        self.screen.blit(net_text, (20, 110))
        
        # Ekran ID
        id_text = self.small_font.render(
            f"Ekran: {self.config.screen_id}", 
            True, 
            self.text_color
        )
        self.screen.blit(id_text, (20, 140))
        
        # Hit input durumu
        hit_text = self.small_font.render(
            f"Hit Input: {'SPACE tuşu' if isinstance(self.hit_input, KeyboardHitInput) else 'Serial'}", 
            True, 
            self.text_color
        )
        self.screen.blit(hit_text, (20, 170))
    
    def _cleanup(self):
        """Kaynakları temizle"""
        self.hit_input.stop()
        self.net_client.stop()
        pygame.quit()


def main():
    """Ana fonksiyon"""
    # Komut satırı argümanları
    config_path = "config.json"
    
    if len(sys.argv) > 1:
        config_path = sys.argv[1]
    
    # Oyunu başlat
    try:
        game = ThiefGame(config_path)
        game.run()
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
