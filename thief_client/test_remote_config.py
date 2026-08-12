import json

import pytest

from lib.net_client import NetClient
from lib.remote_config import apply_remote_settings, validate_remote_settings


def _settings():
    return {
        "fps": 30,
        "performance_profile": "pi_zero_2w",
        "render_width": 1200,
        "render_height": 675,
        "adaptive_quality": True,
        "min_fps": 23,
        "playarea": {
            "enabled": True,
            "mode": "manual_px",
            "x": 20,
            "y": 30,
            "width": 1100,
            "height": 620,
        },
    }


def test_remote_settings_are_applied_atomically_and_preserve_identity(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "screen_id": 6,
        "server_url": "http://server.local:8078/event",
        "serial_port": "/dev/ttyUSB0",
        "playarea": {"screen_diagonal_in": 27},
    }), encoding="utf-8")

    assert apply_remote_settings(path, {
        "changed": True,
        "revision": 4,
        "settings": _settings(),
    }) is True

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["screen_id"] == 6
    assert saved["server_url"] == "http://server.local:8078/event"
    assert saved["serial_port"] == "/dev/ttyUSB0"
    assert saved["fps"] == 30
    assert saved["render_width"] == 1200
    assert saved["playarea"]["x"] == 20
    assert saved["remote_settings_revision"] == 4
    assert apply_remote_settings(path, {
        "changed": True,
        "revision": 4,
        "settings": _settings(),
    }) is False


def test_remote_settings_reject_non_allowlisted_or_invalid_values():
    settings = _settings()
    settings["fps"] = 200
    with pytest.raises(ValueError):
        validate_remote_settings(settings)


def test_net_client_keeps_only_latest_settings_payload():
    client = NetClient(
        server_url="http://test:8000/event",
        server_base_url="http://test:8000",
        screen_id=2,
        settings_revision=3,
    )
    client._apply_remote_config_payload({
        "changed": True,
        "revision": 4,
        "settings": _settings(),
    })
    client._apply_remote_config_payload({
        "changed": True,
        "revision": 5,
        "settings": {**_settings(), "fps": 28},
    })

    payload = client.consume_remote_config()
    assert payload["revision"] == 5
    assert payload["settings"]["fps"] == 28
