"""
Game modülü - Oyun mantığı ve state machine
Server kontrollü veya bağımsız çalışabilir.
"""
import time
import random
from enum import Enum, auto
from typing import Optional, Callable
from dataclasses import dataclass


class GameState(Enum):
    """Oyun durumları"""
    IDLE = auto()     # Bekleme (server spawn komutu bekliyor)
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
        server_controlled: bool = False,
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
            server_controlled: True ise spawn server'dan gelir
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
        self._base_band_enabled = bool(band_enabled)
        self.band_enabled = bool(band_enabled)
        self.band_x_min = band_x_min
        self.band_x_max = band_x_max
        self.hit_cooldown_s = hit_cooldown_ms / 1000.0
        self.server_controlled = server_controlled
        self.on_score = on_score
        self.on_direction_change = on_direction_change
        self.debug = debug

        # Başlangıç durumu
        if self.server_controlled:
            # Server kontrollü: IDLE'dan başla
            initial_direction = Direction.LEFT
            self.thief = ThiefState(
                x=self.spawn_x_right,
                y=thief_y,
                state=GameState.IDLE,
                direction=initial_direction,
            )
        else:
            # Bağımsız: eski davranış, RUN'dan başla
            if self.random_direction:
                initial_direction = random.choice([Direction.LEFT, Direction.RIGHT])
            else:
                initial_direction = Direction.LEFT
            initial_x = self.spawn_x_right if initial_direction == Direction.LEFT else self.spawn_x_left

            self.thief = ThiefState(
                x=initial_x,
                y=thief_y,
                state=GameState.RUN,
                direction=initial_direction,
            )

        # Skor ve Kombo
        self.score = 0
        self.combo = 0

        # Fall animasyonu süresi (saniye) — 10 frame @ 12fps = 0.83s + yerde kalma
        self.fall_duration = 1.5
        self.runtime_hit_zones = []
        self.runtime_path_points = []
        self._active_path = []
        self._path_index = 0

    def configure_runtime_layout(self, hit_zones=None, path_points=None):
        """Sahne editöründen yayınlanan vuruş alanı ve hareket yolunu uygula."""
        self.runtime_hit_zones = [dict(zone) for zone in (hit_zones or []) if isinstance(zone, dict)]
        self.runtime_path_points = [
            (float(point[0]), float(point[1]))
            for point in (path_points or [])
            if isinstance(point, (list, tuple)) and len(point) >= 2
        ]
        self.band_enabled = bool(self.runtime_hit_zones) or self._base_band_enabled

    def _start_runtime_path(self, direction):
        if len(self.runtime_path_points) < 2:
            self._active_path = []
            return False
        points = list(self.runtime_path_points)
        self._active_path = list(reversed(points)) if direction == Direction.LEFT else points
        self._path_index = 0
        self.thief.x, self.thief.y = self._active_path[0]
        return True

    def reset_score(self):
        """Yerel skor ve komboyu sifirla."""
        self.score = 0
        self.combo = 0

    def trigger_spawn(self):
        """
        Server'dan spawn komutu geldiğinde çağrılır.
        Hırsızı IDLE'dan RUN'a geçirir.
        """
        if self.thief.state != GameState.IDLE:
            if self.debug:
                print(f"[Game] trigger_spawn reddedildi - state: {self.thief.state}")
            return

        # Yön seç
        if self.random_direction:
            new_direction = random.choice([Direction.LEFT, Direction.RIGHT])
        else:
            new_direction = Direction.LEFT

        # Yöne göre spawn pozisyonu
        if new_direction == Direction.LEFT:
            self.thief.x = self.spawn_x_right
        else:
            self.thief.x = self.spawn_x_left

        self.thief.y = self.thief_y
        self.thief.direction = new_direction
        self._start_runtime_path(new_direction)
        self.thief.state = GameState.RUN

        # Yön değişim callback'i
        if self.on_direction_change:
            self.on_direction_change(new_direction.value)

        if self.debug:
            yön = "SOLA" if new_direction == Direction.LEFT else "SAĞA"
            print(f"[Game] Hırsız spawn edildi - Yön: {yön}")

    def update(self, dt: float):
        """
        Oyun durumunu güncelle

        Args:
            dt: Geçen süre (saniye)
        """
        current_time = time.time()

        if self.thief.state == GameState.IDLE:
            pass  # Bekliyoruz, spawn komutu gelecek

        elif self.thief.state == GameState.RUN:
            self._update_run(dt)

        elif self.thief.state == GameState.FALL:
            self._update_fall(current_time)

        elif self.thief.state == GameState.COOLDOWN:
            self._update_cooldown(current_time)

        elif self.thief.state == GameState.RESET:
            self._do_reset()

    def _update_run(self, dt: float):
        """RUN durumunu güncelle"""
        if len(self._active_path) >= 2:
            self._update_runtime_path(dt)
            return
        # Yöne göre hareket et
        self.thief.x += self.speed_px_s * dt * self.thief.direction.value

        # Ekran dışına çıktıysa reset (Hırsız kaçtı, kombo sıfırlanır)
        if self.thief.direction == Direction.LEFT and self.thief.x < self.reset_x_left:
            self.combo = 0
            self.thief.state = GameState.RESET
        elif self.thief.direction == Direction.RIGHT and self.thief.x > self.reset_x_right:
            self.combo = 0
            self.thief.state = GameState.RESET

    def _update_runtime_path(self, dt: float):
        remaining = max(0.0, self.speed_px_s * dt)
        while remaining > 0 and self._path_index < len(self._active_path) - 1:
            start_x, start_y = self.thief.x, self.thief.y
            target_x, target_y = self._active_path[self._path_index + 1]
            dx, dy = target_x - start_x, target_y - start_y
            distance = (dx * dx + dy * dy) ** 0.5
            if distance <= remaining + 0.001:
                self.thief.x, self.thief.y = target_x, target_y
                self._path_index += 1
                remaining -= distance
            else:
                ratio = remaining / max(0.001, distance)
                self.thief.x += dx * ratio
                self.thief.y += dy * ratio
                remaining = 0
        if self._path_index >= len(self._active_path) - 1:
            self.combo = 0
            self.thief.state = GameState.RESET
    def _update_fall(self, current_time: float):
        """FALL durumunu güncelle"""
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
        if self.server_controlled:
            # Server kontrollü: IDLE'a geç, spawn komutu bekle
            self.thief.state = GameState.IDLE
            if self.debug:
                print("[Game] Hırsız IDLE'a geçti - spawn komutu bekleniyor")
            return

        # Bağımsız mod: eski davranış
        if self.random_direction:
            new_direction = random.choice([Direction.LEFT, Direction.RIGHT])
        else:
            new_direction = Direction.LEFT

        if new_direction == Direction.LEFT:
            self.thief.x = self.spawn_x_right
        else:
            self.thief.x = self.spawn_x_left

        self.thief.y = self.thief_y
        self.thief.direction = new_direction
        self._start_runtime_path(new_direction)
        self.thief.state = GameState.RUN

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
            self.score += 1
            self.combo += 1
            self.thief.state = GameState.FALL
            self.thief.fall_start = time.time()

            if self.debug:
                print(f"[Game] BAŞARILI HIT! Skor: {self.score} | Kombo: {self.combo}")

            if self.on_score:
                self.on_score(1, self.combo)

            return True
        else:
            self.combo = 0  # Iska geçildi, kombo sıfırlandı
            if self.debug:
                print(f"[Game] MISS - Kombo sıfırlandı. x: {self.thief.x}, band: [{self.band_x_min}, {self.band_x_max}]")
            return False

    def _is_in_band(self) -> bool:
        """Hırsız yayınlanan hit-zone veya eski hedef bandında mı?"""
        if self.runtime_hit_zones:
            return any(
                float(zone.get("x", 0)) <= self.thief.x <= float(zone.get("x", 0)) + float(zone.get("width", 0))
                and float(zone.get("y", 0)) <= self.thief.y <= float(zone.get("y", 0)) + float(zone.get("height", 0))
                for zone in self.runtime_hit_zones
            )
        return self.band_x_min <= self.thief.x <= self.band_x_max

    def get_thief_center_x(self) -> float:
        return self.thief.x

    def is_idle(self) -> bool:
        """Hırsız bekleme modunda mı?"""
        return self.thief.state == GameState.IDLE

    def is_running(self) -> bool:
        return self.thief.state == GameState.RUN

    def is_falling(self) -> bool:
        return self.thief.state == GameState.FALL

    def get_state_name(self) -> str:
        return self.thief.state.name

    def get_direction(self) -> int:
        return self.thief.direction.value

    def get_direction_name(self) -> str:
        return "SOLA" if self.thief.direction == Direction.LEFT else "SAĞA"
