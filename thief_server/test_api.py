"""
Server API endpoint testleri - FastAPI TestClient ile
"""
import time
import pytest
from fastapi.testclient import TestClient

from main import app, audio_manager, score_manager, piezo_config, spawn_scheduler


@pytest.fixture(autouse=True)
def reset_state():
    """Her test öncesi state'i sıfırla"""
    import main
    score_manager.reset()
    piezo_config.__init__(threshold=100, refractory_ms=200)
    with main.active_polling_lock:
        main.active_polling_screens.clear()
    if main.spawn_scheduler:
        main.spawn_scheduler.stop()
        main.spawn_scheduler = None
    yield
    if main.spawn_scheduler:
        main.spawn_scheduler.stop()
        main.spawn_scheduler = None


client = TestClient(app)


# ============== Game Lifecycle ==============

class TestGameAPI:
    def test_start_game(self):
        res = client.post("/api/game/start", json={
            "child_count": 3,
            "screen_count": 5,
            "difficulty": "normal",
        })
        assert res.status_code == 200
        data = res.json()
        assert data['success'] is True
        assert data['target_score'] == 45
        assert data['child_count'] == 3

    def test_start_game_hard(self):
        res = client.post("/api/game/start", json={
            "child_count": 5,
            "screen_count": 12,
            "difficulty": "hard",
        })
        data = res.json()
        assert data['target_score'] == 97  # int(75 * 1.3)

    @pytest.mark.parametrize("payload", [
        {"child_count": 0},
        {"child_count": 3, "screen_count": 0},
        {"child_count": 3, "screen_count": 13},
        {"child_count": 3, "duration_minutes": 0},
        {"child_count": 3, "difficulty": "impossible"},
    ])
    def test_start_game_rejects_invalid_values(self, payload):
        res = client.post("/api/game/start", json=payload)
        assert res.status_code == 422

    def test_game_status_no_game(self):
        res = client.get("/api/game/status")
        assert res.status_code == 200
        data = res.json()
        assert data['is_active'] is False

    def test_game_status_active(self):
        client.post("/api/game/start", json={"child_count": 3})
        res = client.get("/api/game/status")
        data = res.json()
        assert data['is_active'] is True
        assert 'phase' in data

    def test_end_game(self):
        client.post("/api/game/start", json={"child_count": 3})
        res = client.post("/api/game/end")
        data = res.json()
        assert data['success'] is True

    def test_end_game_no_game(self):
        res = client.post("/api/game/end")
        data = res.json()
        assert data['success'] is False

    def test_game_lifecycle_controls_server_audio(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            audio_manager,
            "start_game",
            lambda: calls.append(("start", None)),
        )
        monkeypatch.setattr(
            audio_manager,
            "end_game",
            lambda completed: calls.append(("end", completed)),
        )

        client.post("/api/game/start", json={"child_count": 3})
        client.post("/api/game/end")

        assert calls == [("start", None), ("end", False)]


# ============== Spawn Polling ==============

class TestSpawnPoll:
    def test_poll_no_game(self):
        res = client.get("/spawn/poll?screen_id=1")
        data = res.json()
        assert data['spawn'] is False
        assert data['game_active'] is False

    def test_poll_with_game(self):
        client.post("/api/game/start", json={"child_count": 3, "screen_count": 5})
        # Henüz spawn olmamış olabilir, ama endpoint çalışmalı
        res = client.get("/spawn/poll?screen_id=1")
        assert res.status_code == 200
        data = res.json()
        assert data['game_active'] is True
        assert 'spawn' in data

    def test_poll_screen_outside_active_session_is_safe(self):
        client.get("/spawn/poll?screen_id=12")
        client.post("/api/game/start", json={"child_count": 2, "screen_count": 5})

        res = client.get("/spawn/poll?screen_id=12")
        assert res.status_code == 200
        assert res.json()['spawn'] is False
        assert res.json()['participating'] is False

    @pytest.mark.parametrize("screen_id", [0, 13])
    def test_poll_rejects_invalid_screen_id(self, screen_id):
        res = client.get(f"/spawn/poll?screen_id={screen_id}")
        assert res.status_code == 422


# ============== Score Events ==============

class TestScoreAPI:
    def test_send_event(self):
        res = client.post("/event", json={
            "event_id": "test-001",
            "screen_id": 1,
            "points": 1,
            "ts_ms": int(time.time() * 1000),
        })
        data = res.json()
        assert data['success'] is True
        assert data['is_new'] is True
        assert data['total_score'] == 1

    @pytest.mark.parametrize("field,value", [
        ("screen_id", 0),
        ("screen_id", 13),
        ("points", 0),
        ("points", -1),
        ("points", 101),
        ("event_id", ""),
        ("ts_ms", 0),
    ])
    def test_rejects_invalid_event_values(self, field, value):
        event = {
            "event_id": "validation-001",
            "screen_id": 1,
            "points": 1,
            "ts_ms": int(time.time() * 1000),
        }
        event[field] = value
        res = client.post("/event", json=event)
        assert res.status_code == 422

    def test_rejects_score_from_screen_outside_active_session(self):
        client.post("/api/game/start", json={"child_count": 2, "screen_count": 5})
        res = client.post("/event", json={
            "event_id": "outside-session-001",
            "screen_id": 12,
            "points": 1,
            "ts_ms": int(time.time() * 1000),
        })
        assert res.status_code == 409
        assert client.get("/score").json()["total_score"] == 0

    def test_duplicate_event(self):
        event = {
            "event_id": "dup-001",
            "screen_id": 1,
            "points": 1,
            "ts_ms": int(time.time() * 1000),
        }
        client.post("/event", json=event)
        res = client.post("/event", json=event)
        data = res.json()
        assert data['is_new'] is False

    def test_hit_sound_only_plays_for_new_event(self, monkeypatch):
        calls = []
        monkeypatch.setattr(audio_manager, "play_hit", lambda: calls.append("hit"))
        event = {
            "event_id": "audio-hit-001",
            "screen_id": 1,
            "points": 1,
            "ts_ms": int(time.time() * 1000),
        }

        client.post("/event", json=event)
        client.post("/event", json=event)

        assert calls == ["hit"]

    def test_hit_updates_scheduler_score(self):
        client.post("/api/game/start", json={"child_count": 3, "screen_count": 5})
        client.post("/event", json={
            "event_id": "hit-001",
            "screen_id": 1,
            "points": 1,
            "ts_ms": int(time.time() * 1000),
        })
        res = client.get("/api/game/status")
        data = res.json()
        assert data['current_score'] == 1

    def test_get_score(self):
        client.post("/event", json={
            "event_id": "score-001",
            "screen_id": 1,
            "points": 5,
            "ts_ms": int(time.time() * 1000),
        })
        res = client.get("/score")
        data = res.json()
        assert data['total_score'] == 5

    def test_reset_scores(self):
        client.post("/event", json={
            "event_id": "reset-001",
            "screen_id": 1,
            "points": 10,
            "ts_ms": int(time.time() * 1000),
        })
        client.post("/reset")
        res = client.get("/score")
        assert res.json()['total_score'] == 0
        assert res.json()['score_version'] > 0

    def test_reset_updates_scheduler_score(self):
        client.post("/api/game/start", json={"child_count": 3, "screen_count": 5})
        client.post("/event", json={
            "event_id": "reset-scheduler-001",
            "screen_id": 1,
            "points": 5,
            "ts_ms": int(time.time() * 1000),
        })
        client.post("/reset")
        res = client.get("/api/game/status")
        assert res.json()['current_score'] == 0

    def test_history(self):
        client.post("/event", json={
            "event_id": "hist-001",
            "screen_id": 2,
            "points": 1,
            "ts_ms": int(time.time() * 1000),
        })
        res = client.get("/history")
        data = res.json()
        assert data['count'] == 1
        assert data['events'][0]['screen_id'] == 2


# ============== Piezo Config ==============

class TestPiezoConfigAPI:
    def test_get_config(self):
        res = client.get("/api/piezo/config")
        data = res.json()
        assert data['threshold'] == 100
        assert data['refractory_ms'] == 200

    def test_set_config(self):
        res = client.post("/api/piezo/config", json={
            "threshold": 150,
            "refractory_ms": 300,
        })
        data = res.json()
        assert data['success'] is True
        assert data['threshold'] == 150

    def test_set_config_invalid_threshold(self):
        res = client.post("/api/piezo/config", json={
            "threshold": 2000,
            "refractory_ms": 200,
        })
        assert res.status_code == 400

    def test_poll_config_first(self):
        res = client.get("/api/piezo/config/poll?screen_id=1")
        data = res.json()
        assert data['changed'] is True

    def test_poll_config_no_change(self):
        client.get("/api/piezo/config/poll?screen_id=1")  # İlk poll
        res = client.get("/api/piezo/config/poll?screen_id=1")  # Değişiklik yok
        data = res.json()
        assert data['changed'] is False

    def test_poll_config_after_update(self):
        client.get("/api/piezo/config/poll?screen_id=1")  # İlk poll
        client.post("/api/piezo/config", json={"threshold": 200, "refractory_ms": 400})
        res = client.get("/api/piezo/config/poll?screen_id=1")
        data = res.json()
        assert data['changed'] is True
        assert data['threshold'] == 200


# ============== Server Audio ==============

class TestAudioAPI:
    def test_get_audio_status(self, monkeypatch):
        monkeypatch.setattr(audio_manager, "get_status", lambda: {
            "enabled": True,
            "available": True,
            "device_active": "USB Audio Device",
        })
        res = client.get("/api/audio/status")
        assert res.status_code == 200
        assert res.json()["device_active"] == "USB Audio Device"

    def test_set_audio_config(self, monkeypatch):
        captured = {}

        def configure(**kwargs):
            captured.update(kwargs)
            return {"enabled": kwargs["enabled"], "available": True}

        monkeypatch.setattr(audio_manager, "configure", configure)
        res = client.post("/api/audio/config", json={
            "enabled": True,
            "master_volume": 0.7,
            "music_volume": 0.3,
            "sfx_volume": 0.9,
        })

        assert res.status_code == 200
        assert res.json()["success"] is True
        assert captured["master_volume"] == 0.7

    @pytest.mark.parametrize("field,value", [
        ("master_volume", -0.1),
        ("master_volume", 1.1),
        ("music_volume", 1.1),
        ("sfx_volume", -0.1),
    ])
    def test_rejects_invalid_audio_volume(self, field, value):
        payload = {
            "enabled": True,
            "master_volume": 0.8,
            "music_volume": 0.4,
            "sfx_volume": 0.9,
        }
        payload[field] = value
        res = client.post("/api/audio/config", json=payload)
        assert res.status_code == 422

    def test_audio_test_endpoint(self, monkeypatch):
        monkeypatch.setattr(audio_manager, "test_sound", lambda kind: kind == "hit")
        monkeypatch.setattr(audio_manager, "get_status", lambda: {
            "enabled": True,
            "available": True,
        })
        res = client.post("/api/audio/test", json={"sound_type": "hit"})
        assert res.status_code == 200
        assert res.json()["success"] is True

    def test_rejects_unknown_audio_test(self):
        res = client.post("/api/audio/test", json={"sound_type": "explosion"})
        assert res.status_code == 422


# ============== Health ==============

class TestHealth:
    def test_health(self):
        res = client.get("/health")
        data = res.json()
        assert data['status'] == 'healthy'

    def test_dashboard(self):
        res = client.get("/")
        assert res.status_code == 200
        assert 'Hırsız Oyunu' in res.text

    def test_dashboard_route(self):
        res = client.get("/dashboard")
        assert res.status_code == 200
        assert 'Kontrol Paneli' in res.text

    def test_screen_route(self):
        res = client.get("/screen")
        assert res.status_code == 200
        assert 'Toplam Skor' in res.text
