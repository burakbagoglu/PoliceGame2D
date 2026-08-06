"""
Spawn Engine - Server kontrollü hırsız spawn algoritmaları

Çocuk sayısına göre adaptive zorluk ayarlayan, fair-distribution
ile ekran seçen ve faz bazlı spawn yönetimi yapan modül.
"""
import time
import random
import threading
import queue
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


# ============== Target Calculator ==============

class TargetCalculator:
    """Çocuk sayısına göre hedef skor hesaplar"""

    DIFFICULTY_MULTIPLIERS = {
        'easy': 0.8,
        'normal': 1.0,
        'hard': 1.3,
    }

    def __init__(self, base_score_per_child: int = 15):
        self.base_score_per_child = base_score_per_child

    def calculate(self, child_count: int, difficulty: str = 'normal', duration_minutes: int = 20) -> dict:
        """
        Hedef skor hesapla.

        Returns:
            dict: total_target, per_minute, child_count, difficulty
        """
        base_target = child_count * self.base_score_per_child
        multiplier = self.DIFFICULTY_MULTIPLIERS.get(difficulty, 1.0)
        target_score = int(base_target * multiplier)

        per_minute = target_score / max(duration_minutes, 1)

        return {
            'total_target': target_score,
            'per_minute': per_minute,
            'child_count': child_count,
            'difficulty': difficulty,
        }


    def calculate_screen_quotas(
        self,
        child_count: int,
        difficulty: str = "normal",
        duration_minutes: int = 35,
        screen_count: int = 8,
        hits_per_child_per_screen: float = 6.0,
        minimum_per_screen: int = 12,
    ) -> dict:
        """8 paralel ekran için çocuk/süre/zorluğa bağlı bağımsız hedefler üret."""
        multiplier = self.DIFFICULTY_MULTIPLIERS.get(difficulty, 1.0)
        duration_factor = max(0.25, float(duration_minutes) / 35.0)
        per_screen = max(
            int(minimum_per_screen),
            int(math.ceil(child_count * hits_per_child_per_screen * multiplier * duration_factor)),
        )
        screen_targets = {screen_id: per_screen for screen_id in range(1, int(screen_count) + 1)}
        total_target = sum(screen_targets.values())
        return {
            "total_target": total_target,
            "per_screen_target": per_screen,
            "screen_targets": screen_targets,
            "per_minute": total_target / max(duration_minutes, 1),
            "child_count": child_count,
            "difficulty": difficulty,
            "duration_minutes": duration_minutes,
        }
# ============== Screen Selector ==============

class ScreenSelector:
    """
    Hangi ekranda hırsız çıkacağını belirler.
    Fair distribution + Round-robin + Randomness
    """

    def __init__(self, screen_count: int = 8):
        self.screen_count = screen_count
        self.spawn_history: deque = deque(maxlen=50)
        self.screen_spawn_counts: Dict[int, int] = {
            i: 0 for i in range(1, screen_count + 1)
        }

    def select_screens(
        self, spawn_count: int, active_screens: Optional[List[int]] = None
    ) -> List[int]:
        """
        Adil dağılım ile ekran seçimi.
        En az spawn alan ekranlardan seçer.
        """
        if active_screens is None:
            active_screens = list(range(1, self.screen_count + 1))
        else:
            # Polling yapan ancak bu oyun oturumuna dahil olmayan ekranları
            # dağıtım hesabına sokma. Aksi halde temp_counts[s] KeyError üretir.
            active_screens = [
                screen_id
                for screen_id in active_screens
                if 1 <= screen_id <= self.screen_count
            ]

        if not active_screens:
            return []

        selected: List[int] = []
        temp_counts = self.screen_spawn_counts.copy()

        for _ in range(min(spawn_count, len(active_screens))):
            available = [s for s in active_screens if s not in selected]
            if not available:
                break

            min_count = min(temp_counts[s] for s in available)
            candidates = [s for s in available if temp_counts[s] == min_count]

            chosen = random.choice(candidates)
            selected.append(chosen)
            temp_counts[chosen] += 1

        # Gerçek sayaçları güncelle
        for s in selected:
            self.screen_spawn_counts[s] += 1
            self.spawn_history.append(s)

        return selected

    def get_stats(self) -> Dict[int, float]:
        """Ekran başına spawn yüzdesi"""
        total = sum(self.screen_spawn_counts.values())
        if total == 0:
            return {s: 0.0 for s in self.screen_spawn_counts}

        return {
            s: round(count / total * 100, 1)
            for s, count in self.screen_spawn_counts.items()
        }

    def reset(self):
        """İstatistikleri sıfırla"""
        self.screen_spawn_counts = {
            i: 0 for i in range(1, self.screen_count + 1)
        }
        self.spawn_history.clear()


# ============== Game Session ==============

@dataclass
class GameSession:
    """Aktif oyun ve ekran başına bağımsız hırsız kotaları."""
    child_count: int
    target_score: int
    screen_count: int = 8
    current_score: int = 0
    start_time: float = 0.0
    is_active: bool = False
    total_seconds: int = 35 * 60
    total_spawns: int = 0
    countdown_seconds: int = 0
    screen_targets: Dict[int, int] = field(default_factory=dict)
    screen_scores: Dict[int, int] = field(default_factory=dict)

    def __post_init__(self):
        self.screen_count = int(self.screen_count)
        self.screen_targets = {int(k): max(1, int(v)) for k, v in self.screen_targets.items()}
        self.screen_scores = {
            screen_id: max(0, int(
                self.screen_scores.get(
                    screen_id,
                    self.screen_scores.get(str(screen_id), 0),
                )
            ))
            for screen_id in range(1, self.screen_count + 1)
        }
        if self.screen_targets:
            self.target_score = sum(self.screen_targets.values())
            self.current_score = sum(
                min(self.screen_scores.get(screen_id, 0), target)
                for screen_id, target in self.screen_targets.items()
            )

    @property
    def elapsed_seconds(self) -> int:
        if not self.is_active or self.start_time == 0:
            return 0
        return max(0, int(time.time() - self.start_time))

    @property
    def countdown_remaining_ms(self) -> int:
        if not self.is_active or self.start_time == 0:
            return 0
        return max(0, int((self.start_time - time.time()) * 1000))

    @property
    def countdown_remaining(self) -> int:
        if self.countdown_remaining_ms <= 0:
            return 0
        return max(1, math.ceil(self.countdown_remaining_ms / 1000))

    @property
    def countdown_active(self) -> bool:
        return self.countdown_remaining_ms > 0

    @property
    def countdown_message(self) -> Optional[str]:
        if not self.countdown_active:
            return None
        remaining = self.countdown_remaining
        return "HIRSIZLARI VUR" if remaining > 3 else str(remaining)

    def get_screen_target(self, screen_id: int) -> int:
        return int(self.screen_targets.get(int(screen_id), self.target_score))

    def get_screen_score(self, screen_id: int) -> int:
        return int(self.screen_scores.get(int(screen_id), 0))

    def is_screen_complete(self, screen_id: int) -> bool:
        if not self.screen_targets:
            return self.current_score >= self.target_score
        return self.get_screen_score(screen_id) >= self.get_screen_target(screen_id)

    @property
    def completed_screens(self) -> List[int]:
        if not self.screen_targets:
            return list(range(1, self.screen_count + 1)) if self.current_score >= self.target_score else []
        return [screen_id for screen_id in range(1, self.screen_count + 1) if self.is_screen_complete(screen_id)]

    @property
    def incomplete_screens(self) -> List[int]:
        completed = set(self.completed_screens)
        return [screen_id for screen_id in range(1, self.screen_count + 1) if screen_id not in completed]

    @property
    def all_screens_complete(self) -> bool:
        return len(self.completed_screens) == self.screen_count

    def record_screen_score(self, screen_id: int, points: int = 1) -> dict:
        screen_id, points = int(screen_id), max(0, int(points))
        if screen_id < 1 or screen_id > self.screen_count:
            return {"accepted_points": 0, "screen_complete": False}
        if not self.screen_targets:
            self.current_score += points
            self.screen_scores[screen_id] = self.screen_scores.get(screen_id, 0) + points
            return {"accepted_points": points, "screen_complete": self.current_score >= self.target_score}
        target = self.get_screen_target(screen_id)
        before = self.get_screen_score(screen_id)
        accepted = min(points, max(0, target - before))
        self.screen_scores[screen_id] = before + accepted
        self.current_score = sum(min(self.screen_scores.get(sid, 0), goal) for sid, goal in self.screen_targets.items())
        return {
            "accepted_points": accepted,
            "screen_score": self.screen_scores[screen_id],
            "screen_target": target,
            "screen_remaining": max(0, target - self.screen_scores[screen_id]),
            "screen_complete": self.is_screen_complete(screen_id),
            "all_screens_complete": self.all_screens_complete,
        }

    @property
    def progress_ratio(self) -> float:
        return 0.0 if self.target_score <= 0 else self.current_score / self.target_score

    @property
    def time_ratio(self) -> float:
        return 0.0 if self.total_seconds <= 0 else min(1.0, self.elapsed_seconds / self.total_seconds)

    @property
    def is_behind(self) -> bool:
        return self.progress_ratio < self.time_ratio

    def screen_status(self, screen_id: int) -> dict:
        score, target = self.get_screen_score(screen_id), self.get_screen_target(screen_id)
        return {"screen_id": int(screen_id), "screen_score": score, "screen_target": target,
                "screen_remaining": max(0, target - score), "screen_complete": self.is_screen_complete(screen_id)}

    def to_dict(self) -> dict:
        return {
            "child_count": self.child_count, "target_score": self.target_score,
            "current_score": self.current_score, "elapsed_seconds": self.elapsed_seconds,
            "total_seconds": self.total_seconds, "is_active": self.is_active,
            "countdown_active": self.countdown_active, "countdown_message": self.countdown_message,
            "countdown_remaining_ms": self.countdown_remaining_ms,
            "progress_percent": round(self.progress_ratio * 100, 1),
            "time_percent": round(self.time_ratio * 100, 1), "is_behind": self.is_behind,
            "total_spawns": self.total_spawns, "screen_count": self.screen_count,
            "screen_targets": dict(self.screen_targets), "screen_scores": dict(self.screen_scores),
            "completed_screens": self.completed_screens,
            "remaining_screen_count": len(self.incomplete_screens),
            "all_screens_complete": self.all_screens_complete,
        }

# ============== Adaptive Spawn Controller ==============

class AdaptiveSpawnController:
    """
    Oyun ilerledikçe zorluğu ayarlayan kontrolcü.
    Delta = time_ratio - progress_ratio
    """

    def __init__(
        self,
        base_spawn_interval: float = 3.0,
        min_spawn_interval: float = 0.5,
        max_spawn_interval: float = 8.0,
        max_concurrent_spawns: int = 3,
    ):
        self.base_spawn_interval = base_spawn_interval
        self.min_spawn_interval = min_spawn_interval
        self.max_spawn_interval = max_spawn_interval
        self.max_concurrent_spawns = max_concurrent_spawns

    def calculate(self, session: GameSession) -> dict:
        """
        Mevcut duruma göre spawn parametrelerini hesaplar.

        Returns:
            dict: spawn_interval, concurrent_spawns, delta, urgency
        """
        delta = session.time_ratio - session.progress_ratio

        # Spawn aralığı ve eşzamanlı spawn sayısı
        if delta > 0.2:  # Çok gerideyiz
            interval_multiplier = 0.4
            spawn_count_boost = 2
            urgency = 'HIGH'
        elif delta > 0.1:  # Biraz gerideyiz
            interval_multiplier = 0.6
            spawn_count_boost = 1
            urgency = 'MEDIUM'
        elif delta < -0.1:  # İlerideyiz
            interval_multiplier = 1.3
            spawn_count_boost = 0
            urgency = 'LOW'
        else:  # Dengedeyiz
            interval_multiplier = 1.0
            spawn_count_boost = 0
            urgency = 'NORMAL'

        new_interval = max(
            self.min_spawn_interval,
            min(self.max_spawn_interval,
                self.base_spawn_interval * interval_multiplier),
        )

        concurrent_spawns = min(
            self.max_concurrent_spawns,
            1 + spawn_count_boost,
        )

        return {
            'spawn_interval': new_interval,
            'concurrent_spawns': concurrent_spawns,
            'delta': round(delta, 3),
            'urgency': urgency,
        }


# ============== Phase-Based Spawner ==============

class PhaseBasedSpawner:
    """
    Oyun süresini 3 faza böler (süreye göre dinamik):
    - WARMUP  (ilk 1/3):  ×0.7 (yavaş)
    - NORMAL  (orta 1/3): ×1.0
    - INTENSE (son 1/3):  ×1.4 (hızlı)
    """

    def __init__(self, total_seconds: int = 20 * 60):
        third = total_seconds // 3
        self.phases = [
            {'name': 'WARMUP',  'start': 0,         'end': third,     'multiplier': 0.7},
            {'name': 'NORMAL',  'start': third,      'end': 2 * third, 'multiplier': 1.0},
            {'name': 'INTENSE', 'start': 2 * third,  'end': total_seconds, 'multiplier': 1.4},
        ]

    def get_phase(self, elapsed_seconds: int) -> dict:
        """Geçen süreye göre mevcut fazı döndür"""
        for phase in self.phases:
            if phase['start'] <= elapsed_seconds < phase['end']:
                return phase
        return self.phases[-1]  # Default son faz

    def apply_phase(self, base_interval: float, elapsed_seconds: int) -> float:
        """Faz çarpanını uygula: interval / multiplier"""
        phase = self.get_phase(elapsed_seconds)
        return base_interval / phase['multiplier']

    def get_spawn_count_hint(self, elapsed_seconds: int) -> int:
        """Faza göre eşzamanlı spawn sayısı ipucu"""
        phase = self.get_phase(elapsed_seconds)
        if phase['name'] == 'WARMUP':
            return 1
        elif phase['name'] == 'NORMAL':
            return random.choice([1, 1, 2])
        else:  # INTENSE
            return random.choice([2, 2, 3])


# ============== Piezo Config Manager ==============

class PiezoConfigManager:
    """Piezo threshold ve refractory değerlerini yönetir"""

    def __init__(self, threshold: int = 100, refractory_ms: int = 200):
        self.threshold = threshold
        self.refractory_ms = refractory_ms
        self._version = 0  # Her değişiklikte artırılır
        self._client_versions: Dict[int, int] = {}  # screen_id → en son aldığı versiyon

    def update(self, threshold: int, refractory_ms: int):
        """Değerleri güncelle"""
        self.threshold = threshold
        self.refractory_ms = refractory_ms
        self._version += 1

    def get_config(self) -> dict:
        """Mevcut ayarları döndür"""
        return {
            'threshold': self.threshold,
            'refractory_ms': self.refractory_ms,
            'version': self._version,
        }

    def poll(self, screen_id: int) -> Optional[dict]:
        """
        Client polling: değişiklik varsa yeni config döndür, yoksa None.
        """
        last_version = self._client_versions.get(screen_id, -1)
        if self._version > last_version:
            self._client_versions[screen_id] = self._version
            return self.get_config()
        return None


# ============== Spawn Scheduler ==============

class SpawnScheduler:
    """
    Ana koordinatör. Spawn loop thread'ini çalıştırır,
    her döngüde spawn parametrelerini hesaplar, hedef ekranları
    seçer ve spawn kuyruğuna ekler.
    """

    def __init__(
        self,
        session: GameSession,
        screen_selector: ScreenSelector,
        adaptive_controller: AdaptiveSpawnController,
        phase_spawner: PhaseBasedSpawner,
        debug: bool = False,
        on_session_end: Optional[Callable[[str], None]] = None,
        on_countdown_tick: Optional[Callable[[str], None]] = None,
        on_gameplay_start: Optional[Callable[[], None]] = None,
    ):
        self.session = session
        self.screen_selector = screen_selector
        self.adaptive = adaptive_controller
        self.phase = phase_spawner
        self.debug = debug
        self.on_session_end = on_session_end
        self.on_countdown_tick = on_countdown_tick
        self.on_gameplay_start = on_gameplay_start

        # Ekran başına spawn kuyruğu
        self.spawn_queues: Dict[int, queue.Queue] = {
            i: queue.Queue(maxsize=1) for i in range(1, session.screen_count + 1)
        }

        # Aktif ekran takibi (poll yapan ekranlar)
        self._active_screens: Dict[int, float] = {}  # screen_id -> last_poll_time
        self._active_timeout = 10.0  # 10 saniye poll yoksa pasif say

        # Thread kontrolü
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_spawn_time = 0.0
        self._lock = threading.RLock()
        self._end_notified = False
        self._last_countdown_message: Optional[str] = None
        self._gameplay_notified = False

    def start(self, countdown_seconds: int = 0):
        """Spawn loop thread'ini başlat"""
        with self._lock:
            self.session.countdown_seconds = max(0, int(countdown_seconds))
            self.session.start_time = time.time() + self.session.countdown_seconds
            self.session.is_active = True
            self._running = True
            self._last_spawn_time = 0  # İlk spawn hemen olsun
            self._end_notified = False
            self._last_countdown_message = None
            self._gameplay_notified = False
        self._thread = threading.Thread(target=self._spawn_loop, daemon=True)
        self._thread.start()

        if self.debug:
            print(f"[SpawnScheduler] Başlatıldı. Hedef: {self.session.target_score}")

    def resume(self):
        """Daha önce kaydedilmiş start_time değerini koruyarak loop'u sürdür."""
        with self._lock:
            self.session.is_active = True
            self._running = True
            self._last_spawn_time = 0
            self._end_notified = False
            self._last_countdown_message = None
            self._gameplay_notified = not self.session.countdown_active
        self._thread = threading.Thread(target=self._spawn_loop, daemon=True)
        self._thread.start()

        if self.debug:
            print(
                f"[SpawnScheduler] Oturum kurtarıldı. "
                f"Kalan: {max(0, self.session.total_seconds - self.session.elapsed_seconds)} sn"
            )

    def stop(self):
        """Spawn loop'u durdur"""
        with self._lock:
            self._running = False
            self.session.is_active = False
        if (
            self._thread
            and self._thread.is_alive()
            and threading.current_thread() is not self._thread
        ):
            self._thread.join(timeout=2.0)

        if self.debug:
            print("[SpawnScheduler] Durduruldu")

    def poll_spawn(self, screen_id: int) -> dict:
        """Ekranın tek komut kuyruğunu tüket; kotası dolduysa jail durumunu döndür."""
        with self._lock:
            if screen_id < 1 or screen_id > self.session.screen_count:
                return {"spawn": False}
            self._active_screens[screen_id] = time.time()
            status = self.session.screen_status(screen_id)
            if status["screen_complete"]:
                self._clear_spawn_queue(screen_id)
                return {"spawn": False, **status}
            try:
                self.spawn_queues[screen_id].get_nowait()
                return {"spawn": True, **status}
            except queue.Empty:
                return {"spawn": False, **status}

    @staticmethod
    def _drain_queue(target_queue: queue.Queue):
        while True:
            try:
                target_queue.get_nowait()
            except queue.Empty:
                return

    def _clear_spawn_queue(self, screen_id: int):
        target = self.spawn_queues.get(int(screen_id))
        if target is not None:
            self._drain_queue(target)

    def update_score(self, screen_id: int, points: int = 1) -> dict:
        """İlgili ekranın kotasını ilerlet ve tamamlanınca spawn kuyruğunu temizle."""
        with self._lock:
            result = self.session.record_screen_score(screen_id, points)
            if result.get("screen_complete"):
                self._clear_spawn_queue(screen_id)
            return result

    def reset_score(self):
        """Oturum toplamını ve sekiz ekran kotasını sıfırla."""
        with self._lock:
            self.session.current_score = 0
            self.session.screen_scores = {i: 0 for i in range(1, self.session.screen_count + 1)}
            for target in self.spawn_queues.values():
                self._drain_queue(target)
    def get_status(self) -> dict:
        """Mevcut durum bilgisi"""
        with self._lock:
            adaptive_params = self.adaptive.calculate(self.session)
            current_phase = self.phase.get_phase(self.session.elapsed_seconds)

            status = self.session.to_dict()
            status.update({
                'phase': (
                    'COUNTDOWN'
                    if self.session.countdown_active
                    else current_phase['name']
                ),
                'phase_multiplier': current_phase['multiplier'],
                'spawn_interval': adaptive_params['spawn_interval'],
                'concurrent_spawns': adaptive_params['concurrent_spawns'],
                'delta': adaptive_params['delta'],
                'urgency': adaptive_params['urgency'],
                'screen_stats': self.screen_selector.get_stats(),
            })
            return status

    def _spawn_loop(self):
        """Arka planda çalışan spawn döngüsü"""
        while self._running and self.session.is_active:
            try:
                if self.session.countdown_active:
                    message = self.session.countdown_message
                    if message and message != self._last_countdown_message:
                        self._last_countdown_message = message
                        if self.on_countdown_tick:
                            self.on_countdown_tick(message)
                    time.sleep(0.03)
                    continue

                if not self._gameplay_notified:
                    self._gameplay_notified = True
                    if self.on_gameplay_start:
                        self.on_gameplay_start()

                # Süre kontrolü
                with self._lock:
                    elapsed_done = self.session.elapsed_seconds >= self.session.total_seconds
                    target_done = self.session.all_screens_complete

                if elapsed_done:
                    self._finish_session("timeout")
                    break

                # Hedef tamamlandı mı?
                if target_done:
                    if self.debug:
                        print("[SpawnScheduler] Hedef tamamlandı!")
                    self._finish_session("target")
                    break

                # Adaptive parametreleri hesapla
                params = self.adaptive.calculate(self.session)

                # Faz bazlı ayarlama
                phase_interval = self.phase.apply_phase(
                    params['spawn_interval'],
                    self.session.elapsed_seconds,
                )

                # Spawn zamanı mı?
                time_since_last = time.time() - self._last_spawn_time
                if time_since_last >= phase_interval:
                    self._trigger_spawn(params)

                time.sleep(0.1)  # 100ms kontrol aralığı

            except Exception as e:
                if self.debug:
                    print(f"[SpawnScheduler] Hata: {e}")
                time.sleep(1)

    def _finish_session(self, reason: str):
        """Oturumu bir kez bitir ve merkezi callback'i çağır."""
        with self._lock:
            if self._end_notified:
                return
            self._end_notified = True

        self.stop()
        if self.on_session_end:
            try:
                self.on_session_end(reason)
            except Exception as e:
                if self.debug:
                    print(f"[SpawnScheduler] Bitiş callback hatası: {e}")

    def _get_active_screen_ids(self) -> List[int]:
        """Kotası açık ve yakın zamanda poll yapan bağlı ekranları döndür."""
        with self._lock:
            now = time.time()
            incomplete = set(self.session.incomplete_screens)
            return [
                screen_id
                for screen_id, last_poll in self._active_screens.items()
                if screen_id in incomplete and now - last_poll < self._active_timeout
            ]

    def _trigger_spawn(self, params: dict):
        """Hırsız spawn et"""
        concurrent = params['concurrent_spawns']

        # Faz ipucunu da dikkate al
        phase_hint = self.phase.get_spawn_count_hint(
            self.session.elapsed_seconds
        )
        concurrent = max(concurrent, phase_hint)
        concurrent = min(concurrent, self.adaptive.max_concurrent_spawns)

        # Kotası dolmamış ve gerçekten bağlı ekranları kullan
        active_screens = self._get_active_screen_ids()

        with self._lock:
            selected = self.screen_selector.select_screens(concurrent, active_screens)
            queued = []
            for screen_id in selected:
                if screen_id not in self.spawn_queues:
                    self.spawn_queues[screen_id] = queue.Queue(maxsize=1)
                try:
                    self.spawn_queues[screen_id].put_nowait({"spawn": True})
                    queued.append(screen_id)
                except queue.Full:
                    continue

            self.session.total_spawns += len(queued)
            self._last_spawn_time = time.time()

        if self.debug:
            print(
                f"[SpawnScheduler] SPAWN → Ekranlar: {queued} | "
                f"Aktif: {active_screens} | "
                f"Urgency: {params['urgency']} | "
                f"Interval: {params['spawn_interval']:.1f}s"
            )
