"""
Animation modülü - Sprite sheet'ten frame okuma ve animasyon yönetimi
"""
import pygame
import os
from typing import List, Optional


class SpriteSheet:
    """Sprite sheet'ten frame'leri çıkaran sınıf"""
    
    def __init__(self, image_path: str, frame_width: int, frame_height: int):
        """
        Args:
            image_path: Sprite sheet dosya yolu
            frame_width: Her frame'in genişliği (piksel)
            frame_height: Her frame'in yüksekliği (piksel)
        """
        self.sheet = pygame.image.load(image_path).convert_alpha()
        self.frame_width = frame_width
        self.frame_height = frame_height
        
        # Sheet boyutlarından satır/sütun sayısını hesapla
        self.cols = self.sheet.get_width() // frame_width
        self.rows = self.sheet.get_height() // frame_height
    
    def get_frame(self, row: int, col: int) -> pygame.Surface:
        """Belirli bir frame'i al"""
        x = col * self.frame_width
        y = row * self.frame_height
        
        frame = pygame.Surface((self.frame_width, self.frame_height), pygame.SRCALPHA)
        frame.blit(self.sheet, (0, 0), (x, y, self.frame_width, self.frame_height))
        return frame
    
    def get_row_frames(self, row: int) -> List[pygame.Surface]:
        """Bir satırdaki tüm frame'leri al"""
        return [self.get_frame(row, col) for col in range(self.cols)]
    
    def get_scaled_frame(self, row: int, col: int, scale: int) -> pygame.Surface:
        """Ölçeklenmiş frame al"""
        frame = self.get_frame(row, col)
        new_size = (self.frame_width * scale, self.frame_height * scale)
        return pygame.transform.scale(frame, new_size)


class Animator:
    """Animasyon oynatıcı sınıfı"""
    
    def __init__(self, frames: List[pygame.Surface], fps: int = 12):
        """
        Args:
            frames: Animasyon frame'leri listesi
            fps: Animasyon hızı (frame per second)
        """
        self.frames = frames
        self.fps = fps
        self.frame_duration = 1.0 / fps  # Her frame'in süresi (saniye)
        
        self.current_frame = 0
        self.elapsed_time = 0.0
        self.is_playing = True
        self.loop = True
        self.finished = False
    
    def update(self, dt: float):
        """
        Animasyonu güncelle
        
        Args:
            dt: Geçen süre (saniye)
        """
        if not self.is_playing or not self.frames:
            return
        
        self.elapsed_time += dt
        
        if self.elapsed_time >= self.frame_duration:
            self.elapsed_time -= self.frame_duration
            self.current_frame += 1
            
            if self.current_frame >= len(self.frames):
                if self.loop:
                    self.current_frame = 0
                else:
                    self.current_frame = len(self.frames) - 1
                    self.is_playing = False
                    self.finished = True
    
    def get_current_frame(self) -> Optional[pygame.Surface]:
        """Mevcut frame'i döndür"""
        if not self.frames:
            return None
        return self.frames[self.current_frame]
    
    def reset(self):
        """Animasyonu sıfırla"""
        self.current_frame = 0
        self.elapsed_time = 0.0
        self.is_playing = True
        self.finished = False
    
    def play(self, loop: bool = True):
        """Animasyonu başlat"""
        self.loop = loop
        self.is_playing = True
        self.finished = False
    
    def stop(self):
        """Animasyonu durdur"""
        self.is_playing = False


class ThiefAnimator:
    """Hırsız karakteri için özel animatör"""
    
    # Sprite sheet satır indeksleri (N/E/S/W formatı)
    ROW_NORTH = 0  # Yukarı
    ROW_EAST = 1   # Sağa
    ROW_SOUTH = 2  # Aşağı
    ROW_WEST = 3   # Sola
    
    def __init__(self, sprite_sheet_path: str, scale: int = 4, anim_fps: int = 12):
        """
        Args:
            sprite_sheet_path: Sprite sheet dosya yolu
            scale: Büyütme faktörü
            anim_fps: Animasyon FPS
        """
        # 48x64 sprite sheet için
        self.sheet = SpriteSheet(sprite_sheet_path, 48, 64)
        self.scale = scale
        self.anim_fps = anim_fps
        
        # Frame boyutları
        self.frame_width = 48 * scale
        self.frame_height = 64 * scale
        
        # Mevcut hareket yönü (1: sağa, -1: sola)
        self.direction = -1  # Varsayılan: sola gidiyor
        
        # Animasyonları oluştur
        self._create_animations()
        
        # Aktif animatör
        self.current_animator: Optional[Animator] = None
        self.current_state = "idle"
    
    def _create_animations(self):
        """Her iki yön için animasyonları oluştur"""
        # West (sola) yönü frame'lerini al
        west_frames_raw = self.sheet.get_row_frames(self.ROW_WEST)
        # East (sağa) yönü frame'lerini al
        east_frames_raw = self.sheet.get_row_frames(self.ROW_EAST)
        
        # Ölçekle
        self.west_frames = [
            pygame.transform.scale(f, (self.frame_width, self.frame_height))
            for f in west_frames_raw
        ]
        self.east_frames = [
            pygame.transform.scale(f, (self.frame_width, self.frame_height))
            for f in east_frames_raw
        ]
        
        # Run animasyonları (her iki yön için)
        self.run_frames_left = self.west_frames
        self.run_frames_right = self.east_frames
        
        # Fall animasyonları (her iki yön için)
        self.fall_frames_left = self._create_fall_frames(self.west_frames[1], -1)
        self.fall_frames_right = self._create_fall_frames(self.east_frames[1], 1)
        
        # Animatörler (varsayılan: sola)
        self.run_animator = Animator(self.run_frames_left, self.anim_fps)
        self.fall_animator = Animator(self.fall_frames_left, self.anim_fps)
    
    def _create_fall_frames(self, base_frame: pygame.Surface, direction: int = -1) -> List[pygame.Surface]:
        """
        Düşme animasyonu oluştur (basit döndürme efekti)
        Gerçek projede özel fall sprite'ları kullanılmalı
        
        Args:
            base_frame: Temel frame
            direction: Yön (-1: sola düşer, 1: sağa düşer)
        """
        fall_frames = []
        
        # 6 frame'lik düşme animasyonu
        # Sola gidiyorsa sağa dönerek düşer, sağa gidiyorsa sola dönerek düşer
        angles = [0, 15, 30, 45, 60, 90]
        
        for angle in angles:
            # Yöne göre döndürme yönünü belirle
            rotated = pygame.transform.rotate(base_frame, angle * (-direction))
            fall_frames.append(rotated)
        
        return fall_frames
    
    def set_direction(self, direction: int):
        """
        Hareket yönünü değiştir
        
        Args:
            direction: 1 (sağa) veya -1 (sola)
        """
        if direction == self.direction:
            return
        
        self.direction = direction
        
        # Animatörleri güncelle
        if direction == -1:  # Sola
            self.run_animator = Animator(self.run_frames_left, self.anim_fps)
            self.fall_animator = Animator(self.fall_frames_left, self.anim_fps)
        else:  # Sağa
            self.run_animator = Animator(self.run_frames_right, self.anim_fps)
            self.fall_animator = Animator(self.fall_frames_right, self.anim_fps)
        
        # Mevcut state'i koru
        if self.current_state == "run":
            self.current_animator = self.run_animator
            self.current_animator.play(loop=True)
        elif self.current_state == "fall":
            self.current_animator = self.fall_animator
    
    def set_state(self, state: str):
        """
        Animasyon durumunu değiştir
        
        Args:
            state: "run" veya "fall"
        """
        if state == self.current_state:
            return
        
        self.current_state = state
        
        if state == "run":
            self.current_animator = self.run_animator
            self.current_animator.reset()
            self.current_animator.play(loop=True)
        elif state == "fall":
            self.current_animator = self.fall_animator
            self.current_animator.reset()
            self.current_animator.play(loop=False)
    
    def update(self, dt: float):
        """Animasyonu güncelle"""
        if self.current_animator:
            self.current_animator.update(dt)
    
    def get_current_frame(self) -> Optional[pygame.Surface]:
        """Mevcut frame'i döndür"""
        if self.current_animator:
            return self.current_animator.get_current_frame()
        return None
    
    def is_animation_finished(self) -> bool:
        """Animasyon bitti mi?"""
        if self.current_animator:
            return self.current_animator.finished
        return False
