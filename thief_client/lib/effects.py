import random
import math
import pygame
from typing import Tuple, List

class FloatingText:
    """Yukarı doğru süzülüp kaybolan yazı efekti"""
    
    def __init__(self, x: int, y: int, text: str, font: pygame.font.Font, color: Tuple[int, int, int] = (255, 255, 0)):
        self.x = float(x)
        self.y = float(y)
        self.text = text
        self.font = font
        self.color = color
        
        self.alpha = 255.0
        # Yukarı doğru hareket hızı (piksel/saniye)
        self.vy = -50.0 
        # Kaybolma hızı
        self.fade_speed = 200.0
        
        self.surface = self.font.render(self.text, True, self.color)
        
    def update(self, dt: float) -> bool:
        """
        Güncelleme metodu.
        Return: True ise hayatta, False ise silinmeli.
        """
        self.y += self.vy * dt
        self.alpha -= self.fade_speed * dt
        
        return self.alpha > 0
        
    def draw(self, screen: pygame.Surface, offset_x: int = 0, offset_y: int = 0):
        if self.alpha <= 0:
            return None
            
        # Alpha uygulamak için geçici bir yüzey oluştur
        # Pygame'de metinlere doğrudan alpha uygulanmaz, copy'sine uygulanır
        txt_surf = self.surface.copy()
        txt_surf.set_alpha(int(self.alpha))
        
        # Ortalayarak çiz
        draw_x = int(self.x) - txt_surf.get_width() // 2 + offset_x
        draw_y = int(self.y) + offset_y
        
        return screen.blit(txt_surf, (draw_x, draw_y))


class Particle:
    """Fizik kurallarına göre hareket eden parçacık (ör: Altın para)"""
    
    def __init__(self, x: int, y: int, size: int = 6):
        self.x = float(x)
        self.y = float(y)
        self.size = size
        
        # Parıltılı altın renkleri
        colors = [(255, 215, 0), (218, 165, 32), (255, 223, 0), (255, 250, 205)]
        self.color = random.choice(colors)
        
        # Rastgele hız (patlama efekti için)
        self.vx = random.uniform(-150, 150)
        self.vy = random.uniform(-250, -50)
        
        # Yerçekimi
        self.gravity = 500.0
        
        # Yaşam süresi
        self.life = random.uniform(0.5, 1.2)
        self.max_life = self.life

        self.surface = pygame.Surface((self.size, self.size), pygame.SRCALPHA)
        pygame.draw.rect(self.surface, self.color, (0, 0, self.size, self.size))
        
    def update(self, dt: float, floor_y: int) -> bool:
        """
        Güncelleme metodu.
        Return: True ise hayatta, False ise silinmeli.
        """
        self.vy += self.gravity * dt
        
        self.x += self.vx * dt
        self.y += self.vy * dt
        
        # Yere çarpma ve sekme
        if self.y >= floor_y:
            self.y = floor_y
            self.vy = -self.vy * 0.5  # Sönümlemeli sekme
            self.vx *= 0.8            # Sürtünme
            
        self.life -= dt
        return self.life > 0
        
    def draw(self, screen: pygame.Surface, offset_x: int = 0, offset_y: int = 0):
        if self.life <= 0:
            return
            
        # Yaşamının son %30'unda yavaşça kaybolsun
        alpha = 255
        fade_point = self.max_life * 0.3
        if self.life < fade_point:
            alpha = int((self.life / fade_point) * 255)
            
        # Parçacık yüzeyi
        surf = self.surface
        if alpha < 255:
            surf = self.surface.copy()
            surf.set_alpha(alpha)
        
        # Biraz dönme efekti için boyutu rastgele değiştirerek yanıltsama yapılabilir
        # (Şimdilik basit kare kalsın)
        
        draw_x = int(self.x) - self.size // 2 + offset_x
        draw_y = int(self.y) - self.size + offset_y
        
        screen.blit(surf, (draw_x, draw_y))


class Confetti:
    """Başlangıç sayımında kullanılan hafif, yüzeysiz konfeti parçacığı."""

    COLORS = (
        (255, 224, 56),
        (255, 58, 94),
        (121, 45, 193),
        (255, 112, 180),
        (255, 255, 255),
        (56, 205, 255),
    )

    def __init__(self, x: int, y: int, spread: float = 1.0):
        self.x = float(x)
        self.y = float(y)
        self.width = random.randint(5, 11)
        self.height = random.randint(10, 20)
        self.color = random.choice(self.COLORS)
        self.vx = random.uniform(-420, 420) * spread
        self.vy = random.uniform(-620, -180)
        self.gravity = random.uniform(650, 900)
        self.spin = random.uniform(8, 18)
        self.angle = random.uniform(0, math.tau)
        self.life = random.uniform(1.2, 2.0)
        self.max_life = self.life

    def update(self, dt: float) -> bool:
        self.vy += self.gravity * dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.angle += self.spin * dt
        self.life -= dt
        return self.life > 0

    def draw(self, screen: pygame.Surface, offset_x: int = 0, offset_y: int = 0):
        if self.life <= 0:
            return
        visible_width = max(2, int(abs(math.cos(self.angle)) * self.width))
        alpha = min(255, int(255 * self.life / min(0.35, self.max_life)))
        color = (*self.color, alpha)
        rect = pygame.Rect(
            int(self.x) - visible_width // 2 + offset_x,
            int(self.y) - self.height // 2 + offset_y,
            visible_width,
            self.height,
        )
        pygame.draw.rect(screen, color, rect, border_radius=2)
