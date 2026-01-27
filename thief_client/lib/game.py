"""
Game modülü - Oyun mantığı ve state machine
"""
import time
import random
from enum import Enum, auto
from typing import Optional, Callable
from dataclasses import dataclass


class GameState(Enum):
    """Oyun durumları"""
    RUN = auto()      # Hırsız koşuyor
    FALL = auto()     # Hırsız düşüyor (hit başarılı)
    COOLDOWN = auto() # Hit spam engeli
    RESET = auto()    # Hırsız yeniden spawn


class Direction(Enum):
    """Hareket yönleri"""
    LEFT = -1   # Sola
    RIGHT = 1   # Sağa


@dataclass
class ThiefState:
    """Hırsız durumu"""
    x: float
    y: float
    state: GameState
    direction: Direction = Direction.LEFT
    
    def __post_init__(self):
        self.cooldown_end: float = 0.0
        self.fall_start: float = 0.0


class GameLogic:
    """Oyun mantığı yöneticisi"""
    
    def __init__(
        self,
        spawn_x: int,
        reset_x: int,
        thief_y: int,
        speed_px_s: float,
        random_direction: bool,
        band_enabled: bool,
        band_x_min: int,
        band_x_max: int,
        hit_cooldown_ms: int,
        screen_width: int = 1920,
        on_score: Optional[Callable[[int], None]] = None,
        on_direction_change: Optional[Callable[[int], None]] = None,
        debug: bool = False
    ):
        """
        Args:
            spawn_x: Hırsızın başlangıç x pozisyonu (sağda)
            reset_x: Hırsızın reset x pozisyonu (solda, ekran dışı)
            thief_y: Hırsızın y pozisyonu
            speed_px_s: Hırsız hızı (piksel/saniye)
            random_direction: Rastgele yön mü (False = sadece sağdan)
            band_enabled: Band kontrolü aktif mi (False = her hit düşürür)
            band_x_min: Hedef bandının sol sınırı
            band_x_max: Hedef bandının sağ sınırı
            hit_cooldown_ms: Hit sonrası bekleme süresi (ms)
            screen_width: Ekran genişliği (sağa gidiş için)
            on_score: Skor artışında çağrılacak callback
            on_direction_change: Yön değişiminde çağrılacak callback
            debug: Debug modu
        """
        self.spawn_x_right = spawn_x  # Sağdan başlama noktası
        self.reset_x_left = reset_x   # Solda çıkış noktası
        self.spawn_x_left = reset_x   # Soldan başlama noktası
        self.reset_x_right = spawn_x  # Sağda çıkış noktası
        self.screen_width = screen_width
        self.thief_y = thief_y
        self.speed_px_s = speed_px_s
        self.random_direction = random_direction
        self.band_enabled = band_enabled
        self.band_x_min = band_x_min
        self.band_x_max = band_x_max
        self.hit_cooldown_s = hit_cooldown_ms / 1000.0
        self.on_score = on_score
        self.on_direction_change = on_direction_change
        self.debug = debug
        
        # Başlangıç yönü (random_direction=False ise sadece sağdan sola)
        if self.random_direction:
            initial_direction = random.choice([Direction.LEFT, Direction.RIGHT])
        else:
            initial_direction = Direction.LEFT  # Sağdan başla, sola git
        initial_x = self.spawn_x_right if initial_direction == Direction.LEFT else self.spawn_x_left
        
        # Hırsız durumu
        self.thief = ThiefState(
            x=initial_x,
            y=thief_y,
            state=GameState.RUN,
            direction=initial_direction
        )
        
        # Skor
        self.score = 0
        
        # Fall animasyonu süresi (saniye)
        self.fall_duration = 0.5
    
    def update(self, dt: float):
        """
        Oyun durumunu güncelle
        
        Args:
            dt: Geçen süre (saniye)
        """
        current_time = time.time()
        
        if self.thief.state == GameState.RUN:
            self._update_run(dt)
        
        elif self.thief.state == GameState.FALL:
            self._update_fall(current_time)
        
        elif self.thief.state == GameState.COOLDOWN:
            self._update_cooldown(current_time)
        
        elif self.thief.state == GameState.RESET:
            self._do_reset()
    
    def _update_run(self, dt: float):
        """RUN durumunu güncelle"""
        # Yöne göre hareket et
        self.thief.x += self.speed_px_s * dt * self.thief.direction.value
        
        # Ekran dışına çıktıysa reset
        if self.thief.direction == Direction.LEFT and self.thief.x < self.reset_x_left:
            self.thief.state = GameState.RESET
        elif self.thief.direction == Direction.RIGHT and self.thief.x > self.reset_x_right:
            self.thief.state = GameState.RESET
    
    def _update_fall(self, current_time: float):
        """FALL durumunu güncelle"""
        # Fall animasyonu bitti mi?
        elapsed = current_time - self.thief.fall_start
        
        if elapsed >= self.fall_duration:
            self.thief.state = GameState.COOLDOWN
            self.thief.cooldown_end = current_time + self.hit_cooldown_s
    
    def _update_cooldown(self, current_time: float):
        """COOLDOWN durumunu güncelle"""
        if current_time >= self.thief.cooldown_end:
            self.thief.state = GameState.RESET
    
    def _do_reset(self):
        """Hırsızı yeniden başlat"""
        # Yön seç (random_direction=False ise sadece sağdan sola)
        if self.random_direction:
            new_direction = random.choice([Direction.LEFT, Direction.RIGHT])
        else:
            new_direction = Direction.LEFT  # Sağdan başla, sola git
        
        # Yöne göre spawn pozisyonu
        if new_direction == Direction.LEFT:
            self.thief.x = self.spawn_x_right  # Sağdan başla, sola git
        else:
            self.thief.x = self.spawn_x_left   # Soldan başla, sağa git
        
        self.thief.y = self.thief_y
        self.thief.direction = new_direction
        self.thief.state = GameState.RUN
        
        # Yön değişim callback'i
        if self.on_direction_change:
            self.on_direction_change(new_direction.value)
        
        if self.debug:
            yön = "SOLA" if new_direction == Direction.LEFT else "SAĞA"
            print(f"[Game] Hırsız reset edildi - Yön: {yön}")
    
    def process_hit(self) -> bool:
        """
        Hit sinyalini işle
        
        Returns:
            True: Başarılı hit (skor arttı)
            False: Başarısız hit (band dışı veya cooldown)
        """
        # Sadece RUN durumunda hit kabul et
        if self.thief.state != GameState.RUN:
            if self.debug:
                print(f"[Game] Hit reddedildi - state: {self.thief.state}")
            return False
        
        # Band kontrolü (band_enabled=False ise her hit başarılı)
        hit_success = not self.band_enabled or self._is_in_band()
        
        if hit_success:
            # Başarılı hit!
            self.score += 1
            self.thief.state = GameState.FALL
            self.thief.fall_start = time.time()
            
            if self.debug:
                print(f"[Game] BAŞARILI HIT! Skor: {self.score}")
            
            # Callback
            if self.on_score:
                self.on_score(1)
            
            return True
        else:
            # Miss (sadece band_enabled=True iken olabilir)
            if self.debug:
                print(f"[Game] MISS - x: {self.thief.x}, band: [{self.band_x_min}, {self.band_x_max}]")
            return False
    
    def _is_in_band(self) -> bool:
        """Hırsız hedef bandında mı?"""
        # Hırsızın merkez x pozisyonu
        # (thief_rect.centerx yerine x kullanıyoruz, 
        # çünkü x zaten merkez olarak kabul ediliyor)
        return self.band_x_min <= self.thief.x <= self.band_x_max
    
    def get_thief_center_x(self) -> float:
        """Hırsızın merkez x pozisyonunu döndür"""
        return self.thief.x
    
    def is_running(self) -> bool:
        """Hırsız koşuyor mu?"""
        return self.thief.state == GameState.RUN
    
    def is_falling(self) -> bool:
        """Hırsız düşüyor mu?"""
        return self.thief.state == GameState.FALL
    
    def get_state_name(self) -> str:
        """Mevcut durum adını döndür"""
        return self.thief.state.name
    
    def get_direction(self) -> int:
        """Mevcut yönü döndür (-1: sola, 1: sağa)"""
        return self.thief.direction.value
    
    def get_direction_name(self) -> str:
        """Mevcut yön adını döndür"""
        return "SOLA" if self.thief.direction == Direction.LEFT else "SAĞA"
