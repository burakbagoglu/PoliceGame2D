"""
Spawn Engine - Server kontrollü hırsız spawn algoritmaları

Çocuk sayısına göre adaptive zorluk ayarlayan, fair-distribution
ile ekran seçen ve faz bazlı spawn yönetimi yapan modül.
"""
import time
import random
import threading
import queue
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional


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


# ============== Screen Selector ==============

class ScreenSelector:
    """
    Hangi ekranda hırsız çıkacağını belirler.
    Fair distribution + Round-robin + Randomness
    """

    def __init__(self, screen_count: int = 12):
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
    """Aktif oyun durumunu tutan sınıf"""
    child_count: int
    target_score: int
    screen_count: int = 12
    current_score: int = 0
    start_time: float = 0.0
    is_active: bool = False
    total_seconds: int = 20 * 60  # 20 dakika (varsayılan)
    total_spawns: int = 0

    @property
    def elapsed_seconds(self) -> int:
        if not self.is_active or self.start_time == 0:
            return 0
        return int(time.time() - self.start_time)

    @property
    def progress_ratio(self) -> float:
        """Hedefin ne kadarı tamamlandı (0.0 - 1.0+)"""
        if self.target_score <= 0:
            return 0.0
        return self.current_score / self.target_score

    @property
    def time_ratio(self) -> float:
        """Zamanın ne kadarı geçti (0.0 - 1.0)"""
        if self.total_seconds <= 0:
            return 0.0
        return min(1.0, self.elapsed_seconds / self.total_seconds)

    @property
    def is_behind(self) -> bool:
        """Hedef gerisinde miyiz?"""
        return self.progress_ratio < self.time_ratio

    def to_dict(self) -> dict:
        return {
            'child_count': self.child_count,
            'target_score': self.target_score,
            'current_score': self.current_score,
            'elapsed_seconds': self.elapsed_seconds,
            'total_seconds': self.total_seconds,
            'is_active': self.is_active,
            'progress_percent': round(self.progress_ratio * 100, 1),
            'time_percent': round(self.time_ratio * 100, 1),
            'is_behind': self.is_behind,
            'total_spawns': self.total_spawns,
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
    ):
        self.session = session
        self.screen_selector = screen_selector
        self.adaptive = adaptive_controller
        self.phase = phase_spawner
        self.debug = debug

        # Ekran başına spawn kuyruğu
        self.spawn_queues: Dict[int, queue.Queue] = {
            i: queue.Queue() for i in range(1, session.screen_count + 1)
        }

        # Thread kontrolü
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_spawn_time = 0.0

    def start(self):
        """Spawn loop thread'ini başlat"""
        self.session.start_time = time.time()
        self.session.is_active = True
        self._running = True
        self._last_spawn_time = time.time()
        self._thread = threading.Thread(target=self._spawn_loop, daemon=True)
        self._thread.start()

        if self.debug:
            print(f"[SpawnScheduler] Başlatıldı. Hedef: {self.session.target_score}")

    def stop(self):
        """Spawn loop'u durdur"""
        self._running = False
        self.session.is_active = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

        if self.debug:
            print("[SpawnScheduler] Durduruldu")

    def poll_spawn(self, screen_id: int) -> dict:
        """
        Client'ın spawn kontrolü. Kuyruğunda spawn varsa consume eder.

        Returns:
            {"spawn": True/False}
        """
        if screen_id not in self.spawn_queues:
            return {"spawn": False}

        try:
            self.spawn_queues[screen_id].get_nowait()
            return {"spawn": True}
        except queue.Empty:
            return {"spawn": False}

    def update_score(self, points: int = 1):
        """Skor güncelle (hit geldiğinde çağrılır)"""
        self.session.current_score += points

    def get_status(self) -> dict:
        """Mevcut durum bilgisi"""
        adaptive_params = self.adaptive.calculate(self.session)
        current_phase = self.phase.get_phase(self.session.elapsed_seconds)

        status = self.session.to_dict()
        status.update({
            'phase': current_phase['name'],
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
                # Süre kontrolü
                if self.session.elapsed_seconds >= self.session.total_seconds:
                    self.stop()
                    break

                # Hedef tamamlandı mı?
                if self.session.current_score >= self.session.target_score:
                    if self.debug:
                        print("[SpawnScheduler] Hedef tamamlandı!")
                    self.stop()
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

    def _trigger_spawn(self, params: dict):
        """Hırsız spawn et"""
        concurrent = params['concurrent_spawns']

        # Faz ipucunu da dikkate al
        phase_hint = self.phase.get_spawn_count_hint(
            self.session.elapsed_seconds
        )
        concurrent = max(concurrent, phase_hint)
        concurrent = min(concurrent, self.adaptive.max_concurrent_spawns)

        # Aktif ekranları al (şimdilik tümü aktif)
        active_screens = list(range(1, self.session.screen_count + 1))

        selected = self.screen_selector.select_screens(concurrent, active_screens)

        # Seçilen ekranların kuyruğuna spawn ekle
        for screen_id in selected:
            if screen_id in self.spawn_queues:
                self.spawn_queues[screen_id].put({"spawn": True})

        self.session.total_spawns += len(selected)
        self._last_spawn_time = time.time()

        if self.debug:
            print(
                f"[SpawnScheduler] SPAWN → Ekranlar: {selected} | "
                f"Urgency: {params['urgency']} | "
                f"Interval: {params['spawn_interval']:.1f}s"
            )
