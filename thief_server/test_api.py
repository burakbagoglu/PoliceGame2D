"""
Server API endpoint testleri - FastAPI TestClient ile
"""
import json
import time
import base64
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from main import app, audio_manager, score_manager, piezo_config, spawn_scheduler
from scene_manager import SceneManager, default_scene_document


@pytest.fixture(autouse=True)
def reset_state(monkeypatch, tmp_path):
    """Her test öncesi state'i ve hassas fotoğraf deposunu sıfırla."""
    import main
    from photo_auth import PhotoAccessGuard
    from photo_manager import PhotoSessionManager
    from runtime_state import RuntimeStateStore
    from client_settings import ClientSettingsStore

    def fake_capture(path):
        Image.new("RGB", (960, 540), (30, 100, 190)).save(path, "JPEG", quality=88)

    test_photo_manager = PhotoSessionManager(
        tmp_path / "photo_sessions", capture_backend=fake_capture
    )
    monkeypatch.setattr(main, "photo_manager", test_photo_manager)
    monkeypatch.setattr(
        main,
        "runtime_state_store",
        RuntimeStateStore(tmp_path / "runtime_state.json"),
    )
    monkeypatch.setattr(
        main,
        "client_settings",
        ClientSettingsStore(tmp_path / "client_settings.json"),
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(main.CONFIG), encoding="utf-8")
    monkeypatch.setattr(main, "CONFIG_PATH", str(config_path))
    monkeypatch.setattr(main, "photo_guard", PhotoAccessGuard(pin="123456"))
    client.cookies.clear()
    score_manager.reset()
    piezo_config.__init__(threshold=100, refractory_ms=200)
    with main.active_polling_lock:
        main.active_polling_screens.clear()
    if main.spawn_scheduler:
        main.spawn_scheduler.stop()
        main.spawn_scheduler = None
    main._show_result_scene(None)
    yield
    if main.spawn_scheduler:
        main.spawn_scheduler.stop()
        main.spawn_scheduler = None
    test_photo_manager.shutdown()


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
        assert data['target_score'] == 144
        assert data['per_screen_target'] == 18
        assert data['screen_count'] == 8
        assert len(data['screen_targets']) == 8
        assert data['child_count'] == 3

    def test_start_game_hard(self):
        res = client.post("/api/game/start", json={
            "child_count": 5,
            "screen_count": 8,
            "difficulty": "hard",
        })
        data = res.json()
        assert data['target_score'] == 312
        assert data['per_screen_target'] == 39

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
            "begin_countdown",
            lambda: calls.append(("countdown", None)),
        )
        monkeypatch.setattr(
            audio_manager,
            "end_game",
            lambda completed: calls.append(("end", completed)),
        )

        client.post("/api/game/start", json={"child_count": 3})
        deadline = time.time() + 0.5
        while not calls and time.time() < deadline:
            time.sleep(0.01)
        client.post("/api/game/end")

        assert calls == [("countdown", None), ("end", False)]


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
        assert data['phase'] == 'COUNTDOWN'
        assert data['countdown_active'] is True
        assert data['countdown_message'] == 'HIRSIZLARI VUR'

    def test_all_eight_screens_participate_even_if_request_says_five(self):
        client.get("/spawn/poll?screen_id=8")
        client.post("/api/game/start", json={"child_count": 2, "screen_count": 5})

        res = client.get("/spawn/poll?screen_id=8")
        assert res.status_code == 200
        assert res.json()['participating'] is True
        assert res.json()['screen_target'] == 12
        assert res.json()['screen_complete'] is False

    def test_screen_quota_switches_only_that_screen_to_jail(self):
        client.post("/api/game/start", json={"child_count": 1, "duration_minutes": 35, "difficulty": "easy"})
        for hit in range(12):
            response = client.post("/event", json={
                "event_id": f"screen-one-hit-{hit}",
                "screen_id": 1,
                "points": 1,
                "ts_ms": int(time.time() * 1000) + hit,
            })
            assert response.status_code == 200

        jailed = client.get("/spawn/poll?screen_id=1").json()
        playing = client.get("/spawn/poll?screen_id=2").json()
        assert jailed["active_scene"] == "jail"
        assert jailed["screen_score"] == jailed["screen_target"] == 12
        assert jailed["screen_complete"] is True
        assert jailed["spawn"] is False
        assert playing["active_scene"] != "jail"
        assert playing["screen_complete"] is False
        assert playing["game_active"] is True
    def test_manual_end_exposes_lose_scene(self):
        client.post("/api/game/start", json={"child_count": 3})
        client.post("/api/game/end")

        data = client.get("/spawn/poll?screen_id=1").json()
        assert data["game_active"] is False
        assert data["active_scene"] == "lose"

    @pytest.mark.parametrize("screen_id", [0, 9])
    def test_poll_rejects_invalid_screen_id(self, screen_id):
        res = client.get(f"/spawn/poll?screen_id={screen_id}")
        assert res.status_code == 422


# ============== Scene Editor ==============

class TestSceneEditorAPI:
    def test_editor_page_and_initial_state(self, monkeypatch, tmp_path):
        import main
        monkeypatch.setattr(main, "scene_manager", SceneManager(tmp_path))

        page = client.get("/scene-editor")
        state = client.get("/api/scenes/editor")

        assert page.status_code == 200
        assert "Sahne Editörü" in page.text
        assert 'id="timelineScrub"' in page.text
        assert 'id="prefabList"' in page.text
        assert 'id="performanceCard"' in page.text
        assert 'id="stageViewport"' in page.text
        assert 'id="handToolBtn"' in page.text
        assert 'id="zoomSelect"' in page.text
        assert 'id="fitViewBtn"' in page.text
        assert "finishMarqueeSelection" in page.text
        assert state.status_code == 200
        assert "waiting" in state.json()["draft"]["scenes"]

    def test_draft_publish_preview_and_client_payload(self, monkeypatch, tmp_path):
        import main
        monkeypatch.setattr(main, "scene_manager", SceneManager(tmp_path))
        document = default_scene_document()
        document["scenes"]["waiting"]["name"] = "API Taslağı"

        saved = client.put("/api/scenes/draft", json={"document": document})
        preview = client.post(
            "/api/scenes/preview",
            json={"screen_id": 2, "scene_id": "waiting"},
        )
        preview_payload = client.get(
            "/api/scenes/client?screen_id=2&known_version="
        ).json()
        published = client.post("/api/scenes/publish")
        normal_payload = client.get(
            "/api/scenes/client?screen_id=1&known_version="
        ).json()

        assert saved.status_code == 200
        assert preview.status_code == 200
        assert preview_payload["preview"] is True
        assert preview_payload["preview_scene"] == "waiting"
        assert published.status_code == 200
        assert normal_payload["document"]["scenes"]["waiting"]["name"] == "API Taslağı"

    def test_server_scene_rule_resolves_central_audio_scene(self):
        import main
        document = default_scene_document()
        document["scenes"]["urgent"] = {
            "name": "Acil", "background": "transparent", "duration": 2,
            "transition": {"type": "fade", "duration": 0.2},
            "audio_cues": [], "elements": [],
        }
        document["rules"] = [
            {"id": "urgent_rule", "scene_id": "urgent", "event": "time_lte", "value": 10, "priority": 100}
        ]

        resolved = main._resolve_server_rule_scene("gameplay", document, 8, True)

        assert resolved == "urgent"
    def test_asset_upload_and_download(self, monkeypatch, tmp_path):
        import main
        monkeypatch.setattr(main, "scene_manager", SceneManager(tmp_path))
        content = b"scene-asset"

        uploaded = client.post("/api/scenes/assets", json={
            "filename": "logo.png",
            "data_base64": base64.b64encode(content).decode("ascii"),
            "optimize": False,
        })
        downloaded = client.get("/api/scene-assets/logo.png")

        assert uploaded.status_code == 200
        assert uploaded.json()["name"] == "logo.png"
        assert downloaded.status_code == 200
        assert downloaded.content == content

    def test_draft_revision_conflict_returns_409(self, monkeypatch, tmp_path):
        import main
        monkeypatch.setattr(main, "scene_manager", SceneManager(tmp_path))
        document = default_scene_document()

        first = client.put("/api/scenes/draft", json={
            "document": document, "base_revision": 1,
        })
        stale = client.put("/api/scenes/draft", json={
            "document": document, "base_revision": 1,
        })

        assert first.status_code == 200
        assert stale.status_code == 409
        assert stale.json()["detail"]["server_revision"] == 2

    def test_client_heartbeat_status(self, monkeypatch):
        import main
        from client_telemetry import ClientTelemetryStore
        monkeypatch.setattr(main, "client_telemetry", ClientTelemetryStore())

        heartbeat = client.post("/api/clients/heartbeat", json={
            "screen_id": 2, "fps": 29.8, "memory_mb": 88,
            "serial_connected": True,
            "piezo": {"latest": 42, "peak": 300, "samples": [10, 42]},
        })
        status = client.get("/api/clients/status")

        assert heartbeat.status_code == 200
        assert status.json()["online_count"] == 1
        assert status.json()["clients"][1]["piezo"]["peak"] == 300
    def test_builtin_client_background_is_available(self):
        response = client.get("/api/scene-assets/__client_background__")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
        assert len(response.content) > 100

    def test_client_screenshot_request_upload_and_download(self, monkeypatch, tmp_path):
        import main
        monkeypatch.setattr(main, "scene_manager", SceneManager(tmp_path))

        requested = client.post(
            "/api/scenes/screenshot/request",
            json={"screen_id": 3},
        )
        token = requested.json()["request_token"]
        client_payload = client.get(
            "/api/scenes/client?screen_id=3&known_version=published-1"
        ).json()
        content = b"\x89PNG\r\n\x1a\napi-preview"
        uploaded = client.post(
            "/api/scenes/screenshot/upload",
            json={
                "screen_id": 3,
                "request_token": token,
                "data_base64": base64.b64encode(content).decode("ascii"),
            },
        )
        status = client.get("/api/scenes/screenshot/3/status")
        downloaded = client.get("/api/scenes/screenshot/3")

        assert requested.status_code == 200
        assert client_payload["screenshot_request"] == token
        assert uploaded.status_code == 200
        assert status.json()["status"] == "ready"
        assert downloaded.content == content
    def test_invalid_document_is_rejected(self, monkeypatch, tmp_path):
        import main
        monkeypatch.setattr(main, "scene_manager", SceneManager(tmp_path))
        document = default_scene_document()
        document["scenes"]["waiting"]["elements"][0]["height"] = 0

        response = client.put("/api/scenes/draft", json={"document": document})
        assert response.status_code == 422


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

    def test_rejects_score_outside_fixed_eight_screens(self):
        client.post("/api/game/start", json={"child_count": 2, "screen_count": 5})
        res = client.post("/event", json={
            "event_id": "outside-session-001",
            "screen_id": 9,
            "points": 1,
            "ts_ms": int(time.time() * 1000),
        })
        assert res.status_code == 422
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
        import main
        with open(main.CONFIG_PATH, encoding="utf-8") as handle:
            persisted = json.load(handle)
        assert persisted["default_piezo_threshold"] == 150
        assert persisted["default_piezo_refractory_ms"] == 300

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
        import main
        with open(main.CONFIG_PATH, encoding="utf-8") as handle:
            persisted = json.load(handle)
        assert persisted["audio"]["master_volume"] == 0.7

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

    def test_operator_route_is_touch_friendly_and_protected(self):
        res = client.get("/operator")
        assert res.status_code == 200
        assert "Kafe ekibi hızlı başlangıç ekranı" in res.text
        assert "OYUNU BAŞLAT" in res.text
        assert "capture_photos: true" in res.text
        assert "photo_consent: true" in res.text
        assert "X-Photo-CSRF" in res.text
        assert res.headers["cache-control"] == "private, no-store"
        assert res.headers["x-frame-options"] == "DENY"

        alias = client.get("/kafe")
        assert alias.status_code == 200

    def test_screen_route(self):
        res = client.get("/screen")
        assert res.status_code == 200
        assert 'Toplam Skor' in res.text

    def test_photo_gallery_route(self):
        res = client.get("/photos")
        assert res.status_code == 200
        assert 'Fotoğraf Satış & Baskı' in res.text
        assert res.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in res.headers["content-security-policy"]
        assert res.headers["cache-control"] == "private, no-store"


def test_game_profiles_can_start_a_session():
    profiles = client.get("/api/game/profiles")
    assert profiles.status_code == 200
    assert "standard" in profiles.json()["profiles"]

    started = client.post("/api/game/start", json={"profile_id": "short"})
    assert started.status_code == 200
    assert started.json()["child_count"] == 2
    assert started.json()["screen_count"] == 8
    assert started.json()["game_duration_minutes"] == 30


def test_active_game_recovers_from_runtime_checkpoint():
    import main

    started = client.post(
        "/api/game/start",
        json={"child_count": 1, "duration_minutes": 1, "difficulty": "easy"},
    )
    assert started.status_code == 200
    event = {
        "event_id": "power-loss-event",
        "screen_id": 1,
        "points": 1,
        "ts_ms": int(time.time() * 1000),
    }
    scored = client.post("/event", json=event)
    assert scored.status_code == 200
    assert main.runtime_state_store.load()["session"]["is_active"] is True

    main.spawn_scheduler.stop()
    main.spawn_scheduler = None
    score_manager.reset()

    assert main._restore_runtime_checkpoint() is True
    assert main.spawn_scheduler.session.is_active is True
    assert main.spawn_scheduler.session.get_screen_score(1) == 1

    duplicate = client.post("/event", json=event)
    assert duplicate.status_code == 200
    assert duplicate.json()["is_new"] is False


def test_unknown_game_profile_is_rejected():
    response = client.post("/api/game/start", json={"profile_id": "missing"})
    assert response.status_code == 404


def test_scene_diff_endpoint_returns_publish_summary():
    response = client.get("/api/scenes/diff")
    assert response.status_code == 200
    assert {"has_changes", "changed_total", "draft_revision", "published_version"} <= set(response.json())

def test_combined_client_poll(monkeypatch):
    import main
    from client_telemetry import ClientTelemetryStore

    monkeypatch.setattr(main, "client_telemetry", ClientTelemetryStore())
    response = client.post("/api/client/poll", json={
        "screen_id": 1,
        "telemetry": {
            "fps": 29.7,
            "frame_time_p95_ms": 35.1,
            "performance_profile": "pi_zero_2w",
            "quality_level": "low",
            "render_width": 1280,
            "render_height": 720,
        },
    })

    assert response.status_code == 200
    payload = response.json()
    assert "spawn_state" in payload
    assert payload["piezo_config"]["changed"] is True
    assert payload["heartbeat"]["frame_time_p95_ms"] == 35.1

    second = client.post("/api/client/poll", json={"screen_id": 1})
    assert second.status_code == 200
    assert second.json()["piezo_config"]["changed"] is False

def test_online_client_can_receive_restart_command(monkeypatch):
    import main
    from client_commands import ClientCommandStore
    from client_telemetry import ClientTelemetryStore

    telemetry = ClientTelemetryStore()
    telemetry.update(3, {
        "fps": 30,
        "serial_connected": True,
        "app_version": "scene-engine-v9-remote-config",
    })
    monkeypatch.setattr(main, "client_telemetry", telemetry)
    monkeypatch.setattr(main, "client_commands", ClientCommandStore())

    queued = client.post("/api/clients/3/restart")
    assert queued.status_code == 200

    poll = client.post("/api/client/poll", json={"screen_id": 3})
    assert poll.status_code == 200
    assert poll.json()["command"]["type"] == "restart"

    next_poll = client.post("/api/client/poll", json={"screen_id": 3})
    assert next_poll.json()["command"] is None


def test_offline_client_restart_is_rejected(monkeypatch):
    import main
    from client_telemetry import ClientTelemetryStore

    monkeypatch.setattr(main, "client_telemetry", ClientTelemetryStore())

    response = client.post("/api/clients/2/restart")

    assert response.status_code == 409


def test_field_check_combines_clients_audio_and_camera(monkeypatch):
    import main
    from client_telemetry import ClientTelemetryStore

    telemetry = ClientTelemetryStore()
    for screen_id in range(1, 9):
        telemetry.update(screen_id, {
            "fps": 30,
            "serial_connected": True,
            "app_version": "scene-engine-v9-remote-config",
            "cpu_temp_c": 55,
        })
    monkeypatch.setattr(main, "client_telemetry", telemetry)
    monkeypatch.setattr(main.audio_manager, "get_status", lambda: {
        "enabled": True,
        "available": True,
        "device_active": "Headphones",
        "last_error": None,
    })
    monkeypatch.setattr(main.photo_manager, "camera_status", lambda: {
        "enabled": True,
        "available": True,
        "device": "/dev/video0",
        "last_error": None,
    })

    response = client.get("/api/field-check")

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["online_count"] == 8


def _photo_login():
    response = client.post("/api/photo-auth/login", json={"pin": "123456"})
    assert response.status_code == 200
    return response.json()["csrf"]


def test_client_update_requires_operator_login_and_queues_allowlisted_command(monkeypatch):
    import main
    from client_commands import ClientCommandStore
    from client_telemetry import ClientTelemetryStore

    telemetry = ClientTelemetryStore()
    telemetry.update(3, {
        "fps": 30,
        "serial_connected": True,
        "app_version": "scene-engine-v9-remote-config",
        "update_state": "idle",
    })
    monkeypatch.setattr(main, "client_telemetry", telemetry)
    monkeypatch.setattr(main, "client_commands", ClientCommandStore())
    client.cookies.clear()

    assert client.post("/api/clients/3/update").status_code == 401
    csrf = _photo_login()
    queued = client.post(
        "/api/clients/3/update",
        headers={"X-Photo-CSRF": csrf},
    )
    assert queued.status_code == 200

    poll = client.post("/api/client/poll", json={"screen_id": 3})
    assert poll.status_code == 200
    assert poll.json()["command"]["type"] == "update"


def test_photo_gallery_requires_operator_login():
    assert client.get("/api/photo-sessions").status_code == 401
    assert client.post("/api/photo-auth/login", json={"pin": "000000"}).status_code == 401

    csrf = _photo_login()

    assert len(csrf) >= 20
    assert client.get("/api/photo-sessions").status_code == 200
    assert client.get("/api/camera/status").status_code == 200


def test_photo_storage_requires_login_and_supports_dry_run():
    assert client.get("/api/photo-storage").status_code == 401
    csrf = _photo_login()

    status = client.get("/api/photo-storage")
    assert status.status_code == 200
    assert "used_mb" in status.json()

    cleanup = client.post(
        "/api/photo-storage/cleanup",
        headers={"X-Photo-CSRF": csrf},
        json={"dry_run": True},
    )
    assert cleanup.status_code == 200
    assert cleanup.json()["dry_run"] is True


def test_photo_capture_requires_consent():
    response = client.post("/api/game/start", json={
        "child_count": 2,
        "session_name": "İzinsiz çekim",
        "capture_photos": True,
        "photo_consent": False,
    })

    assert response.status_code == 400
    assert "onay" in response.json()["detail"]


def test_authorization_is_required_to_start_photo_capture():
    response = client.post("/api/game/start", json={
        "child_count": 2,
        "session_name": "Yetkisiz çekim",
        "capture_photos": True,
        "photo_consent": True,
    })

    assert response.status_code == 401


def test_screen_completion_creates_protected_session_photo():
    import main

    csrf = _photo_login()
    started = client.post("/api/game/start", headers={"X-Photo-CSRF": csrf}, json={
        "child_count": 1,
        "duration_minutes": 35,
        "difficulty": "easy",
        "session_name": "Grup Mavi",
        "capture_photos": True,
        "photo_consent": True,
    })
    assert started.status_code == 200
    session_id = started.json()["photo_session"]["id"]
    screen_target = started.json()["screen_targets"]["1"]

    for index in range(screen_target):
        hit = client.post("/event", json={
            "event_id": f"photo-hit-{index}",
            "screen_id": 1,
            "points": 1,
            "ts_ms": int(time.time() * 1000) + index,
        })
        assert hit.status_code == 200

    deadline = time.time() + 3
    session = main.photo_manager.get_session(session_id)
    while not session["photos"] and time.time() < deadline:
        time.sleep(0.02)
        session = main.photo_manager.get_session(session_id)

    assert len(session["photos"]) == 1
    photo = session["photos"][0]
    assert photo["screen_id"] == 1
    assert client.get(photo["url"]).status_code == 200
    assert client.patch(
        f"/api/photo-sessions/{session_id}",
        json={"sold": True, "sale_price": 300, "customer_name": "Aile Mavi"},
    ).status_code == 403

    updated = client.patch(
        f"/api/photo-sessions/{session_id}",
        headers={"X-Photo-CSRF": csrf},
        json={"sold": True, "sale_price": 300, "customer_name": "Aile Mavi"},
    )
    assert updated.status_code == 200
    assert updated.json()["sold"] is True
    archive = client.get(f"/api/photo-sessions/{session_id}/download")
    assert archive.status_code == 200
    assert archive.content.startswith(b"PK")

    client.post("/api/game/end")
    deleted = client.delete(
        f"/api/photo-sessions/{session_id}", headers={"X-Photo-CSRF": csrf}
    )
    assert deleted.status_code == 200


class TestClientSettingsAPI:
    def test_settings_are_saved_and_delivered_until_client_applies_revision(self):
        payload = {
            "screen_ids": [3],
            "settings": {
                "fps": 30,
                "performance_profile": "pi_zero_2w",
                "render_width": 1200,
                "render_height": 675,
                "adaptive_quality": True,
                "min_fps": 23,
                "playarea": {
                    "enabled": True,
                    "mode": "manual_px",
                    "x": 10,
                    "y": 20,
                    "width": 1100,
                    "height": 620,
                },
            },
        }
        saved = client.post("/api/clients/settings", json=payload)
        assert saved.status_code == 200
        assert saved.json()["records"]["3"]["revision"] == 1

        pending = client.post("/api/client/poll", json={
            "screen_id": 3,
            "settings_revision": 0,
        })
        assert pending.status_code == 200
        assert pending.json()["client_settings"]["changed"] is True
        assert pending.json()["client_settings"]["settings"]["render_width"] == 1200

        applied = client.post("/api/client/poll", json={
            "screen_id": 3,
            "settings_revision": 1,
        })
        assert applied.json()["client_settings"] == {
            "changed": False,
            "revision": 1,
        }

    def test_invalid_client_settings_are_rejected(self):
        response = client.post("/api/clients/settings", json={
            "screen_ids": [1],
            "settings": {"fps": 200},
        })
        assert response.status_code == 422
