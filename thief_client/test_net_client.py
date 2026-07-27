"""
Net Client testleri - NetClient polling ve gönderim testleri
responses kütüphanesi ile HTTP mock
"""
import time
import json
import os
import pytest
import threading
import hashlib
import base64

try:
    import responses
    RESPONSES_AVAILABLE = True
except ImportError:
    RESPONSES_AVAILABLE = False

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

from lib.net_client import NetClient, ScoreEvent


# ============== ScoreEvent ==============

class TestScoreEvent:
    def test_create(self):
        event = ScoreEvent.create(screen_id=1, points=1)
        assert event.screen_id == 1
        assert event.points == 1
        assert len(event.event_id) > 0

    def test_to_dict(self):
        event = ScoreEvent.create(screen_id=2, points=3)
        d = event.to_dict()
        assert d['screen_id'] == 2
        assert d['points'] == 3
        assert 'event_id' in d
        assert 'ts_ms' in d


# ============== NetClient Unit Tests ==============

class TestNetClientUnit:
    def test_get_spawn_empty(self):
        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
        )
        assert client.get_spawn() is False

    def test_get_spawn_with_data(self):
        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
        )
        client.spawn_queue.put({"spawn": True})
        assert client.get_spawn() is True
        assert client.get_spawn() is False  # Consumed

    def test_get_piezo_config_empty(self):
        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
        )
        assert client.get_piezo_config() is None

    def test_get_piezo_config_with_data(self):
        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
        )
        config = {"threshold": 150, "refractory_ms": 300}
        client.piezo_config_queue.put(config)
        result = client.get_piezo_config()
        assert result == config

    def test_consume_score_reset(self):
        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
        )
        assert client.consume_score_reset() is False
        client.score_reset_queue.put(2)
        assert client.consume_score_reset() is True
        assert client.consume_score_reset() is False

    def test_send_score_queues(self):
        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
        )
        client.send_score(1)
        assert client.send_queue.qsize() == 1

    def test_get_status(self):
        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
        )
        status = client.get_status()
        assert 'connected' in status
        assert 'events_sent' in status
        assert 'spawns_received' in status

    def test_offline_queue_save_load(self):
        queue_file = "/tmp/test_offline_queue.json"
        # Temizle
        if os.path.exists(queue_file):
            os.remove(queue_file)

        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
            queue_file=queue_file,
        )
        client.send_score(1)
        client.send_score(2)
        client._save_offline_queue()

        assert os.path.exists(queue_file)

        # Yeni client yüklesin
        client2 = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
            queue_file=queue_file,
        )
        assert client2.send_queue.qsize() == 2
        # Dosya, eventler başarıyla gönderilene kadar silinmemeli.
        assert os.path.exists(queue_file)

        # Temizle
        if os.path.exists(queue_file):
            os.remove(queue_file)

    def test_offline_queue_merge_does_not_lose_failed_events(self, tmp_path):
        queue_file = str(tmp_path / "event_queue.json")
        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
            queue_file=queue_file,
        )
        failed_event = ScoreEvent.create(1, 1)
        client._add_to_offline_queue(failed_event)
        client.send_score(2)
        client._save_offline_queue()

        with open(queue_file, "r", encoding="utf-8") as f:
            events = json.load(f)
        event_ids = {event["event_id"] for event in events}
        assert len(event_ids) == 2
        assert failed_event.event_id in event_ids
        assert sorted(event["points"] for event in events) == [1, 2]

    def test_successful_retry_removes_persisted_event(self, tmp_path, monkeypatch):
        queue_file = str(tmp_path / "event_queue.json")
        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
            queue_file=queue_file,
        )
        event = ScoreEvent.create(1, 1)
        client._add_to_offline_queue(event)
        client.send_queue.put(event)
        monkeypatch.setattr(client, "_send_event", lambda _event: True)

        client.running = True
        client._stop_event.clear()
        worker = threading.Thread(target=client._send_loop)
        worker.start()

        deadline = time.time() + 2
        while client.events_sent == 0 and time.time() < deadline:
            time.sleep(0.01)
        client.running = False
        client._stop_event.set()
        worker.join(timeout=2)

        assert client.events_sent == 1
        assert not os.path.exists(queue_file)

    def test_failed_event_is_retried_during_runtime(self, tmp_path, monkeypatch):
        queue_file = str(tmp_path / "event_queue.json")
        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
            queue_file=queue_file,
        )
        attempts = []

        def send_with_recovery(event):
            attempts.append(event.event_id)
            return len(attempts) >= 2

        monkeypatch.setattr(client, "_send_event", send_with_recovery)
        client.send_score(1)
        client.running = True
        client._stop_event.clear()
        worker = threading.Thread(target=client._send_loop)
        worker.start()

        deadline = time.time() + 3
        while client.events_sent == 0 and time.time() < deadline:
            time.sleep(0.01)
        client.running = False
        client._stop_event.set()
        worker.join(timeout=2)

        assert len(attempts) >= 2
        assert len(set(attempts)) == 1
        assert client.events_sent == 1
        assert not os.path.exists(queue_file)


# ============== Polling with Mocked HTTP ==============

@pytest.mark.skipif(not RESPONSES_AVAILABLE, reason="responses kütüphanesi yüklü değil")
class TestNetClientPolling:
    @responses.activate
    def test_poll_spawn_success(self):
        responses.get(
            "http://test:8000/spawn/poll",
            json={"spawn": True, "game_active": True},
            status=200,
        )

        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
        )
        client._poll_spawn()
        assert client.get_spawn() is True

    @responses.activate
    def test_poll_spawn_no_spawn(self):
        responses.get(
            "http://test:8000/spawn/poll",
            json={"spawn": False, "game_active": True},
            status=200,
        )

        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
        )
        client._poll_spawn()
        assert client.get_spawn() is False

    @responses.activate
    @responses.activate
    def test_poll_spawn_jail_state_clears_pending_spawn(self):
        responses.get(
            "http://test:8000/spawn/poll",
            json={
                "spawn": True,
                "game_active": True,
                "active_scene": "jail",
                "screen_score": 12,
                "screen_target": 12,
                "screen_remaining": 0,
                "screen_complete": True,
            },
            status=200,
        )
        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
        )
        client.spawn_queue.put({"spawn": True})
        client._poll_spawn()

        assert client.server_scene == "jail"
        assert client.server_screen_score == 12
        assert client.server_screen_target == 12
        assert client.server_screen_complete is True
        assert client.get_spawn() is False

    @responses.activate
    def test_poll_spawn_updates_countdown_status(self):
        responses.get(
            "http://test:8000/spawn/poll",
            json={
                "spawn": False,
                "game_active": True,
                "countdown_active": True,
                "countdown_message": "3",
                "countdown_remaining_ms": 2750,
            },
            status=200,
        )

        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
        )
        client._poll_spawn()

        assert client.get_countdown_status() == {
            "active": True,
            "message": "3",
            "remaining_ms": 2750,
        }

    @responses.activate
    def test_poll_scene_config_queues_new_document(self, tmp_path):
        responses.get(
            "http://test:8000/api/scenes/client",
            json={
                "changed": True,
                "version": "published-2",
                "preview": False,
                "preview_scene": None,
                "document": {
                    "canvas": {"width": 1920, "height": 1080},
                    "scenes": {"waiting": {"elements": []}},
                },
                "assets": [],
            },
            status=200,
        )
        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
            scene_cache_dir=str(tmp_path),
        )

        client._poll_scene_config()
        payload = client.consume_scene_config()

        assert payload["version"] == "published-2"
        assert payload["preview"] is False
        assert client.scene_version == "published-2"

    @responses.activate
    def test_poll_scene_config_downloads_checksum_asset(self, tmp_path):
        content = b"fake-image-bytes"
        digest = hashlib.sha256(content).hexdigest()
        responses.get(
            "http://test:8000/api/scenes/client",
            json={
                "changed": True,
                "version": "draft-4-win",
                "preview": True,
                "preview_scene": "win",
                "document": {
                    "canvas": {"width": 1920, "height": 1080},
                    "scenes": {"win": {"elements": []}},
                },
                "assets": [{
                    "name": "hero.png",
                    "sha256": digest,
                    "url": "/api/scene-assets/hero.png",
                }],
            },
            status=200,
        )
        responses.get(
            "http://test:8000/api/scene-assets/hero.png",
            body=content,
            status=200,
        )
        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=4,
            scene_cache_dir=str(tmp_path),
        )

        client._poll_scene_config()
        payload = client.consume_scene_config()

        assert payload["preview_scene"] == "win"
        assert open(payload["asset_paths"]["hero.png"], "rb").read() == content

    @responses.activate
    def test_poll_scene_config_queues_screenshot_request_when_unchanged(self, tmp_path):
        responses.get(
            "http://test:8000/api/scenes/client",
            json={
                "changed": False,
                "version": "published-1",
                "preview": False,
                "preview_scene": None,
                "screenshot_request": "request-token-123",
            },
            status=200,
        )
        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=2,
            scene_cache_dir=str(tmp_path),
        )

        client._poll_scene_config()

        assert client.consume_scene_screenshot_request() == "request-token-123"
        assert client.consume_scene_config() is None

    @responses.activate
    def test_upload_scene_screenshot_uses_network_queue(self, tmp_path):
        responses.post(
            "http://test:8000/api/scenes/screenshot/upload",
            json={"success": True},
            status=200,
        )
        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=2,
            scene_cache_dir=str(tmp_path),
        )
        content = b"\x89PNG\r\n\x1a\nclient-preview"

        assert client.submit_scene_screenshot("request-token-123", content) is True
        client._upload_scene_screenshot()

        body = json.loads(responses.calls[0].request.body)
        assert body["screen_id"] == 2
        assert body["request_token"] == "request-token-123"
        assert base64.b64decode(body["data_base64"]) == content
    @responses.activate
    def test_poll_spawn_score_version_change_queues_reset(self):
        responses.get(
            "http://test:8000/spawn/poll",
            json={"spawn": False, "game_active": True, "score_version": 1},
            status=200,
        )
        responses.get(
            "http://test:8000/spawn/poll",
            json={"spawn": False, "game_active": True, "score_version": 2},
            status=200,
        )

        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
        )
        client._poll_spawn()
        assert client.consume_score_reset() is False
        client._poll_spawn()
        assert client.consume_score_reset() is True

    @responses.activate
    def test_poll_piezo_config_changed(self):
        responses.get(
            "http://test:8000/api/piezo/config/poll",
            json={"changed": True, "threshold": 200, "refractory_ms": 400},
            status=200,
        )

        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
        )
        client._poll_piezo_config()
        config = client.get_piezo_config()
        assert config is not None
        assert config['threshold'] == 200

    @responses.activate
    def test_poll_piezo_config_no_change(self):
        responses.get(
            "http://test:8000/api/piezo/config/poll",
            json={"changed": False},
            status=200,
        )

        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
        )
        client._poll_piezo_config()
        assert client.get_piezo_config() is None

    @responses.activate
    def test_send_event_success(self):
        responses.post(
            "http://test:8000/event",
            json={"success": True, "is_new": True, "total_score": 1},
            status=200,
        )

        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=1,
        )
        event = ScoreEvent.create(1, 1)
        result = client._send_event(event)
        assert result is True
    @responses.activate
    def test_send_heartbeat_merges_provider_payload(self):
        responses.post(
            "http://test:8000/api/clients/heartbeat",
            json={"success": True},
            status=200,
        )
        client = NetClient(
            server_url="http://test:8000/event",
            server_base_url="http://test:8000",
            screen_id=3,
            telemetry_provider=lambda: {
                "fps": 29.5,
                "serial_connected": True,
                "piezo": {"latest": 100},
            },
        )

        client._send_heartbeat()

        payload = json.loads(responses.calls[0].request.body)
        assert payload["screen_id"] == 3
        assert payload["fps"] == 29.5
        assert payload["piezo"]["latest"] == 100
