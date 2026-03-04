"""
Spawn Engine testleri - Algoritma sınıflarının birim testleri
"""
import time
import pytest
from unittest.mock import patch

from spawn_engine import (
    TargetCalculator,
    ScreenSelector,
    AdaptiveSpawnController,
    PhaseBasedSpawner,
    PiezoConfigManager,
    GameSession,
    SpawnScheduler,
)


# ============== TargetCalculator ==============

class TestTargetCalculator:
    def test_normal_difficulty(self):
        calc = TargetCalculator(base_score_per_child=15)
        result = calc.calculate(3, 'normal')
        assert result['total_target'] == 45
        assert result['child_count'] == 3

    def test_easy_difficulty(self):
        calc = TargetCalculator(base_score_per_child=15)
        result = calc.calculate(3, 'easy')
        assert result['total_target'] == 36  # 45 * 0.8

    def test_hard_difficulty(self):
        calc = TargetCalculator(base_score_per_child=15)
        result = calc.calculate(5, 'hard')
        assert result['total_target'] == 97  # int(75 * 1.3)

    def test_per_minute(self):
        calc = TargetCalculator(base_score_per_child=15)
        result = calc.calculate(3, 'normal', duration_minutes=45)
        assert result['per_minute'] == 1.0  # 45/45

    def test_large_child_count(self):
        calc = TargetCalculator(base_score_per_child=15)
        result = calc.calculate(8, 'normal')
        assert result['total_target'] == 120

    def test_custom_base_score(self):
        calc = TargetCalculator(base_score_per_child=20)
        result = calc.calculate(3, 'normal')
        assert result['total_target'] == 60


# ============== ScreenSelector ==============

class TestScreenSelector:
    def test_select_single_screen(self):
        selector = ScreenSelector(screen_count=12)
        result = selector.select_screens(1)
        assert len(result) == 1
        assert 1 <= result[0] <= 12

    def test_select_multiple_screens(self):
        selector = ScreenSelector(screen_count=12)
        result = selector.select_screens(3)
        assert len(result) == 3
        assert len(set(result)) == 3  # Hepsi farklı

    def test_fair_distribution(self):
        selector = ScreenSelector(screen_count=4)
        # 8 spawn → her ekrana 2
        for _ in range(8):
            selector.select_screens(1)

        stats = selector.get_stats()
        for pct in stats.values():
            assert pct == 25.0  # Eşit dağılım

    def test_no_duplicate_in_single_spawn(self):
        selector = ScreenSelector(screen_count=5)
        for _ in range(20):
            result = selector.select_screens(3)
            assert len(result) == len(set(result))

    def test_respects_active_screens(self):
        selector = ScreenSelector(screen_count=12)
        result = selector.select_screens(2, active_screens=[1, 2, 3])
        assert all(s in [1, 2, 3] for s in result)

    def test_empty_active_screens(self):
        selector = ScreenSelector(screen_count=12)
        result = selector.select_screens(2, active_screens=[])
        assert result == []

    def test_spawn_count_exceeds_screens(self):
        selector = ScreenSelector(screen_count=3)
        result = selector.select_screens(5)
        assert len(result) == 3  # En fazla 3

    def test_reset(self):
        selector = ScreenSelector(screen_count=4)
        selector.select_screens(2)
        selector.reset()
        assert all(v == 0 for v in selector.screen_spawn_counts.values())
        assert len(selector.spawn_history) == 0


# ============== GameSession ==============

class TestGameSession:
    def test_progress_ratio(self):
        session = GameSession(child_count=3, target_score=45, current_score=15)
        assert abs(session.progress_ratio - 1 / 3) < 0.01

    def test_progress_ratio_zero_target(self):
        session = GameSession(child_count=0, target_score=0)
        assert session.progress_ratio == 0.0

    def test_time_ratio_not_started(self):
        session = GameSession(child_count=3, target_score=45)
        assert session.time_ratio == 0.0

    def test_is_behind(self):
        session = GameSession(
            child_count=3,
            target_score=45,
            current_score=0,
            start_time=time.time() - 1350,  # 50% süre geçmiş
            is_active=True,
        )
        # 0 skor, %50 süre → geride
        assert session.is_behind is True

    def test_not_behind(self):
        session = GameSession(
            child_count=3,
            target_score=45,
            current_score=30,
            start_time=time.time() - 400,  # 400s geçmiş
            is_active=True,
            total_seconds=1200,
        )
        # 30/45 = %66 skor, 400/1200 = %33 süre → ileride
        assert session.is_behind is False

    def test_to_dict(self):
        session = GameSession(child_count=3, target_score=45, current_score=10)
        d = session.to_dict()
        assert d['child_count'] == 3
        assert d['target_score'] == 45
        assert d['current_score'] == 10
        assert 'progress_percent' in d


# ============== AdaptiveSpawnController ==============

class TestAdaptiveSpawnController:
    def test_behind_high(self):
        """Çok geride → hızlı spawn"""
        ctrl = AdaptiveSpawnController(base_spawn_interval=3.0)
        session = GameSession(
            child_count=3, target_score=45, current_score=0,
            start_time=time.time() - 1350, is_active=True,
        )
        result = ctrl.calculate(session)
        assert result['urgency'] == 'HIGH'
        assert result['spawn_interval'] < 3.0

    def test_behind_medium(self):
        """Biraz geride"""
        ctrl = AdaptiveSpawnController(base_spawn_interval=3.0)
        session = GameSession(
            child_count=3, target_score=45, current_score=10,
            start_time=time.time() - 1350, is_active=True,
        )
        result = ctrl.calculate(session)
        assert result['urgency'] in ('MEDIUM', 'HIGH')

    def test_ahead(self):
        """İleride → yavaş spawn"""
        ctrl = AdaptiveSpawnController(base_spawn_interval=3.0)
        session = GameSession(
            child_count=3, target_score=45, current_score=40,
            start_time=time.time() - 300, is_active=True,
            total_seconds=1200,
        )
        result = ctrl.calculate(session)
        assert result['urgency'] == 'LOW'
        assert result['spawn_interval'] > 3.0

    def test_balanced(self):
        """Dengede"""
        ctrl = AdaptiveSpawnController(base_spawn_interval=3.0)
        session = GameSession(
            child_count=3, target_score=45, current_score=15,
            start_time=time.time() - 400, is_active=True,
            total_seconds=1200,
        )
        # 15/45 = 0.33 skor, 400/1200 = 0.33 süre → dengede
        result = ctrl.calculate(session)
        assert result['urgency'] == 'NORMAL'

    def test_interval_bounds(self):
        """Interval min/max sınırları"""
        ctrl = AdaptiveSpawnController(
            base_spawn_interval=3.0,
            min_spawn_interval=0.5,
            max_spawn_interval=8.0,
        )
        session = GameSession(
            child_count=3, target_score=45, current_score=0,
            start_time=time.time() - 2700, is_active=True,
        )
        result = ctrl.calculate(session)
        assert result['spawn_interval'] >= 0.5
        assert result['spawn_interval'] <= 8.0


# ============== PhaseBasedSpawner ==============

class TestPhaseBasedSpawner:
    def test_warmup_phase(self):
        spawner = PhaseBasedSpawner(total_seconds=1200)  # 20dk
        phase = spawner.get_phase(100)  # ~1.5dk
        assert phase['name'] == 'WARMUP'
        assert phase['multiplier'] == 0.7

    def test_normal_phase(self):
        spawner = PhaseBasedSpawner(total_seconds=1200)  # 20dk
        phase = spawner.get_phase(500)  # ~8dk
        assert phase['name'] == 'NORMAL'
        assert phase['multiplier'] == 1.0

    def test_intense_phase(self):
        spawner = PhaseBasedSpawner(total_seconds=1200)  # 20dk
        phase = spawner.get_phase(1000)  # ~16dk
        assert phase['name'] == 'INTENSE'
        assert phase['multiplier'] == 1.4

    def test_apply_phase_warmup(self):
        spawner = PhaseBasedSpawner(total_seconds=1200)
        result = spawner.apply_phase(3.0, 100)
        assert abs(result - 3.0 / 0.7) < 0.01  # Yavaşlatır

    def test_apply_phase_intense(self):
        spawner = PhaseBasedSpawner(total_seconds=1200)
        result = spawner.apply_phase(3.0, 1000)
        assert abs(result - 3.0 / 1.4) < 0.01  # Hızlandırır

    def test_spawn_count_warmup(self):
        spawner = PhaseBasedSpawner(total_seconds=1200)
        assert spawner.get_spawn_count_hint(100) == 1

    def test_spawn_count_intense(self):
        spawner = PhaseBasedSpawner(total_seconds=1200)
        count = spawner.get_spawn_count_hint(1000)
        assert count in (2, 3)


# ============== PiezoConfigManager ==============

class TestPiezoConfigManager:
    def test_initial_config(self):
        mgr = PiezoConfigManager(threshold=100, refractory_ms=200)
        config = mgr.get_config()
        assert config['threshold'] == 100
        assert config['refractory_ms'] == 200

    def test_update(self):
        mgr = PiezoConfigManager()
        mgr.update(150, 300)
        config = mgr.get_config()
        assert config['threshold'] == 150
        assert config['refractory_ms'] == 300

    def test_poll_first_time(self):
        mgr = PiezoConfigManager(threshold=100, refractory_ms=200)
        result = mgr.poll(screen_id=1)
        assert result is not None
        assert result['threshold'] == 100

    def test_poll_no_change(self):
        mgr = PiezoConfigManager()
        mgr.poll(screen_id=1)  # İlk poll
        result = mgr.poll(screen_id=1)  # İkinci poll, değişiklik yok
        assert result is None

    def test_poll_after_update(self):
        mgr = PiezoConfigManager()
        mgr.poll(screen_id=1)  # İlk poll
        mgr.update(200, 400)
        result = mgr.poll(screen_id=1)
        assert result is not None
        assert result['threshold'] == 200

    def test_poll_per_screen(self):
        mgr = PiezoConfigManager()
        r1 = mgr.poll(screen_id=1)
        r2 = mgr.poll(screen_id=2)
        assert r1 is not None
        assert r2 is not None


# ============== SpawnScheduler ==============

class TestSpawnScheduler:
    def _make_scheduler(self, target_score=45, screen_count=4):
        session = GameSession(
            child_count=3,
            target_score=target_score,
            screen_count=screen_count,
        )
        selector = ScreenSelector(screen_count)
        adaptive = AdaptiveSpawnController()
        phase = PhaseBasedSpawner()
        return SpawnScheduler(
            session=session,
            screen_selector=selector,
            adaptive_controller=adaptive,
            phase_spawner=phase,
        )

    def test_poll_spawn_empty(self):
        scheduler = self._make_scheduler()
        result = scheduler.poll_spawn(1)
        assert result['spawn'] is False

    def test_poll_spawn_with_event(self):
        scheduler = self._make_scheduler()
        # Manuel olarak kuyruğa ekle
        scheduler.spawn_queues[1].put({"spawn": True})
        result = scheduler.poll_spawn(1)
        assert result['spawn'] is True

    def test_poll_spawn_consumes(self):
        scheduler = self._make_scheduler()
        scheduler.spawn_queues[1].put({"spawn": True})
        scheduler.poll_spawn(1)  # Consume
        result = scheduler.poll_spawn(1)  # Boş olmalı
        assert result['spawn'] is False

    def test_update_score(self):
        scheduler = self._make_scheduler()
        scheduler.update_score(5)
        assert scheduler.session.current_score == 5

    def test_get_status(self):
        scheduler = self._make_scheduler()
        scheduler.session.start_time = time.time()
        scheduler.session.is_active = True
        status = scheduler.get_status()
        assert 'phase' in status
        assert 'urgency' in status
        assert 'spawn_interval' in status

    def test_invalid_screen_poll(self):
        scheduler = self._make_scheduler(screen_count=4)
        result = scheduler.poll_spawn(99)
        assert result['spawn'] is False

    def test_start_stop(self):
        scheduler = self._make_scheduler()
        scheduler.start()
        assert scheduler.session.is_active is True
        time.sleep(0.2)
        scheduler.stop()
        assert scheduler.session.is_active is False
