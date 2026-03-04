"""
Net Client testleri - NetClient polling ve gönderim testleri
responses kütüphanesi ile HTTP mock
"""
import time
import json
import os
import pytest

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

        # Temizle
        if os.path.exists(queue_file):
            os.remove(queue_file)


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
