"""
Game Logic testleri - GameLogic sınıfı birim testleri
Pygame gerektirmez.
"""
import time
import pytest
import sys
import os

# Modül yolunu ekle
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

from lib.game import GameLogic, GameState, Direction


def make_game(server_controlled=False, band_enabled=False, **kwargs):
    """Test için GameLogic oluştur"""
    defaults = dict(
        spawn_x=1920,
        reset_x=-200,
        thief_y=500,
        speed_px_s=360,
        random_direction=False,
        band_enabled=band_enabled,
        band_x_min=900,
        band_x_max=1020,
        hit_cooldown_ms=200,
        screen_width=1920,
        server_controlled=server_controlled,
        debug=False,
    )
    defaults.update(kwargs)
    return GameLogic(**defaults)


# ============== IDLE State ==============

class TestIdleState:
    def test_starts_idle_when_server_controlled(self):
        game = make_game(server_controlled=True)
        assert game.thief.state == GameState.IDLE

    def test_starts_run_when_not_server_controlled(self):
        game = make_game(server_controlled=False)
        assert game.thief.state == GameState.RUN

    def test_is_idle(self):
        game = make_game(server_controlled=True)
        assert game.is_idle() is True
        assert game.is_running() is False

    def test_idle_no_movement(self):
        game = make_game(server_controlled=True)
        initial_x = game.thief.x
        game.update(1.0)
        assert game.thief.x == initial_x


# ============== Trigger Spawn ==============

class TestTriggerSpawn:
    def test_trigger_spawn_from_idle(self):
        game = make_game(server_controlled=True)
        game.trigger_spawn()
        assert game.thief.state == GameState.RUN

    def test_trigger_spawn_not_idle_ignored(self):
        game = make_game(server_controlled=False)
        assert game.thief.state == GameState.RUN
        game.trigger_spawn()  # RUN iken çağrılırsa görmezden gel
        assert game.thief.state == GameState.RUN

    def test_trigger_spawn_sets_position(self):
        game = make_game(server_controlled=True)
        game.trigger_spawn()
        # Sağdan başlamalı (direction=LEFT)
        assert game.thief.x == 1920

    def test_trigger_spawn_direction_callback(self):
        directions = []
        game = make_game(
            server_controlled=True,
            on_direction_change=lambda d: directions.append(d),
        )
        game.trigger_spawn()
        assert len(directions) == 1


# ============== Reset Behavior ==============

class TestResetBehavior:
    def test_reset_to_idle_when_server_controlled(self):
        game = make_game(server_controlled=True)
        game.trigger_spawn()
        # Hırsızı ekran dışına çıkar
        game.thief.x = -300
        game.update(0.01)
        assert game.thief.state == GameState.RESET
        game.update(0.01)
        assert game.thief.state == GameState.IDLE

    def test_reset_to_run_when_not_server_controlled(self):
        game = make_game(server_controlled=False)
        game.thief.x = -300
        game.update(0.01)
        assert game.thief.state == GameState.RESET
        game.update(0.01)
        assert game.thief.state == GameState.RUN


# ============== Hit Processing ==============

class TestHitProcessing:
    def test_hit_during_run(self):
        game = make_game(server_controlled=False)
        result = game.process_hit()
        assert result is True
        assert game.score == 1
        assert game.thief.state == GameState.FALL

    def test_hit_during_idle(self):
        game = make_game(server_controlled=True)
        result = game.process_hit()
        assert result is False
        assert game.score == 0

    def test_hit_with_band_in_band(self):
        game = make_game(band_enabled=True)
        game.thief.x = 960  # Band içi (900-1020)
        result = game.process_hit()
        assert result is True

    def test_hit_with_band_outside_band(self):
        game = make_game(band_enabled=True)
        game.thief.x = 500  # Band dışı
        result = game.process_hit()
        assert result is False

    def test_score_callback(self):
        scores = []
        game = make_game(on_score=lambda p: scores.append(p))
        game.process_hit()
        assert scores == [1]

    def test_hit_during_fall_rejected(self):
        game = make_game()
        game.process_hit()  # FALL'a geç
        result = game.process_hit()  # FALL iken tekrar
        assert result is False
        assert game.score == 1


# ============== Fall → Cooldown → Reset ==============

class TestFallCooldown:
    def test_fall_to_cooldown(self):
        game = make_game()
        game.process_hit()
        assert game.thief.state == GameState.FALL
        # Fall süresini simüle et
        game.thief.fall_start = time.time() - 1.0  # 1 saniye önce
        game.update(0.01)
        assert game.thief.state == GameState.COOLDOWN

    def test_cooldown_to_reset(self):
        game = make_game()
        game.process_hit()
        game.thief.fall_start = time.time() - 1.0
        game.update(0.01)  # → COOLDOWN
        game.thief.cooldown_end = time.time() - 0.1
        game.update(0.01)  # → RESET
        assert game.thief.state == GameState.RESET


# ============== Movement ==============

class TestMovement:
    def test_moves_left(self):
        game = make_game()
        initial_x = game.thief.x
        game.update(1.0)
        assert game.thief.x < initial_x

    def test_exits_screen_left(self):
        game = make_game()
        game.thief.x = -300
        game.update(0.01)
        assert game.thief.state == GameState.RESET

    def test_direction_name(self):
        game = make_game()
        assert game.get_direction_name() == "SOLA"
        assert game.get_direction() == -1
